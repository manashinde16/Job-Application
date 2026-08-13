#!/usr/bin/env python3
"""
Score fetched jobs against the candidate profile.

Two passes, deliberately:

  1. Prefilter — pure regex on title and location. Free, instant, and it throws
     away ~98% of the roles. Nothing reaches a model that a keyword could have
     rejected.
  2. Model scoring — one call per survivor, returning structured JSON: fit,
     tier, what to lead with, which case study to link, red flags.

The split is what keeps the daily run inside a free API tier.

    python3 scripts/score_jobs.py                 # score this run's new jobs
    python3 scripts/score_jobs.py --all           # rescore everything in the db
    python3 scripts/score_jobs.py --dry-run       # prefilter only, no API key needed
    python3 scripts/score_jobs.py --limit 20

Reads  profile/profile.md, targets/search.json, db/jobs_new.jsonl
Writes db/scored.jsonl
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_jobs import fetch_description  # noqa: E402

PROFILE = ROOT / "profile" / "profile.md"
SEARCH = ROOT / "targets" / "search.json"
DB = ROOT / "db"
SCORED = DB / "scored.jsonl"

SYSTEM = """You screen job postings for one specific candidate. You are blunt and
calibrated, not encouraging. Most postings are a bad fit and should score low.

HARD RULE — seniority. The candidate has roughly 1.5 years total: 13 months in her
current role plus a 7-month internship. A posting is eligible only if its
requirement TOPS OUT at {cap} years.
- eligible: fresher, entry-level, graduate, "0-2 years", "1-3 years",
  "2-3 years", "3 years"
- NOT eligible: "{cap}+ years", "minimum {cap} years", "at least {cap} years",
  "3-5 years", "4+ years", mid-level, senior, lead
The plus sign matters. "3 years" is fine; "3+ years" is not, because the plus
means there is no upper bound. Treat "minimum 3" and "at least 3" as "3+".
When ineligible: tier MUST be "skip", fit MUST be 3 or lower, and say so in
red_flags. Apply this even when the title sounds junior.

Other rules:
- Score against what the candidate has ACTUALLY done. Never credit potential.
- Visual/brand/graphic design roles AT PRODUCT COMPANIES OR DESIGN STUDIOS are a
  legitimate route in for this candidate and should score well — do not penalise
  them for not being titled "Product Designer".
- Location. Two different rules:
  * ONSITE or HYBRID: only Pune, Mumbai, Navi Mumbai, Thane, Hyderabad or Nagpur.
    Anywhere else, including Bengaluru and NCR, is location_ok false.
  * REMOTE: acceptable ANYWHERE IN THE WORLD. She is in India with no visa, so
    the only remote roles to reject are ones restricted to a region she cannot
    work from — "Remote (US only)", "Remote - EMEA". A globally remote role, or
    one open to India, is location_ok true regardless of where the company is.
- Only name skills, projects and numbers that appear in the profile. Inventing
  anything makes the output useless."""

PROMPT = """CANDIDATE PROFILE
{profile}

JOB POSTING
Company:  {company}
Title:    {title}
Location: {location}
URL:      {url}

Experience figures detected in this JD by regex: {years_found}
(may be empty or noisy — trust your own reading of the text over this)

Description:
{description}

Return JSON with exactly these keys:
{{
  "fit": 0-10 integer, how well the candidate matches THIS posting,
  "tier": "apply" | "stretch" | "skip",
  "years_required": the minimum years the JD asks for, as a number, or null,
  "years_ok": true if that minimum is {cap} or less, else false,
  "seniority": "under" | "good" | "over",
  "location_ok": true | false,
  "location_note": "one clause on why",
  "why": "1-2 sentences, concrete, naming the actual overlap",
  "matched": ["profile facts that genuinely match, max 4"],
  "gaps": ["what the posting wants that she lacks, max 4"],
  "angle": "what her resume and email should lead with for THIS role, 1 sentence",
  "case_study": "which of her projects to link, and why — or null if none fits",
  "red_flags": ["anything that makes this a bad use of an application, or []"]
}}

