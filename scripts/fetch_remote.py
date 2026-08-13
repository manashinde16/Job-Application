#!/usr/bin/env python3
"""
Pull remote design roles from the public remote-job aggregators.

Opening the search to remote-anywhere is pointless without sources that carry
remote-anywhere jobs. Company ATS boards mostly list roles tied to an office; the
aggregators below exist specifically to list remote ones, all have free public
endpoints, and none needs a key.

    python3 scripts/fetch_remote.py
    python3 scripts/fetch_remote.py --limit 50

Reads  nothing — the sources are fixed
Writes db/jobs.jsonl and db/jobs_new.jsonl, same shape as the other channels

Descriptions arrive inline here, so no per-job follow-up fetch is needed.
"""

import argparse
import gzip
import html as html_mod
import json
import re
import sys
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_jobs import BROWSER_HEADERS, DB, clean, load_seen  # noqa: E402

ALL_JOBS = DB / "jobs.jsonl"
NEW_JOBS = DB / "jobs_new.jsonl"

# Only design roles are wanted; the aggregators are large and mostly engineering.
DESIGN_RE = re.compile(
    r"\b(product design|ux|ui|ui/ux|user experience|user interface|visual design|"
    r"graphic design|brand design|interaction design|design system|designer|design)\b",
    re.I,
)


def get(url, timeout=40, as_json=True):
    """Fetch and decode. BROWSER_HEADERS asks for gzip, so it must be undone —
    skipping that step is why every JSON source failed to parse."""
    req = urllib.request.Request(url, headers={
        **BROWSER_HEADERS,
        "Accept": "application/json, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").lower()

    try:
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        pass  # mislabelled encoding; try the bytes as they came

    text = raw.decode("utf-8", errors="replace")
    return json.loads(text) if as_json else text


def from_remoteok():
    """remoteok.com/api — one array, first element is legal boilerplate."""
    data = get("https://remoteok.com/api")
    for job in data[1:] if isinstance(data, list) else []:
        title = job.get("position") or ""
        tags = " ".join(job.get("tags") or [])
        if not DESIGN_RE.search(f"{title} {tags}"):
            continue
        yield {
            "source": "remoteok",
            "source_id": str(job.get("id") or job.get("slug")),
            "company": job.get("company") or "unknown",
            "title": title,
            "location": job.get("location") or "Remote",
            "url": job.get("url") or job.get("apply_url") or "",
            "apply_url": job.get("apply_url") or job.get("url") or "",
            "posted_at": job.get("date") or "",
            "description": clean(job.get("description") or ""),
            "detail_url": job.get("url") or "",
        }


def from_remotive():
    """remotive.com — has a design category, so ask for it directly."""
    url = "https://remotive.com/api/remote-jobs?category=design&limit=200"
    for job in (get(url) or {}).get("jobs", []):
        yield {
            "source": "remotive",
            "source_id": str(job.get("id")),
            "company": job.get("company_name") or "unknown",
            "title": job.get("title") or "",
            "location": job.get("candidate_required_location") or "Remote",
            "url": job.get("url") or "",
            "apply_url": job.get("url") or "",
            "posted_at": job.get("publication_date") or "",
            "description": clean(job.get("description") or ""),
            "detail_url": job.get("url") or "",
        }


def from_weworkremotely():
    """RSS, design category. No JSON API, but the feed is stable and public."""
    feed = get("https://weworkremotely.com/categories/remote-design-jobs.rss",
               as_json=False)
    for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
        def tag(name):
            m = re.search(rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>",
                          item, re.S)
            return html_mod.unescape(m.group(1)).strip() if m else ""

        raw_title = tag("title")
        link = tag("link")
        if not raw_title or not link:
            continue
        # WWR titles read "Company: Role Name"
        company, _, title = raw_title.partition(":")
        if not title:
            company, title = "unknown", raw_title
        if not DESIGN_RE.search(title):
            continue
        yield {
            "source": "weworkremotely",
            "source_id": (re.search(r"/(\d+)-", link) or [None, link])[1],
            "company": company.strip() or "unknown",
            "title": title.strip(),
            "location": tag("region") or "Remote",
            "url": link,
            "apply_url": link,
            "posted_at": tag("pubDate"),
            "description": clean(tag("description")),
            "detail_url": link,
        }


def from_himalayas():
    """himalayas.app — remote-first, and its JSON feed is open."""
    url = "https://himalayas.app/jobs/api?limit=200"
    for job in (get(url) or {}).get("jobs", []):
        title = job.get("title") or ""
        if not DESIGN_RE.search(title):
            continue
        locations = job.get("locationRestrictions") or []
        yield {
            "source": "himalayas",
            "source_id": str(job.get("guid") or job.get("id")),
            "company": job.get("companyName") or "unknown",
            "title": title,
            "location": ", ".join(locations) if locations else "Remote - Worldwide",
            "url": job.get("applicationLink") or job.get("url") or "",
            "apply_url": job.get("applicationLink") or job.get("url") or "",
            "posted_at": str(job.get("pubDate") or ""),
            "description": clean(job.get("description") or ""),
            "detail_url": job.get("url") or "",
        }


SOURCES = {
    "remoteok": from_remoteok,
    "remotive": from_remotive,
    "weworkremotely": from_weworkremotely,
    "himalayas": from_himalayas,
}


def collect(name):
    try:
        return name, list(SOURCES[name]()), None
    except urllib.error.HTTPError as e:
        return name, [], f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — one dead aggregator must not stop the rest
        return name, [], f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap new jobs stored")
    args = ap.parse_args()

    DB.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat()

    print(f"fetching {len(SOURCES)} remote job boards ({len(seen)} jobs already known)\n")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(collect, SOURCES))

    fresh, failed = [], []
    for name, jobs, error in results:
        if error:
            failed.append(f"{name}: {error}")
            print(f"  {name:<18} FAILED  {error}")
            continue
        new_here = 0
        for job in jobs:
            job["key"] = f"{name}:{job['source_id']}"
            job["fetched_at"] = now
            if job["key"] in seen or not job.get("url"):
                continue
            seen.add(job["key"])
            fresh.append(job)
            new_here += 1
        print(f"  {name:<18} {len(jobs):>4} design roles, {new_here:>4} new")

    if args.limit:
        fresh = fresh[: args.limit]

    with ALL_JOBS.open("a") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")
    with NEW_JOBS.open("a") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")

    print(f"\n{len(fresh)} new remote design roles -> {NEW_JOBS.relative_to(ROOT)}")
    if failed:
        print(f"{len(failed)} source(s) unavailable — the others still ran")


if __name__ == "__main__":
    main()
