#!/usr/bin/env python3
"""
Pull design roles from Instahyre's public job-search API.

Instahyre exposes an unauthenticated JSON endpoint that its own site calls. The
useful part is job_functions, a numeric filter that narrows 13,700 postings down
to the ~300 design ones in a single request:

    7   UX / Visual Design
    18  Graphic Design / Animation
    77  Other Design

Free-text filters (q, keyword, location, min_experience) are accepted but
IGNORED without a login — the response comes back identical. So filtering has to
be done here, which our prefilter does anyway. Only job_functions actually works
server side, and that is the one that matters.

Individual job pages are public, and they print the experience band next to the
location ("Bangalore 4-8 Years"), so the seniority gate has something to read.

    python3 scripts/fetch_instahyre.py
    python3 scripts/fetch_instahyre.py --dry-run

Writes db/jobs.jsonl and db/jobs_new.jsonl in the shape every channel shares.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_jobs import DB, load_seen  # noqa: E402

ALL_JOBS = DB / "jobs.jsonl"
NEW_JOBS = DB / "jobs_new.jsonl"

API = "https://www.instahyre.com/api/v1/job_search"
DESIGN_FUNCTIONS = (7, 18, 77)
PAGE = 100
MAX_PAGES = 6          # 600 postings is far more than the ~300 that exist
PAUSE = 1.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.instahyre.com/",
}


def fetch_page(offset):
    params = "&".join(f"job_functions={f}" for f in DESIGN_FUNCTIONS)
    url = f"{API}?limit={PAGE}&offset={offset}&{params}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} at offset {offset}")
    except Exception as e:  # noqa: BLE001 — one bad page must not stop the run
        print(f"  {type(e).__name__} at offset {offset}")
    return None


def to_job(obj):
    employer = obj.get("employer")
    if isinstance(employer, dict):
        company = employer.get("company_name") or employer.get("name") or "unknown"
    else:
        company = str(employer or "unknown")

    job_id = str(obj.get("id") or "")
    url = obj.get("public_url") or ""
    keywords = obj.get("keywords") or []

    return {
        "source": "instahyre",
        "source_id": job_id,
        "company": company,
        "title": obj.get("title") or obj.get("candidate_title") or "",
        "location": obj.get("locations") or "",
        "url": url,
        "apply_url": url,
        # The API carries no posting date. Undated jobs are kept by the freshness
        # rule (a live listing is presumed current) and counted separately.
        "posted_at": "",
        # Keywords are the only text the list gives us. They are worth keeping as
        # a stopgap description so a role is still scoreable if its page fails to
        # load; the full page is fetched on demand via detail_url.
        "description": "",
        "keywords": ", ".join(str(k) for k in keywords) if keywords else "",
        "detail_url": url,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    DB.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat()

    first = fetch_page(0)
    if not first:
        sys.exit("Instahyre did not respond — nothing fetched")
    total = (first.get("meta") or {}).get("total_count", 0)
    print(f"Instahyre: {total} design postings (job_functions {DESIGN_FUNCTIONS})\n")

    pages = [first]
    for page in range(1, min(MAX_PAGES, -(-total // PAGE))):
        time.sleep(PAUSE)
        data = fetch_page(page * PAGE)
        if not data or not data.get("objects"):
            break
        pages.append(data)

    fresh, count = [], 0
    for data in pages:
        for obj in data.get("objects") or []:
            job = to_job(obj)
            if not job["title"] or not job["source_id"]:
                continue
            count += 1
            job["key"] = f"instahyre:{job['source_id']}"
            job["fetched_at"] = now
            if job["key"] in seen:
                continue
            seen.add(job["key"])
            fresh.append(job)

    print(f"{count} postings read across {len(pages)} page(s), {len(fresh)} new")
    if args.limit:
        fresh = fresh[: args.limit]

    if args.dry_run:
        for job in fresh[:15]:
            print(f"    {job['company'][:22]:<23} {job['title'][:38]:<39} "
                  f"{job['location'][:22]}")
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