Calibration: 8-10 = apply today. 6-7 = worth an application. 4-5 = stretch,
only if the company is a strong target. 0-3 = skip.
If years_ok is false, tier is "skip" and fit is 3 or lower. No exceptions."""


# How a JD states its experience bar. open_ended means "this many or more", which
# is the difference between "3 years" (acceptable) and "3+ years" (not).
YEARS_PATTERNS = [
    # "4+ years", "3 + yrs", "5 years or more"
    (r"(\d+)\s*\+\s*(?:years?|yrs?)", True),
    (r"(\d+)\s*(?:years?|yrs?)\s*(?:or|and)\s*(?:more|above|higher|plus)", True),
    # "minimum 3 years", "at least 4 yrs" — also a floor with no ceiling
    (r"(?:minimum|min\.?|at\s*least|atleast|over|more\s+than)\s*(?:of\s*)?"
     r"(\d+)\s*(?:years?|yrs?)", True),
    # "2-4 years" — bounded, so the UPPER bound is what matters
    (r"\d+\s*(?:-|–|—|to)\s*(\d+)\s*(?:years?|yrs?)", False),
    # plain "3 years of experience"
    (r"(\d+)\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant\s+|professional\s+|proven\s+"
     r"|total\s+|hands[- ]on\s+|industry\s+)?experience", False),
]

FRESHER_HINTS = re.compile(
    r"fresher|entry[- ]level|no prior experience|0\s*(?:-|–|to)\s*[12]\s*(?:years?|yrs?)"
    r"|graduate program|campus hire|intern(?:ship)?\b",
    re.I,
)


def years_figures(text):
    """Experience requirements found in the JD, as (years, open_ended) pairs.

    A bounded range contributes its UPPER bound: "1-3 years" tops out at 3, which
    is acceptable, whereas "3-5" tops out at 5 and is not.
    """
    found = set()
    for pattern, open_ended in YEARS_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 <= n <= 30:  # ignore "10000 users", "2024", and similar noise
                found.add((n, open_ended))
    return sorted(found)


def min_years_required(text):
    """Just the numbers, for display and for the prompt."""
    return sorted({n for n, _ in years_figures(text)})


def experience_gate(text, cap, open_ended_cap=None):
    """Reject postings that ask for more experience than she has.

    Two ways to fail, matching the rule exactly:
      - a bounded requirement whose top is above the cap ("3-5 years", cap 3)
      - an open-ended floor at or above open_ended_cap ("3+ years", "minimum 3")
        because the plus means there is no ceiling

    A posting that never names a number is not rejected here — the model reads it.
    """
    if FRESHER_HINTS.search(text):
        return None

    open_ended_cap = cap if open_ended_cap is None else open_ended_cap
    figures = years_figures(text)
    if not figures:
        return None

    # "minimum 3 years" matches both the open-ended pattern and the plain-years
    # one, giving (3, True) and (3, False) for a single phrase. The open-ended
    # reading is the true one, so collapse per number with True winning.
    bar = {}
    for n, open_ended in figures:
        bar[n] = bar.get(n, False) or open_ended

    acceptable = [
        n for n, open_ended in bar.items()
        if ((n < open_ended_cap) if open_ended else (n <= cap))
    ]
    if acceptable:
        return None

    worst = min(bar)
    return (f"needs {worst}{'+' if bar[worst] else ''} yrs "
            f"(cap {cap}, no open-ended {open_ended_cap}+)")


# Every source stamps its dates differently: ISO, epoch seconds, epoch
# milliseconds, RFC822, or a human phrase scraped off a portal row.
RELATIVE_RE = re.compile(
    r"(?:(\d+)\+?\s*(minute|hour|day|week|month)s?\s*ago)"
    r"|(just\s*posted|today|new|posted\s*today|yesterday|active\s*today)",
    re.I,
)
RELATIVE_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}


def parse_posted(value):
    """Best-effort age in days from any of the date shapes we receive.

    Returns None when nothing can be read — the caller decides what to do with
    an unknown date rather than guessing at one.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    now = datetime.now(timezone.utc)

    # Epoch, seconds or milliseconds
    if re.fullmatch(r"\d{10}", text):
        return (now - datetime.fromtimestamp(int(text), timezone.utc)).days
    if re.fullmatch(r"\d{13}", text):
        return (now - datetime.fromtimestamp(int(text) / 1000, timezone.utc)).days

    # "3 days ago", "Just posted", "30+ days ago"
    m = RELATIVE_RE.search(text)
    if m:
        if m.group(3):
            return 1 if m.group(3).lower() == "yesterday" else 0
        return int(m.group(1)) * RELATIVE_DAYS[m.group(2).lower()]

    # ISO 8601, with or without timezone
    iso = text.replace("Z", "+00:00")
    for candidate in (iso, iso[:19], iso[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (now - dt).days
        except ValueError:
            continue

    # RFC822, as used by RSS feeds
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).days
    except (TypeError, ValueError, IndexError):
        return None


def job_age_days(job):
    """Age of a posting in days, or None if no source field is readable."""
    for field in ("posted_at", "posted", "fetched_at"):
        if field == "fetched_at":
            break  # fetched_at is when WE saw it, not when it was posted
        age = parse_posted(job.get(field))
        if age is not None:
            return max(age, 0)
    return None


def load_search():
    cfg = json.loads(SEARCH.read_text())
    compile_any = lambda key: re.compile("|".join(cfg[key]), re.I)
    cfg["_inc_title"] = compile_any("title_include")
    cfg["_exc_title"] = compile_any("title_exclude")
    cfg["_onsite"] = compile_any("onsite_cities")
    cfg["_onsite_no"] = compile_any("onsite_excluded_cities")
    cfg["_remote"] = compile_any("remote_markers")
    cfg["_remote_global"] = compile_any("remote_global_markers")
    cfg["_remote_no"] = compile_any("remote_restricted_to")
    return cfg


def prefilter(job, cfg):
    """Return None to keep the job, or a string reason for dropping it."""
    # Freshness first: it is the cheapest check and removes the most.
    max_age = cfg.get("max_job_age_days")
    if max_age:
        age = job_age_days(job)
        if age is not None and age > max_age:
            # Stable prefix so the run summary groups these into one line.
            return f"posted over {max_age} days ago ({age}d)"

    title = job.get("title", "")
    if not cfg["_inc_title"].search(title):
        return "title not a design role"
    if cfg["_exc_title"].search(title):
        return "title excluded (seniority or wrong discipline)"

    loc = job.get("location", "") or ""
    if not loc.strip():
        return None  # unstated location — let the model read the description

    # Remote and onsite are judged by different rules.
    if cfg["_remote"].search(loc):
        # A remote role anywhere in the world is fine, unless the posting pins
        # itself to a region she has no right to work in. Naming worldwide/global/
        # India alongside that rescues it ("Remote - India, United States").
        if cfg["_remote_no"].search(loc) and not cfg["_remote_global"].search(loc):
            return f"remote but restricted to another region ({loc[:40]})"
        return None

    # Onsite or hybrid: she has to be able to get there.
    if cfg["_onsite_no"].search(loc) and not cfg["_onsite"].search(loc):
        return f"onsite in a city she has excluded ({loc[:40]})"
    if cfg["_onsite"].search(loc):
        return None
    if re.search(r"\bindia\b|\bin-", loc, re.I):
        return None  # "India" with no city — ambiguous, let the model decide
    return f"onsite outside her cities ({loc[:40]})"


def load_jobs(use_all):
    path = DB / ("jobs.jsonl" if use_all else "jobs_new.jsonl")
    if not path.exists():
        sys.exit(f"missing {path} — run scripts/fetch_jobs.py first")
    jobs = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                jobs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return jobs


def already_scored():
    if not SCORED.exists():
        return set()
    keys = set()
    for line in SCORED.read_text().splitlines():
        if line.strip():
            try:
                keys.add(json.loads(line)["key"])
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="rescore the whole db, not just new jobs")
    ap.add_argument("--dry-run", action="store_true", help="prefilter only, no model calls")
    ap.add_argument("--limit", type=int, default=0, help="cap how many jobs get scored")
    ap.add_argument("--rescore", action="store_true", help="score jobs even if already scored")
    args = ap.parse_args()

    cfg = load_search()
    profile = PROFILE.read_text()
    jobs = load_jobs(args.all)
    seen = set() if args.rescore else already_scored()

    kept, drops = [], {}
    for job in jobs:
        reason = prefilter(job, cfg)
        if reason:
            drops[reason.split(" (")[0]] = drops.get(reason.split(" (")[0], 0) + 1
        elif job["key"] in seen:
            drops["already scored"] = drops.get("already scored", 0) + 1
        else:
            kept.append(job)

    print(f"{len(jobs)} jobs in -> {len(kept)} survived the prefilter\n")
    if cfg.get("max_job_age_days"):
        undated = sum(1 for j in kept if job_age_days(j) is None)
        print(f"  freshness: {len(kept) - undated} dated within "
              f"{cfg['max_job_age_days']} days, {undated} with no readable date "
              f"(kept — a live listing page is presumed current)\n")
    for reason, n in sorted(drops.items(), key=lambda kv: -kv[1]):
        print(f"  dropped {n:>5}  {reason}")

    if not kept:
        print("\nnothing to score")
        return

    print(f"\n{'-' * 78}")
    for job in kept:
        print(f"  {job['company'][:16]:<17} {job['title'][:44]:<45} {job['location'][:24]}")
    print(f"{'-' * 78}")

    if args.dry_run:
        print(f"\ndry run — {len(kept)} jobs would be scored. Set an API key to score them.")
        return

    from llm import LLMError, complete_json  # noqa: PLC0415 — only needed past this point

    cap = args.limit or cfg["max_llm_calls_per_run"]
    todo = kept[:cap]
    if len(kept) > cap:
        print(f"\ncapping at {cap} model calls this run ({len(kept) - cap} deferred)")

    DB.mkdir(parents=True, exist_ok=True)
    results, failures = [], 0
    print(f"\nscoring {len(todo)} jobs\n")

    cap = cfg["max_years_required"]
    open_cap = cfg.get("reject_open_ended_at_or_above", cap)
    system = SYSTEM.format(cap=cap)
    gated = 0

    for i, job in enumerate(todo, 1):
        desc = fetch_description(job)
        if not desc:
            print(f"  [{i}/{len(todo)}] {job['company'][:14]:<15} no description available, skipped")
            continue

        # Cheap deterministic reject before spending a model call.
        blocked = experience_gate(desc, cap, open_cap)
        if blocked:
            gated += 1
            print(f"  [{i}/{len(todo)}] {job['company'][:14]:<15} "
                  f"too senior — {blocked:<22} {job['title'][:32]}")
            continue

        prompt = PROMPT.format(
            profile=profile,
            company=job.get("company", ""),
            title=job.get("title", ""),
            location=job.get("location", ""),
            url=job.get("url", ""),
            years_found=min_years_required(desc) or "none found",
            cap=cap,
            description=desc[: cfg["jd_chars_for_scoring"]],
        )
        try:
            verdict = complete_json(prompt, system=system, max_tokens=1200)
        except LLMError as e:
            failures += 1
            print(f"  [{i}/{len(todo)}] {job['company'][:14]:<15} FAILED  {str(e)[:70]}")
            if failures >= 5:
                print("\n5 consecutive-ish failures — stopping. Check the API key and quota.")
                break
            continue

        record = {
            "key": job["key"],
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "url": job.get("url", ""),
            "apply_url": job.get("apply_url", ""),
            "source": job.get("source", ""),
            "description": desc,
            "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "years_detected": min_years_required(desc),
            **{k: verdict.get(k) for k in
               ("fit", "tier", "years_required", "years_ok", "seniority",
                "location_ok", "location_note", "why", "matched", "gaps",
                "angle", "case_study", "red_flags")},
        }
        results.append(record)
        fit = record.get("fit")
        print(f"  [{i}/{len(todo)}] {job['company'][:14]:<15} "
              f"fit {fit if fit is not None else '?':>2}  {str(record.get('tier')):<8} "
              f"{job['title'][:36]}")

    # Safety net: the seniority rule is a hard constraint, so enforce it on the
    # model's own answer too, not just on the regex gate.
    keep = [
        r for r in results
        if (r.get("fit") or 0) >= cfg["min_fit_to_keep"]
        and r.get("years_ok") is not False
        and (r.get("years_required") or 0) <= cap
    ]
    with SCORED.open("a") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    if gated:
        print(f"\n{gated} rejected by the {cap}-year gate before any model call")
    print(f"\n{len(results)} scored, {len(keep)} at fit >= {cfg['min_fit_to_keep']} "
          f"and within {cap} yrs")
    print(f"-> {SCORED.relative_to(ROOT)}")

    if keep:
        print("\nworth applying to:\n")
        for r in sorted(keep, key=lambda r: -(r.get("fit") or 0)):
            print(f"  {r['fit']:>2}/10  {r['tier']:<8} {r['company'][:16]:<17} {r['title'][:40]}")
            print(f"         {r.get('why', '')[:100]}")


if __name__ == "__main__":
    main()
