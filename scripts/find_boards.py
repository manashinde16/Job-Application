#!/usr/bin/env python3
"""
Given a list of company names, work out which job board each one uses.

Guessing slugs by hand wastes time and produces silent 404s. This probes every
provider with a few name variants and prints the lines that actually returned
jobs, ready to paste into targets/companies.txt.

    python3 scripts/find_boards.py                    # reads targets/candidates.txt
    python3 scripts/find_boards.py Razorpay Groww      # or names on the CLI
    python3 scripts/find_boards.py --append            # write hits straight to companies.txt

Only public board endpoints are touched — the same ones the companies' own
careers pages call.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "targets" / "candidates.txt"
COMPANIES = ROOT / "targets" / "companies.txt"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) job-agent/1.0"
TIMEOUT = 12

# url template -> how to count jobs in the response
PROBES = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        lambda d: len(d.get("jobs", [])) if isinstance(d, dict) else 0,
    ),
    "lever": (
        "https://api.lever.co/v0/postings/{slug}?mode=json",
        lambda d: len(d) if isinstance(d, list) else 0,
    ),
    "ashby": (
        "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        lambda d: len(d.get("jobs", [])) if isinstance(d, dict) else 0,
    ),
    "workable": (
        "https://apply.workable.com/api/v1/widget/accounts/{slug}",
        lambda d: len(d.get("jobs", [])) if isinstance(d, dict) else 0,
    ),
    "recruitee": (
        "https://{slug}.recruitee.com/api/offers/",
        lambda d: len(d.get("offers", [])) if isinstance(d, dict) else 0,
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
        lambda d: d.get("totalFound", 0) if isinstance(d, dict) else 0,
    ),
}


def slug_variants(name):
    """Company name -> plausible board slugs, most likely first."""
    base = re.sub(r"[^a-z0-9\s-]", "", name.lower()).strip()
    compact = re.sub(r"[\s-]+", "", base)
    hyphen = re.sub(r"\s+", "-", base)
    variants = [compact, hyphen]
    # "Tata 1mg" -> also try just "1mg"; drop common suffixes
    stripped = re.sub(r"(technologies|technology|labs|inc|ltd|limited|pvt|private)$", "", compact)
    variants.append(stripped)
    seen, out = set(), []
    for v in variants:
        if v and len(v) > 1 and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def probe(provider, slug):
    url, counter = PROBES[provider]
    req = urllib.request.Request(
        url.format(slug=slug), headers={"User-Agent": UA, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            json.JSONDecodeError, ConnectionError):
        return 0
    try:
        return counter(data)
    except (AttributeError, TypeError):
        return 0


def find(name):
    """Return (provider, slug, count) for the best hit, or None."""
    hits = []
    for slug in slug_variants(name):
        for provider in PROBES:
            count = probe(provider, slug)
            if count > 0:
                hits.append((provider, slug, count))
        if hits:  # first variant that works wins — don't keep hammering
            break
    if not hits:
        return None
    return max(hits, key=lambda h: h[2])


def read_candidates():
    if not CANDIDATES.exists():
        sys.exit(f"missing {CANDIDATES} — one company name per line")
    names = []
    for raw in CANDIDATES.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    append = "--append" in sys.argv
    names = args or read_candidates()

    print(f"probing {len(names)} companies across {len(PROBES)} providers\n")
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(find, names))

    hits, misses = [], []
    for name, result in zip(names, results):
        if result:
            provider, slug, count = result
            hits.append((provider, slug, name, count))
            print(f"  FOUND  {name:<24} {provider:<11} {slug:<24} {count:>4} jobs")
        else:
            misses.append(name)

    print(f"\n{len(hits)} found, {len(misses)} not on a supported board")
    if misses:
        print("\nThese use their own portal / Naukri / Darwinbox — browser path, later:")
        for m in misses:
            print(f"  - {m}")

    if hits:
        lines = [f"{p:<11} {s:<24} {n}" for p, s, n, _ in sorted(hits)]
        block = "\n".join(lines)
        if append:
            with COMPANIES.open("a") as fh:
                fh.write("\n# --- added by find_boards.py ---\n" + block + "\n")
            print(f"\nappended {len(hits)} boards to {COMPANIES.relative_to(ROOT)}")
        else:
            print("\npaste into targets/companies.txt:\n")
            print(block)


if __name__ == "__main__":
    main()
