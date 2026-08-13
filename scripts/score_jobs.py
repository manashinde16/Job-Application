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
current role plus a 7-month internship. She is a junior designer. A posting is
eligible ONLY if its minimum experience requirement is {cap} years or less.
- "0-2 years", "1-3 years", "2-3 years", "3 years", "3+ years", fresher,
  entry-level, graduate  -> eligible
- "4+ years", "3-6 years", "5 years", mid-level, senior, lead  -> tier MUST be
  "skip", fit MUST be 3 or lower, and say so in red_flags
Apply this even when the title sounds junior. Read what the JD actually asks for.
Do not stretch, do not rationalise, do not credit "she could grow into it".

Other rules:
- Score against what the candidate has ACTUALLY done. Never credit potential.
- Visual/brand/graphic design roles AT PRODUCT COMPANIES OR DESIGN STUDIOS are a
  legitimate route in for this candidate and should score well — do not penalise
  them for not being titled "Product Designer".
- Location is a hard constraint. Read it carefully. "Remote" with no country
  named is ambiguous, not automatically fine.
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


# Ways a JD states its experience bar. Each pattern's first group is the minimum.
YEARS_PATTERNS = [
    r"(\d+)\s*\+\s*(?:years?|yrs?)",                                   # "4+ years"
    r"(\d+)\s*(?:-|–|—|to)\s*\d+\s*(?:years?|yrs?)",                   # "2-4 years"
    r"(?:minimum|min\.?|at\s*least|atleast|over)\s*(?:of\s*)?(\d+)\s*(?:years?|yrs?)",
    r"(\d+)\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant\s+|professional\s+|proven\s+"
    r"|total\s+|hands[- ]on\s+|industry\s+)?experience",
]

FRESHER_HINTS = re.compile(
    r"fresher|entry[- ]level|no prior experience|0\s*(?:-|–|to)\s*[12]\s*(?:years?|yrs?)"
    r"|graduate program|campus hire|intern(?:ship)?\b",
    re.I,
)


def min_years_required(text):
    """Every distinct minimum-years figure the JD states, ascending.

    Returns [] when the posting never names a number — common, and not a reason
    to reject. A JD often states several bars ("2-4 years design experience",
    "5+ years leading teams"); the caller decides how strict to be, so this
    reports all of them rather than picking one.
    """
    found = set()
    for pattern in YEARS_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 <= n <= 30:  # ignore "10000 users", "2024", and similar noise
                found.add(n)
    return sorted(found)


def experience_gate(text, cap):
    """Reject only when the posting is unambiguously above the candidate's level.

    A definite reject means every experience figure in the JD is above the cap.
    If any figure is within reach — or the JD signals fresher/entry-level — the
    posting goes to the model, which makes the judgement call with the rule
    stated explicitly.
    """
    if FRESHER_HINTS.search(text):
        return None
    years = min_years_required(text)
    if years and min(years) > cap:
        return f"needs {min(years)}+ yrs (cap {cap})"
    return None


def load_search():
    cfg = json.loads(SEARCH.read_text())
    cfg["_inc_title"] = re.compile("|".join(cfg["title_include"]), re.I)
    cfg["_exc_title"] = re.compile("|".join(cfg["title_exclude"]), re.I)
    cfg["_inc_geo"] = re.compile("|".join(cfg["geo_include"]), re.I)
    cfg["_exc_geo"] = re.compile("|".join(cfg["geo_exclude"]), re.I)
    return cfg


def prefilter(job, cfg):
    """Return None to keep the job, or a string reason for dropping it."""
    title = job.get("title", "")
    if not cfg["_inc_title"].search(title):
        return "title not a design role"
    if cfg["_exc_title"].search(title):
        return "title excluded (seniority or wrong discipline)"

    loc = job.get("location", "") or ""
    if cfg["_exc_geo"].search(loc):
        # A multi-city posting survives only if one of HER cities is named.
        # "Mumbai, Bengaluru" stays; "Bengaluru, India" and "Remote - US" go.
        if not re.search(r"pune|mumbai|thane|hyderabad|secunderabad|nagpur", loc, re.I):
            return f"location out of scope ({loc[:40]})"
    if not cfg["_inc_geo"].search(loc):
        return f"location not in scope ({loc[:40]})"
    return None


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
    system = SYSTEM.format(cap=cap)
    gated = 0

    for i, job in enumerate(todo, 1):
        desc = fetch_description(job)
        if not desc:
            print(f"  [{i}/{len(todo)}] {job['company'][:14]:<15} no description available, skipped")
            continue

        # Cheap deterministic reject before spending a model call.
        blocked = experience_gate(desc, cap)
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
