#!/usr/bin/env python3
"""
Pull open roles from public company job boards. No API key, no login, no scraping.

These are the same JSON endpoints the companies' own careers pages call, so
this is documented public data — not something that gets an account banned.

    python3 scripts/fetch_jobs.py

Reads  targets/companies.txt
Writes db/jobs.jsonl      (every job ever seen, append-only)
       db/jobs_new.jsonl  (only this run's new jobs — what scoring reads next)
"""

import gzip
import html
import json
import re
import sys
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "targets" / "companies.txt"
DB = ROOT / "db"
ALL_JOBS = DB / "jobs.jsonl"
NEW_JOBS = DB / "jobs_new.jsonl"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) job-agent/1.0"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\n{3,}")


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def clean(text):
    """HTML -> readable plain text, so the JD is cheap to feed to a model."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    return WS_RE.sub("\n\n", "\n".join(lines)).strip()


# --- one adapter per board provider -----------------------------------------


def from_greenhouse(slug):
    # No content=true: descriptions are fetched per job later, and only for the
    # tiny fraction that survive the prefilter. Asking for them here would store
    # ~6 KB of JD per row across thousands of rows we will never look at.
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    for job in get_json(url).get("jobs", []):
        job_id = str(job.get("id"))
        yield {
            "source": "greenhouse",
            "source_id": job_id,
            "title": job.get("title", ""),
            "location": (job.get("location") or {}).get("name", ""),
            "url": job.get("absolute_url", ""),
            "apply_url": job.get("absolute_url", ""),
            "posted_at": job.get("updated_at", ""),
            "description": "",
            "detail_url": (
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
            ),
        }


def from_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    for job in get_json(url):
        cats = job.get("categories") or {}
        job_id = str(job.get("id"))
        yield {
            "source": "lever",
            "source_id": job_id,
            "title": job.get("text", ""),
            "location": cats.get("location", ""),
            "url": job.get("hostedUrl", ""),
            "apply_url": job.get("applyUrl") or job.get("hostedUrl", ""),
            "posted_at": job.get("createdAt", ""),
            "description": "",
            "detail_url": f"https://api.lever.co/v0/postings/{slug}/{job_id}",
        }


def from_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    for job in get_json(url).get("jobs", []):
        yield {
            "source": "ashby",
            "source_id": str(job.get("id")),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "url": job.get("jobUrl", ""),
            "apply_url": job.get("applyUrl") or job.get("jobUrl", ""),
            "posted_at": job.get("publishedAt", ""),
            # Ashby's job pages are client-rendered, so there is nothing to fetch
            # later — but its board API already includes the full description in
            # this same call. Keep it inline; it costs no extra request.
            "description": clean(job.get("descriptionPlain") or job.get("descriptionHtml", "")),
            "detail_url": job.get("jobUrl", ""),
        }


def from_workable(slug):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    for job in get_json(url).get("jobs", []):
        loc = job.get("location") or {}
        city = loc.get("city") or ""
        country = loc.get("country") or ""
        yield {
            "source": "workable",
            "source_id": str(job.get("shortcode") or job.get("id")),
            "title": job.get("title", ""),
            "location": ", ".join(p for p in (city, country) if p),
            "url": job.get("url", ""),
            "apply_url": job.get("application_url") or job.get("url", ""),
            "posted_at": job.get("published_on", ""),
            "description": "",
            "detail_url": job.get("application_url") or job.get("url", ""),
        }


def from_recruitee(slug):
    url = f"https://{slug}.recruitee.com/api/offers/"
    for job in get_json(url).get("offers", []):
        yield {
            "source": "recruitee",
            "source_id": str(job.get("id")),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "url": job.get("careers_url", ""),
            "apply_url": job.get("careers_apply_url") or job.get("careers_url", ""),
            "posted_at": job.get("published_at", ""),
            "description": "",
            "detail_url": job.get("careers_url", ""),
        }


def from_smartrecruiters(slug):
    """SmartRecruiters splits list and description across two endpoints.

    The list call is cheap and already carries title + location, so we only pay
    for a description fetch on roles that survive the title/geo prefilter.
    Descriptions for the rest are filled in later, on demand.
    """
    postings, offset = [], 0
    while True:
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{slug}"
            f"/postings?limit=100&offset={offset}"
        )
        page = get_json(url)
        batch = page.get("content", [])
        postings.extend(batch)
        offset += len(batch)
        if not batch or offset >= page.get("totalFound", 0) or offset >= 600:
            break

    for job in postings:
        loc = job.get("location") or {}
        parts = [loc.get("city"), loc.get("region"), loc.get("country")]
        if loc.get("remote"):
            parts.append("Remote")
        job_id = str(job.get("id") or job.get("uuid"))
        yield {
            "source": "smartrecruiters",
            "source_id": job_id,
            "title": job.get("name", ""),
            "location": ", ".join(p for p in parts if p),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
            "apply_url": f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
            "posted_at": job.get("releasedDate", ""),
            "description": "",  # fetched on demand — see fetch_description()
            "detail_url": (
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
            ),
        }


# Several Indian careers sites reject a bare urllib User-Agent outright — Nykaa,
# Purplle and Angel One all returned nothing until the request looked like a real
# browser. Sending the full set costs nothing and roughly doubled our coverage.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}


def get_html(url, timeout=30):
    """Fetch a URL and return raw HTML, plus the URL actually landed on.

    The final URL matters when a careers page redirects to a hosted board — that
    redirect target is often exactly the listings URL we're looking for.
    Returns ("", url) on any failure.
    """
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            landed = resp.geturl()
    except Exception:  # noqa: BLE001 — an unreachable page is data, not an error
        return "", url

    # We ask for compression, so we have to be able to undo it.
    try:
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        pass  # some servers mislabel the encoding; try the bytes as-is

    return raw.decode("utf-8", errors="replace"), landed


def get_text(url, timeout=30):
    """Fetch a URL and return its readable text. Empty string on any failure."""
    raw, _ = get_html(url, timeout=timeout)
    if not raw:
        return ""
    # Drop script/style bodies before stripping tags, or we get minified JS back.
    raw = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    return clean(raw)


def fetch_description(job):
    """Fill in a job's description when the board deferred it.

    Handles both the SmartRecruiters JSON detail endpoint and a plain HTML job
    page, so careers-page jobs and ATS jobs go through the same path.
    Returns "" if unavailable. Safe to call on jobs that already have one.
    """
    if job.get("description"):
        return job["description"]
    detail = job.get("detail_url")
    if not detail:
        return ""

    source = job.get("source", "")

    if source == "greenhouse":
        try:
            return clean((get_json(detail) or {}).get("content", ""))
        except Exception:  # noqa: BLE001
            return get_text(job.get("url", ""))

    if source == "lever":
        try:
            data = get_json(detail) or {}
        except Exception:  # noqa: BLE001
            return get_text(job.get("url", ""))
        return clean(data.get("descriptionPlain") or data.get("description", ""))

    if "api.smartrecruiters.com" not in detail:
        return get_text(detail)

    try:
        data = get_json(detail)
    except Exception:  # noqa: BLE001 — a missing JD must not break the pipeline
        return ""
    sections = (data.get("jobAd") or {}).get("sections") or {}
    order = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    chunks = []
    for name in order:
        section = sections.get(name) or {}
        text = clean(section.get("text", ""))
        if text:
            title = section.get("title") or name
            chunks.append(f"{title}\n{text}")
    return "\n\n".join(chunks)


ADAPTERS = {
    "greenhouse": from_greenhouse,
    "lever": from_lever,
    "ashby": from_ashby,
    "workable": from_workable,
    "recruitee": from_recruitee,
    "smartrecruiters": from_smartrecruiters,
}


# --- driver ------------------------------------------------------------------


def read_targets():
    if not COMPANIES.exists():
        sys.exit(f"missing {COMPANIES} — add lines like 'greenhouse  stripe'")
    targets = []
    for lineno, raw in enumerate(COMPANIES.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            print(f"  skipping {COMPANIES.name}:{lineno} — expected 'provider slug'")
            continue
        provider, slug = parts[0].lower(), parts[1]
        label = " ".join(parts[2:]) or slug
        if provider not in ADAPTERS:
            print(f"  skipping {COMPANIES.name}:{lineno} — unknown provider {provider!r}")
            continue
        targets.append((provider, slug, label))
    return targets


def load_seen():
    """Keys of jobs already fetched on an earlier run."""
    if not ALL_JOBS.exists():
        return set()
    seen = set()
    for line in ALL_JOBS.read_text().splitlines():
        if line.strip():
            try:
                seen.add(json.loads(line)["key"])
            except (json.JSONDecodeError, KeyError):
                continue
    return seen


def main():
    DB.mkdir(parents=True, exist_ok=True)
    targets = read_targets()
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat()

    fresh, total, failed = [], 0, []
    print(f"fetching {len(targets)} boards ({len(seen)} jobs already known)\n")

    for provider, slug, label in targets:
        try:
            jobs = list(ADAPTERS[provider](slug))
        except urllib.error.HTTPError as e:
            failed.append(f"{label} [{provider}/{slug}] HTTP {e.code}")
            print(f"  {label:<28} FAILED  HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001 — one bad board must not kill the run
            failed.append(f"{label} [{provider}/{slug}] {type(e).__name__}: {e}")
            print(f"  {label:<28} FAILED  {type(e).__name__}")
            continue

        new_here = 0
        for job in jobs:
            total += 1
            job["company"] = label
            job["key"] = f"{provider}:{slug}:{job['source_id']}"
            job["fetched_at"] = now
            if job["key"] in seen:
                continue
            seen.add(job["key"])
            fresh.append(job)
            new_here += 1
        print(f"  {label:<28} {len(jobs):>4} open, {new_here:>3} new")

    with ALL_JOBS.open("a") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")
    with NEW_JOBS.open("w") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")

    print(f"\n{total} roles seen across {len(targets)} boards")
    print(f"{len(fresh)} new -> {NEW_JOBS.relative_to(ROOT)}")
    if failed:
        print(f"\n{len(failed)} board(s) failed — usually a wrong slug:")
        for f in failed:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
