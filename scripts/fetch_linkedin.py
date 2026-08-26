#!/usr/bin/env python3
"""
Pull design roles from LinkedIn's public jobs-guest endpoints.

I had previously written LinkedIn off as unreachable without a login. That was
wrong: LinkedIn serves an unauthenticated "jobs-guest" API that returns job cards
as HTML, and a second endpoint that returns a single posting's full description.

The endpoint names, query parameters and card markup were learned from
github.com/MadsLorentzen/ai-job-search (MIT licensed), which implements the same
thing as a Bun/TypeScript CLI. This is a Python reimplementation so the agent
keeps zero dependencies and runs unchanged in GitHub Actions.

    python3 scripts/fetch_linkedin.py
    python3 scripts/fetch_linkedin.py --dry-run
    python3 scripts/fetch_linkedin.py --limit 20

Reads  targets/linkedin_searches.txt   (one "query | location | mode" per line)
Writes db/jobs.jsonl and db/jobs_new.jsonl, the same shape as every other channel

IMPORTANT — no account is used and nothing is logged into, so there is no account
to restrict. But automated access is against LinkedIn's terms, so volume is kept
deliberately low: a handful of searches a day, one page each, spaced out. Do not
raise that. It is also why descriptions are fetched only for postings that survive
the local filters.
"""

import argparse
import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_jobs import DB, clean, load_seen  # noqa: E402

SEARCHES = ROOT / "targets" / "linkedin_searches.txt"
ALL_JOBS = DB / "jobs.jsonl"
NEW_JOBS = DB / "jobs_new.jsonl"

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}

# Workplace-type filter values LinkedIn uses.
WORK_TYPE = {"onsite": "1", "remote": "2", "hybrid": "3"}

# Seconds between requests. Low volume is the whole safety story here.
PAUSE = 2.5


def fetch_html(url, retries=4):
    """GET with backoff on 429 and 5xx. Empty string on 404 or exhaustion."""
    delay = 1.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code == 429 or e.code >= 500:
                if attempt == retries:
                    print(f"    giving up after {e.code}")
                    return ""
                print(f"    {e.code}, backing off {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, 16)
                continue
            print(f"    HTTP {e.code}")
            return ""
        except Exception as e:  # noqa: BLE001 — one bad search must not stop the run
            if attempt == retries:
                print(f"    {type(e).__name__}: {e}")
                return ""
            time.sleep(delay)
            delay = min(delay * 2, 16)
    return ""


def strip_tags(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def parse_cards(html):
    """Job cards out of the search HTML.

    Split on the job-posting URN and parse each chunk on its own, so one
    malformed card cannot take out the rest of the page.
    """
    cards = []
    for chunk in html.split('data-entity-urn="urn:li:jobPosting:')[1:]:
        m = re.match(r"^(\d+)", chunk)
        if not m:
            continue
        job_id = m.group(1)

        link = re.search(r'class="base-card__full-link[^"]*"[^>]*href="([^"]+)"',
                         chunk, re.I)
        url = html_mod.unescape(link.group(1)).split("?")[0] if link else \
            f"https://www.linkedin.com/jobs/view/{job_id}"

        title = None
        h3 = re.search(r'class="base-search-card__title"[^>]*>([\s\S]*?)</h3>', chunk, re.I)
        if h3:
            title = strip_tags(h3.group(1))
        if not title:
            sr = re.search(r'class="sr-only"[^>]*>([\s\S]*?)</span>', chunk, re.I)
            if sr:
                title = strip_tags(sr.group(1))
        if not title:
            continue

        company = ""
        sub = re.search(r'class="base-search-card__subtitle"[^>]*>([\s\S]*?)</h4>',
                        chunk, re.I)
        if sub:
            company = strip_tags(sub.group(1))

        loc = re.search(r'class="job-search-card__location"[^>]*>([\s\S]*?)</span>',
                        chunk, re.I)
        location = strip_tags(loc.group(1)) if loc else ""

        dt = re.search(r'class="job-search-card__listdate[^"]*"[^>]*datetime="([^"]+)"',
                       chunk, re.I)

        cards.append({
            "source": "linkedin",
            "source_id": job_id,
            "company": company or "unknown",
            "title": title,
            "location": location,
            "url": url,
            "apply_url": url,
            "posted_at": dt.group(1) if dt else "",
            "description": "",
            "detail_url": f"{DETAIL_URL}/{job_id}",
        })
    return cards


def fetch_description(job_id):
    """Full posting text. Called only for jobs that survived the local filters."""
    html = fetch_html(f"{DETAIL_URL}/{job_id}")
    if not html:
        return ""
    body = re.search(
        r'class="(?:show-more-less-html__markup|description__text)[^"]*"[^>]*>([\s\S]*?)</div>',
        html, re.I)
    return clean(body.group(1)) if body else clean(html)


def build_url(query, location, mode, days, page=1):
    params = {}
    if query:
        params["keywords"] = query
    if location:
        params["location"] = location
    if days:
        params["f_TPR"] = f"r{int(days) * 86400}"
    if mode and mode.lower() in WORK_TYPE:
        params["f_WT"] = WORK_TYPE[mode.lower()]
    params["start"] = str((page - 1) * 10)
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


def read_searches():
    if not SEARCHES.exists():
        sys.exit(f"missing {SEARCHES} — lines of 'query | location | mode'")
    out = []
    for lineno, raw in enumerate(SEARCHES.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            print(f"  skipping {SEARCHES.name}:{lineno} — need 'query | location'")
            continue
        out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="search and report, write nothing")
    ap.add_argument("--limit", type=int, default=0, help="cap new jobs stored")
    ap.add_argument("--days", type=int, default=0,
                    help="posted within N days (default: read max_job_age_days)")
    args = ap.parse_args()

    days = args.days
    if not days:
        try:
            cfg = json.loads((ROOT / "targets" / "search.json").read_text())
            days = int(cfg.get("max_job_age_days") or 7)
        except (OSError, json.JSONDecodeError, ValueError):
            days = 7
    # LinkedIn accepts any window; matching our freshness cap means stale cards
    # are never fetched in the first place.

    searches = read_searches()
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat()
    DB.mkdir(parents=True, exist_ok=True)

    print(f"{len(searches)} LinkedIn searches, posted within {days} days\n")
    fresh, total = [], 0

    for i, (query, location, mode) in enumerate(searches):
        if i:
            time.sleep(PAUSE)          # deliberately slow; see the module docstring
        url = build_url(query, location, mode, days)
        cards = parse_cards(fetch_html(url))
        total += len(cards)

        new_here = 0
        for card in cards:
            card["key"] = f"linkedin:{card['source_id']}"
            card["fetched_at"] = now
            if card["key"] in seen:
                continue
            seen.add(card["key"])
            fresh.append(card)
            new_here += 1

        label = f"{query or 'any'} @ {location}" + (f" [{mode}]" if mode else "")
        print(f"  {label[:52]:<54} {len(cards):>3} found, {new_here:>3} new")

    if args.limit:
        fresh = fresh[: args.limit]

    print(f"\n{total} cards seen, {len(fresh)} new")
    if args.dry_run:
        for job in fresh[:15]:
            print(f"    {job['company'][:22]:<23} {job['title'][:38]:<39} "
                  f"{job['location'][:24]:<25} {job['posted_at'][:10]}")
        print("\ndry run — nothing written")
        return

    with ALL_JOBS.open("a") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")
    with NEW_JOBS.open("a") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")
    print(f"-> {NEW_JOBS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
