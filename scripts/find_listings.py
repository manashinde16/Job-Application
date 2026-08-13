#!/usr/bin/env python3
"""
Find the real job-listings URL behind a careers landing page.

Most careers pages are marketing: "Join our mission", a demo form, and a button
that goes somewhere else. The openings live on a hosted board or a separate
listings route. This crawls each landing page and reports:

  ATS      -> the company uses a board we can already read via its API.
              Add the printed line to targets/companies.txt — far better than
              scraping, since we get structured data and full JDs.
  LISTINGS -> a plausible in-site listings URL. Replaces the landing page in
              targets/careers_urls.txt.
  NONE     -> nothing found; needs the browser channel.

    python3 scripts/find_listings.py
    python3 scripts/find_listings.py --apply    # rewrite the two target files

No model calls — this is pattern matching, and it costs nothing to run.
"""

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_jobs import get_html  # noqa: E402

CAREERS_URLS = ROOT / "targets" / "careers_urls.txt"
COMPANIES = ROOT / "targets" / "companies.txt"

# Hosted boards we can read through an API. Group 1 is the slug.
ATS_PATTERNS = [
    ("greenhouse", r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    ("lever", r"jobs\.lever\.co/([\w-]+)"),
    ("lever", r"api\.lever\.co/v0/postings/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("recruitee", r"([\w-]+)\.recruitee\.com"),
    ("smartrecruiters", r"jobs\.smartrecruiters\.com/([\w-]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([\w-]+)"),
]

# Boards with no public API — we still want to know, so the browser channel can
# take them and so we stop guessing at their landing pages.
KNOWN_NO_API = [
    ("workday", r"([\w-]+)\.(?:wd\d+\.)?myworkdayjobs\.com"),
    ("darwinbox", r"([\w-]+)\.darwinbox\.(?:in|com)"),
    ("keka", r"([\w-]+)\.kekahire\.com"),
    ("keka", r"([\w-]+)\.keka\.com"),
    ("freshteam", r"([\w-]+)\.freshteam\.com"),
    ("zohorecruit", r"([\w-]+)\.zohorecruit\.(?:com|in)"),
    ("icims", r"careers[\w-]*\.([\w-]+)\.icims\.com"),
    ("bamboohr", r"([\w-]+)\.bamboohr\.com"),
    ("teamtailor", r"([\w-]+)\.teamtailor\.com"),
    ("phenom", r"([\w-]+)\.phenompeople\.com"),
    ("taleo", r"([\w-]+)\.taleo\.net"),
    ("successfactors", r"([\w-]+)\.successfactors\.com"),
    ("jazzhr", r"([\w-]+)\.applytojob\.com"),
    ("naukri", r"naukri\.com/([\w-]+)-jobs"),
    ("instahyre", r"instahyre\.com/jobs-at-([\w-]+)"),
    ("hirist", r"hirist\.tech/([\w-]+)"),
]

# Link hints that a URL is the listings page rather than more marketing.
LISTING_HINTS = re.compile(
    r"current[- ]?opening|open[- ]?position|open[- ]?role|job[- ]?opening|"
    r"view[- ]?(?:all[- ]?)?job|all[- ]?job|/jobs?\b|/openings?\b|/vacanc|"
    r"/positions?\b|join[- ]us|work[- ]with[- ]us|apply[- ]now|careers?/search|"
    r"job[- ]search|life[- ]at",
    re.I,
)

# Never propose these as listings pages.
LINK_NOISE = re.compile(
    r"linkedin\.com/(?!company/[\w-]+/jobs)|facebook|twitter|x\.com|instagram|"
    r"youtube|mailto:|tel:|\.pdf$|\.jpg$|\.png$|/blog|/privacy|/terms|/cookie|"
    r"glassdoor|ambitionbox|indeed\.com",
    re.I,
)


def read_careers_urls():
    if not CAREERS_URLS.exists():
        sys.exit(f"missing {CAREERS_URLS}")
    out = []
    for raw in CAREERS_URLS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].startswith("http"):
            out.append((parts[0], parts[1].strip()))
    return out


def match_board(text, patterns):
    for provider, pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m and m.group(1).lower() not in ("www", "embed", "jobs", "careers"):
            return provider, m.group(1)
    return None


def score_link(href, label):
    """Higher is more likely to be the listings page."""
    score = 0
    blob = f"{href} {label}"
    if re.search(r"current[- ]?opening|open[- ]?position|open[- ]?role|view[- ]?all[- ]?job",
                 blob, re.I):
        score += 5
    if re.search(r"/jobs?\b|/openings?\b|/positions?\b|/vacanc", href, re.I):
        score += 4
    if re.search(r"careers?/search|job[- ]search", blob, re.I):
        score += 3
    if re.search(r"join[- ]us|work[- ]with[- ]us|life[- ]at", blob, re.I):
        score += 1
    return score


def investigate(entry):
    url, company = entry
    html, landed = get_html(url)
    if not html:
        return company, url, None, landed

    # A redirect to a hosted board answers the question outright.
    for patterns, kind in ((ATS_PATTERNS, "ATS"), (KNOWN_NO_API, "NO_API")):
        hit = match_board(landed, patterns)
        if hit:
            return company, url, (kind, hit[0], hit[1], landed), landed
        hit = match_board(html, patterns)
        if hit:
            return company, url, (kind, hit[0], hit[1], None), landed

    # Otherwise look for the best in-site listings link.
    best, best_score = None, 0
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>",
                         html, re.S | re.I):
        href, label = m.group(1).strip(), re.sub(r"<[^>]+>", " ", m.group(2))
        label = re.sub(r"\s+", " ", label).strip()[:60]
        if not href or href.startswith("javascript:") or LINK_NOISE.search(href):
            continue
        if not LISTING_HINTS.search(f"{href} {label}"):
            continue
        absolute = urljoin(landed, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        if absolute.rstrip("/") == landed.rstrip("/"):
            continue
        s = score_link(href, label)
        if s > best_score:
            best, best_score = (absolute, label), s

    if best:
        return company, url, ("LISTINGS", best[0], best[1], None), landed
    return company, url, None, landed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="rewrite careers_urls.txt and append boards to companies.txt")
    args = ap.parse_args()

    entries = read_careers_urls()
    print(f"crawling {len(entries)} careers pages for their real listings URLs\n")
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(investigate, entries))

    ats, no_api, listings, nothing = [], [], [], []
    for company, url, found, landed in results:
        if not found:
            nothing.append((company, url))
            print(f"  NONE      {company:<26} -> browser channel")
            continue
        kind = found[0]
        if kind == "ATS":
            _, provider, slug, _ = found
            ats.append((company, provider, slug))
            print(f"  ATS       {company:<26} {provider}/{slug}")
        elif kind == "NO_API":
            _, provider, slug, _ = found
            no_api.append((company, provider, slug, url))
            print(f"  NO-API    {company:<26} {provider} ({slug}) -> browser channel")
        else:
            _, listing_url, label, _ = found
            listings.append((company, listing_url, label))
            print(f"  LISTINGS  {company:<26} {listing_url[:62]}")

    print(f"\n{len(ats)} on a readable ATS, {len(listings)} listings URLs found, "
          f"{len(no_api)} on a no-API board, {len(nothing)} need a browser")

    if ats:
        print("\nadd to targets/companies.txt (API access — much better than scraping):\n")
        for company, provider, slug in sorted(ats):
            print(f"{provider:<16} {slug:<26} {company}")

    if listings:
        print("\nreplace these in targets/careers_urls.txt:\n")
        for company, listing_url, _ in sorted(listings):
            print(f"{listing_url:<64} {company}")

    if not args.apply:
        print("\nnothing written — re-run with --apply to update the target files")
        return

    # Companies now reachable by API stop being scraped.
    api_names = {c for c, _, _ in ats}
    replacement = {c: u for c, u, _ in listings}
    kept, moved, dropped = [], 0, 0
    for raw in CAREERS_URLS.read_text().splitlines():
        body = raw.split("#", 1)[0].strip()
        if not body or not body.split()[0].startswith("http"):
            kept.append(raw)
            continue
        url, _, company = body.partition(" ")
        company = company.strip()
        if company in api_names:
            dropped += 1
            continue
        if company in replacement:
            kept.append(f"{replacement[company]:<54} {company}")
            moved += 1
            continue
        kept.append(raw)
    CAREERS_URLS.write_text("\n".join(kept) + "\n")

    if ats:
        with COMPANIES.open("a") as fh:
            fh.write("\n# --- added by find_listings.py ---\n")
            for company, provider, slug in sorted(ats):
                fh.write(f"{provider:<16} {slug:<26} {company}\n")

    print(f"\n{moved} URLs repointed, {dropped} moved to the ATS path, "
          f"{len(ats)} boards appended to companies.txt")


if __name__ == "__main__":
    main()
