#!/usr/bin/env python3
"""
Read company careers pages that aren't on a supported ATS.

~84% of this candidate's target employers — Indian product companies and design
studios — publish openings on their own site with no API. Rather than write a
parser per site, this fetches the page text and has the model extract the
openings. One prompt covers every site.

    python3 scripts/fetch_careers.py --dry-run   # fetch only, report page health
    python3 scripts/fetch_careers.py             # fetch + extract openings
    python3 scripts/fetch_careers.py --limit 10

Reads  targets/careers_urls.txt
Writes db/jobs.jsonl and db/jobs_new.jsonl (same shape as fetch_jobs.py, so
       score_jobs.py consumes both channels identically)

Pages built as client-rendered SPAs return almost no text to a plain fetch.
Those are reported as NEEDS-BROWSER rather than silently skipped — they get
picked up by the browser channel instead.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_jobs import DB, clean, get_html, load_seen  # noqa: E402
from html import unescape as html_unescape  # noqa: E402

URLS = ROOT / "targets" / "careers_urls.txt"
ALL_JOBS = DB / "jobs.jsonl"
NEW_JOBS = DB / "jobs_new.jsonl"
NEEDS_BROWSER = DB / "needs_browser.txt"

# Below this much text, the page is almost certainly JS-rendered.
MIN_TEXT = 600
MAX_TEXT = 14000

SYSTEM = """You extract job openings from careers page text. You are a parser, not
an assistant. If the text contains no job openings, return an empty array. Never
invent a posting, a title, or a location that is not present in the text."""

PROMPT = """Below is the text of {company}'s careers page ({url}).

Extract every DESIGN opening: product design, UX, UI, visual, graphic, brand,
communication, interaction, motion design, or any role with "designer" in the
title. Ignore engineering, sales, marketing, HR, finance and support roles.

Return a JSON array. One object per opening:
[
  {{"title": "exact title as written",
    "location": "exact location as written, or \\"\\" if not stated",
    "url": "link to the posting if one appears in the text, else \\"\\""}}
]

Return [] if there are no design openings. Do not guess at roles that may exist.

