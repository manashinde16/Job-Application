#!/usr/bin/env python3
"""
Pull remote design roles from freehire.me's public agent search API.

freehire aggregates ~3.1M postings and, unlike most aggregators, exposes real
server-side facets — so the filtering happens before the data crosses the wire
instead of locally:

    category=design        34,842 design postings
    seniority=junior       her level, as a first-class facet
    work_mode=remote       remote only
    posted_within_days     matches our freshness cap
    include_description    full description inline, so no per-job follow-up fetch

Endpoint and parameter names come from github.com/MadsLorentzen/ai-job-search
(MIT), whose freehire-search skill documents the API. Reimplemented in Python
stdlib to keep the agent dependency-free.

Note the endpoint that matters is /api/v1/agent/jobs/search. The plainer
/api/v1/jobs path also returns 200 but IGNORES every filter — it served the same
3.1M-row total for every query I tried, which looks like it works and is useless.

    python3 scripts/fetch_freehire.py
    python3 scripts/fetch_freehire.py --dry-run

Reads  targets/freehire_searches.txt   (one query string per line)
Writes db/jobs.jsonl and db/jobs_new.jsonl, the shape every channel shares
"""

import argparse
import json
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

SEARCHES = ROOT / "targets" / "freehire_searches.txt"
ALL_JOBS = DB / "jobs.jsonl"
NEW_JOBS = DB / "jobs_new.jsonl"

API = "https://freehire.me/api/v1/agent/jobs/search"
PAGE = 50
PAUSE = 1.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
}


def search(params):
    url = f"{API}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}")
    except Exception as e:  # noqa: BLE001 — one bad search must not stop the run
        print(f"    {type(e).__name__}: {e}")
    return None


def to_job(row):
    slug = row.get("public_slug") or ""
    # `url` is the real posting on the employer's ATS; the freehire page is the
    # fallback so there is always somewhere to apply.
    url = row.get("url") or (f"https://freehire.me/jobs/{slug}" if slug else "")
    enrich = row.get("enrichment") or {}

    location = row.get("location") or ""
    if not location:
        # No free-text location on some rows; build one so the geo rule has
        # something to read, and so a remote row is recognisably remote.
        bits = row.get("cities") or row.get("countries") or row.get("regions") or []
        location = ", ".join(str(b) for b in bits[:3])
    if row.get("work_mode") == "remote" and "remote" not in location.lower():
        location = f"Remote{', ' + location if location else ''}"

    return {
        "source": "freehire",
        "source_id": slug,
        "company": row.get("company") or "unknown",
        "title": row.get("title") or "",
        "location": location,
        "url": url,
        "apply_url": url,
        "posted_at": row.get("posted_at") or row.get("created_at") or "",
        # Full text arrives inline thanks to include_description, so scoring
        # needs no second request per job.
        "description": clean(row.get("description") or ""),
        "work_mode": row.get("work_mode") or "",
        "seniority": enrich.get("seniority") or "",
        "detail_url": f"https://freehire.me/api/v1/jobs/{slug}" if slug else "",
    }


def read_searches():
    if not SEARCHES.exists():
        sys.exit(f"missing {SEARCHES}")
    out = []
    for raw in SEARCHES.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--days", type=int, default=0,
                    help="posted within N days (default: max_job_age_days)")
    args = ap.parse_args()

    days = args.days
    if not days:
        try:
            cfg = json.loads((ROOT / "targets" / "search.json").read_text())
            days = int(cfg.get("max_job_age_days") or 8)
        except (OSError, json.JSONDecodeError, ValueError):
            days = 8

    DB.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat()
    searches = read_searches()

    print(f"{len(searches)} freehire searches, posted within {days} days\n")
    fresh, total = [], 0

    for i, qs in enumerate(searches):
        if i:
            time.sleep(PAUSE)
        params = dict(urllib.parse.parse_qsl(qs))
        # doseq handles repeated facets, so rebuild any that appeared twice.
        multi = {}
        for key, value in urllib.parse.parse_qsl(qs):
            multi.setdefault(key, []).append(value)
        params = {k: (v if len(v) > 1 else v[0]) for k, v in multi.items()}
        params.update({
            "limit": PAGE,
            "offset": 0,
            "semantic_ratio": 0,        # keyword search; semantic is opt-in
            "posted_within_days": days,
            "include_description": "true",
            "description_format": "text",
        })

        data = search(params)
        if not data:
            print(f"  {qs[:56]:<58} failed")
            continue
        rows = data.get("data") or []
        reported = (data.get("meta") or {}).get("total")
        total += len(rows)

        new_here = 0
        for row in rows:
            job = to_job(row)
            if not job["title"] or not job["source_id"]:
                continue
            job["key"] = f"freehire:{job['source_id']}"
            job["fetched_at"] = now
            if job["key"] in seen:
                continue
            seen.add(job["key"])
            fresh.append(job)
            new_here += 1

        print(f"  {qs[:56]:<58} {len(rows):>3} of {reported} match, {new_here:>3} new")

    if args.limit:
        fresh = fresh[: args.limit]

    print(f"\n{total} rows read, {len(fresh)} new")
    if args.dry_run:
        for job in fresh[:15]:
            print(f"    {job['company'][:20]:<21} {job['title'][:34]:<35} "
                  f"{job['location'][:24]:<25} {job['seniority']}")
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