PAGE TEXT:
{text}"""


def read_urls():
    if not URLS.exists():
        sys.exit(f"missing {URLS} — lines of '<url>  <company name>'")
    out = []
    for lineno, raw in enumerate(URLS.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2 or not parts[0].startswith("http"):
            print(f"  skipping {URLS.name}:{lineno} — expected '<url>  <company>'")
            continue
        out.append((parts[0], parts[1].strip()))
    return out


def clean_html(html):
    """Raw HTML -> readable text, dropping script/style bodies first."""
    if not html:
        return ""
    stripped = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ",
                      html, flags=re.S | re.I)
    return clean(stripped)


ANCHOR_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
STOPWORDS = {
    "the", "and", "for", "with", "job", "jobs", "career", "careers", "hiring",
    "apply", "role", "position", "opening", "openings", "a", "an", "of", "in",
    "at", "to", "view", "details", "more", "read",
}


def _tokens(text):
    return {
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 1 and w not in STOPWORDS
    }


def extract_anchors(html, base):
    """Every link on the page, as (absolute_url, label, label_tokens).

    The model is given plain text, which has no hrefs in it — so per-job links
    have to come from the markup and be matched back to the extracted titles.
    Without this, every careers-page job pointed at the listing page instead of
    its own posting.
    """
    anchors = []
    for href, inner in ANCHOR_RE.findall(html):
        label = re.sub(r"<[^>]+>", " ", inner)
        label = html_unescape(re.sub(r"\s+", " ", label)).strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base, href)
        anchors.append((absolute, label, _tokens(label) | _tokens(href)))
    return anchors


def match_job_url(title, anchors, fallback):
    """Best per-job link for an extracted title, or the listing page."""
    wanted = _tokens(title)
    if not wanted:
        return fallback
    best, best_score = None, 0.0
    for url, label, tokens in anchors:
        if not tokens:
            continue
        overlap = len(wanted & tokens)
        if not overlap:
            continue
        # Reward covering the title, and prefer the tighter of two equal matches.
        score = overlap / len(wanted) + 0.25 * (overlap / len(tokens))
        if score > best_score:
            best, best_score = url, score
    # Require most of the title to be present; a single shared word is noise.
    return best if best and best_score >= 0.7 else fallback


def fetch_one(entry):
    url, company = entry
    html, landed = get_html(url)
    return company, url, clean_html(html), extract_anchors(html, landed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch only, no model calls")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    entries = read_urls()
    if args.limit:
        entries = entries[: args.limit]

    print(f"fetching {len(entries)} careers pages\n")
    with ThreadPoolExecutor(max_workers=10) as pool:
        pages = list(pool.map(fetch_one, entries))

    usable, thin, dead = [], [], []
    for company, url, text, anchors in pages:
        if not text:
            dead.append((company, url))
        elif len(text) < MIN_TEXT:
            thin.append((company, url, len(text)))
        else:
            usable.append((company, url, text, anchors))

    for company, url, text, _anchors in sorted(usable, key=lambda p: -len(p[2])):
        print(f"  OK             {company:<28} {len(text):>7} chars")
    for company, url, n in thin:
        print(f"  NEEDS-BROWSER  {company:<28} {n:>7} chars (JS-rendered)")
    for company, url in dead:
        print(f"  UNREACHABLE    {company:<28}         {url[:44]}")

    print(f"\n{len(usable)} readable, {len(thin)} need a browser, {len(dead)} unreachable")

    DB.mkdir(parents=True, exist_ok=True)
    if thin or dead:
        NEEDS_BROWSER.write_text(
            "\n".join(f"{u}\t{c}" for c, u, _ in thin) + "\n"
            + "\n".join(f"{u}\t{c}" for c, u in dead) + "\n"
        )
        print(f"-> {NEEDS_BROWSER.relative_to(ROOT)} for the browser channel")

    if args.dry_run:
        print("\ndry run — no openings extracted. Set an API key to extract.")
        return
    if not usable:
        print("\nno readable pages, nothing to extract")
        return

    from llm import LLMError, complete_json  # noqa: PLC0415

    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat()
    fresh = []
    print(f"\nextracting openings from {len(usable)} pages\n")

    for company, url, text, anchors in usable:
        try:
            openings = complete_json(
                PROMPT.format(company=company, url=url, text=text[:MAX_TEXT]),
                system=SYSTEM,
                max_tokens=2000,
            )
        except LLMError as e:
            print(f"  {company:<28} FAILED  {str(e)[:60]}")
            continue
        if not isinstance(openings, list):
            print(f"  {company:<28} unexpected shape, skipped")
            continue

        new_here = 0
        for opening in openings:
            if not isinstance(opening, dict) or not opening.get("title"):
                continue
            link = (opening.get("url") or "").strip()
            link = urljoin(url, link) if link else ""
            if not link or link.rstrip("/") == url.rstrip("/"):
                link = match_job_url(opening["title"], anchors, url)
            slug = re.sub(r"[^a-z0-9]+", "-", opening["title"].lower()).strip("-")
            key = f"careers:{company.lower().replace(' ', '-')}:{slug}"
            if key in seen:
                continue
            seen.add(key)
            fresh.append({
                "source": "careers",
                "source_id": slug,
                "company": company,
                "title": opening["title"].strip(),
                "location": (opening.get("location") or "").strip(),
                "url": link,
                "apply_url": link,
                "posted_at": "",
                "description": "",
                # Falls back to the listing page when the extraction found no
                # per-job link. Less detail than a real JD, but the scorer still
                # gets the company context instead of dropping the role.
                "detail_url": link,
                "key": key,
                "fetched_at": now,
            })
            new_here += 1
        print(f"  {company:<28} {len(openings):>3} design roles, {new_here:>3} new")

    with ALL_JOBS.open("a") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")
    with NEW_JOBS.open("a") as fh:
        for job in fresh:
            fh.write(json.dumps(job) + "\n")

    print(f"\n{len(fresh)} new design roles -> {NEW_JOBS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
