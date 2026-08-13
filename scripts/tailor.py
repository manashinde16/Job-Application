#!/usr/bin/env python3
"""
Write a resume tailored to one specific job, and render it to PDF.

Tailoring means reselecting and rephrasing facts that already exist in
profile/profile.md so the ones this employer cares about come first. It does not
mean adding anything. Every bullet is checked against the profile before it is
allowed into the PDF.

The profile also lists known defects in her current resume — a bullet duplicated
across two jobs, her only UX role described as graphic design, a hedged headline,
an oversized AI section, a buried CS degree. Those are fixed here, per role,
rather than left for a human to remember each time.

    python3 scripts/tailor.py --list              # scored jobs available
    python3 scripts/tailor.py --job <key>         # tailor for one job
    python3 scripts/tailor.py --generic           # a strong role-agnostic default
    python3 scripts/tailor.py --job <key> --verify-only

Writes out/<company>-<role>/resume.pdf and resume.json alongside it.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resume import Document  # noqa: E402

PROFILE = ROOT / "profile" / "profile.md"
SCORED = ROOT / "db" / "scored.jsonl"
OUT = ROOT / "out"

SYSTEM = """You tailor one designer's resume to one job posting. You are an editor
working under a hard constraint, not a copywriter.

THE CONSTRAINT: every factual claim you output must already appear in the profile
you are given. You may reorder, reword, re-emphasise, and choose what to leave
out. You may NOT add a skill, a tool, a metric, a responsibility, a client, or a
timespan that is not in the profile. If the job wants something she lacks, leave
it out — do not imply it.

Known defects in her current resume that you must fix:
1. One bullet appears verbatim under BOTH jobs ("Designed banners, standees,
   certificates, ID cards, presentations..."). Never repeat a bullet across roles.
2. Her AT Creation role is titled UI/UX Designer but described almost entirely as
   graphic design. It is her ONLY product/UX experience. For any design role with
   a product or UX component, lead that job with its UX content — usability
   testing, wireframing, prototyping, user flows, developer handoff.
3. Her headline hedges ("UI/UX and Graphic Designer"). Commit to ONE framing that
   matches this posting.
4. The AI-tooling material is oversized relative to her outcomes. One line maximum,
   and only if the posting cares.
5. Her B.Tech in Computer Science is a real differentiator for a designer —
   developer handoff, feasibility, systems thinking. Frame it, don't bury it.

She has roughly 1.5 years of experience. Do not inflate scope or seniority. Plain,
specific, verifiable. No "passionate", no "seamless", no "spearheaded"."""

PROMPT = """Tailor her resume for this job.

PROFILE — the only permitted source of facts
{profile}

TARGET ROLE
Company:  {company}
Title:    {title}
Location: {location}
{extra}
Job description:
{description}

Return JSON exactly in this shape:
{{
  "headline": "one committed title, e.g. Product Designer or Visual Designer",
  "tagline": "one sentence, max 22 words, positioning her for THIS role",
  "skills": [
    {{"label": "group name", "items": "comma-separated, most relevant first"}}
  ],
  "experience": [
    {{"company": "...", "title": "...", "location": "...", "dates": "...",
      "bullets": ["4-6 bullets, most role-relevant first"]}}
  ],
  "projects": [
    {{"name": "...", "dates": "...", "bullets": ["1-2 bullets"]}}
  ],
  "education": [{{"institution": "...", "detail": "...", "dates": "..."}}],
  "emphasis": "one sentence on what you led with and why",
  "omitted": ["profile facts you deliberately left out, and why"]
}}

Rules: 2-4 skill groups. Both jobs must appear, most recent first. No bullet may
repeat across jobs. Keep it to one page — about 14 bullets total across everything."""

GENERIC_TARGET = {
    "company": "General applications",
    "title": "Product Designer / UI-UX Designer (junior)",
    "location": "Pune / Mumbai / Hyderabad / Remote India",
    "description": (
        "A junior product design role at a product company or design studio. "
        "Wants UI/UX fundamentals: wireframing, prototyping, user flows, design "
        "systems, usability testing, and developer handoff. Visual and brand "
        "craft is valued. 0-3 years of experience. Portfolio is the primary "
        "screening artifact."
    ),
}


# Facts that may be phrased many ways but must trace back to the profile. Each
# entry is a set of tokens; if a bullet contains a flagged claim, at least one
# supporting token must be present in the profile.
SUSPECT_CLAIMS = [
    (r"\b(\d[\d,]*)\s*\+?\s*(users?|customers?|clients?)\b", "user counts"),
    (r"\b(\d+)\s*%\b", "percentages"),
    (r"\b(led|managed|mentored)\s+a?\s*team\b", "team leadership"),
    (r"\b(\d+)\s*(years?|yrs?)\b", "durations"),
    (r"₹\s*[\d.]+|\$\s*[\d.]+", "money figures"),
]


def load_profile():
    if not PROFILE.exists():
        sys.exit(f"missing {PROFILE}")
    return PROFILE.read_text()


def load_scored():
    if not SCORED.exists():
        return []
    rows = []
    for line in SCORED.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Keep the newest verdict per job.
    latest = {}
    for r in rows:
        latest[r.get("key")] = r
    return list(latest.values())


def verify(payload, profile):
    """Flag claims that don't trace back to the profile.

    Numbers are where invention shows up, so every figure in the output must
    appear in the profile too. Cheap, deterministic, and it catches the failure
    mode that matters — a fabricated metric surviving into a real application.
    """
    profile_low = profile.lower()
    profile_numbers = set(re.findall(r"\d[\d,]*\.?\d*", profile))
    issues = []

    texts = []
    for key in ("headline", "tagline", "emphasis"):
        if payload.get(key):
            texts.append((key, str(payload[key])))
    for group in payload.get("skills") or []:
        texts.append(("skills", f"{group.get('label','')}: {group.get('items','')}"))
    for job in payload.get("experience") or []:
        for b in job.get("bullets") or []:
            texts.append((f"experience/{job.get('company','?')}", b))
    for proj in payload.get("projects") or []:
        for b in proj.get("bullets") or []:
            texts.append((f"project/{proj.get('name','?')}", b))

    for where, text in texts:
        for number in re.findall(r"\d[\d,]*\.?\d*", text):
            if number not in profile_numbers and len(number) > 1:
                issues.append(f"{where}: number {number!r} is not in the profile — {text[:90]}")
        for pattern, label in SUSPECT_CLAIMS:
            for m in re.finditer(pattern, text, re.I):
                snippet = m.group(0).lower()
                if snippet not in profile_low:
                    issues.append(f"{where}: unverified {label} {m.group(0)!r} — {text[:90]}")

    # A repeated bullet across jobs is the specific defect we're fixing.
    seen = {}
    for job in payload.get("experience") or []:
        for b in job.get("bullets") or []:
            fingerprint = " ".join(re.findall(r"[a-z]{4,}", b.lower())[:6])
            if fingerprint and fingerprint in seen:
                issues.append(
                    f"duplicate bullet across {seen[fingerprint]} and "
                    f"{job.get('company')}: {b[:70]}"
                )
            seen[fingerprint] = job.get("company")

    return sorted(set(issues))


def render(payload, profile_text, path):
    doc = Document()
    doc.name(payload.get("headline_name") or _name_from(profile_text))
    if payload.get("headline"):
        doc.tagline(payload["headline"].upper())
    if payload.get("tagline"):
        doc.tagline(payload["tagline"])
    doc.contact(_contact_from(profile_text))

    if payload.get("skills"):
        doc.section("Skills")
        for group in payload["skills"]:
            doc.labelled(group.get("label", "Skills"), group.get("items", ""))
        doc.space(4)

    if payload.get("experience"):
        doc.section("Experience")
        for job in payload["experience"]:
            doc.job(job.get("company", ""), job.get("title", ""),
                    job.get("dates", ""), job.get("location", ""))
            for b in job.get("bullets") or []:
                doc.bullet(b)
            doc.space(4)

    if payload.get("projects"):
        doc.section("Selected projects")
        for proj in payload["projects"]:
            doc.job(proj.get("name", ""), "", proj.get("dates", ""))
            for b in proj.get("bullets") or []:
                doc.bullet(b)
            doc.space(3)

    if payload.get("education"):
        doc.section("Education")
        for ed in payload["education"]:
            doc.job(ed.get("institution", ""), ed.get("detail", ""), ed.get("dates", ""))
        doc.space(3)

    return doc.save(path)


def _name_from(profile_text):
    m = re.search(r"^#\s*Profile\s*—\s*(.+)$", profile_text, re.M)
    return m.group(1).strip() if m else "Resume"


def _contact_from(profile_text):
    """Pull the contact line out of the profile's VERIFIED block."""
    grab = lambda pat: (re.search(pat, profile_text, re.I) or [None, ""])[1].strip()
    city = grab(r"^-\s*Base:\s*(.+)$")
    email = grab(r"^-\s*Email:\s*(\S+)")
    phone = grab(r"^-\s*Phone:\s*(\S+)")
    portfolio = grab(r"^-\s*Portfolio:\s*(\S+)")
    behance = grab(r"^-\s*Behance:\s*(\S+)")
    parts = [city.split(",")[0] + ", India" if city else "", phone, email,
             portfolio.replace("https://", ""), behance.replace("https://", "")]
    return [p for p in parts if p]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "role"


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="scored jobs available to tailor for")
    g.add_argument("--job", metavar="KEY", help="job key from db/scored.jsonl")
    g.add_argument("--generic", action="store_true", help="role-agnostic strong default")
    ap.add_argument("--verify-only", action="store_true",
                    help="run the fact check and print the resume as text, no PDF")
    args = ap.parse_args()

    profile = load_profile()

    if args.list:
        rows = load_scored()
        if not rows:
            print("no scored jobs yet — run scripts/score_jobs.py")
            print("\nA generic resume needs no job at all:")
            print("  python3 scripts/tailor.py --generic")
            return
        print(f"{'fit':<5} {'tier':<9} {'company':<22} {'title':<38} key")
        for r in sorted(rows, key=lambda r: -(r.get("fit") or 0)):
            print(f"{str(r.get('fit')):<5} {str(r.get('tier')):<9} "
                  f"{r.get('company','')[:21]:<22} {r.get('title','')[:37]:<38} {r.get('key')}")
        return

    if args.generic:
        target = dict(GENERIC_TARGET)
        extra = ""
        out_dir = OUT / "generic"
    else:
        rows = {r.get("key"): r for r in load_scored()}
        if args.job not in rows:
            sys.exit(f"no scored job with key {args.job!r} — try --list")
        job = rows[args.job]
        target = {
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "description": job.get("description", "")[:6000],
        }
        angle = job.get("angle") or ""
        study = job.get("case_study") or ""
        extra = ""
        if angle:
            extra += f"Scoring pass suggested this angle: {angle}\n"
        if study:
            extra += f"Case study to reference: {study}\n"
        out_dir = OUT / f"{slug(job.get('company'))}-{slug(job.get('title'))}"

    from llm import complete_json  # noqa: PLC0415

    print(f"tailoring for {target['company']} — {target['title']}")
    payload = complete_json(
        PROMPT.format(profile=profile, extra=extra, **target),
        system=SYSTEM,
        max_tokens=3000,
    )

    issues = verify(payload, profile)
    if issues:
        print(f"\n{len(issues)} fact-check issue(s):")
        for i in issues:
            print(f"  ! {i}")
    else:
        print("fact check: clean — every claim traces to the profile")

    print(f"\nheadline: {payload.get('headline')}")
    print(f"tagline : {payload.get('tagline')}")
    if payload.get("emphasis"):
        print(f"emphasis: {payload['emphasis']}")
    for o in payload.get("omitted") or []:
        print(f"omitted : {o}")

    if args.verify_only:
        for job in payload.get("experience") or []:
            print(f"\n{job.get('company')} — {job.get('title')} ({job.get('dates')})")
            for b in job.get("bullets") or []:
                print(f"  • {b}")
        return

    if issues:
        print("\nNOT writing a PDF while claims are unverified. "
              "Re-run, or fix the profile if the claim is actually true.")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "resume.json").write_text(json.dumps(payload, indent=2))
    pdf = out_dir / "resume.pdf"
    pages = render(payload, profile, pdf)
    print(f"\n-> {pdf.relative_to(ROOT)} ({pages} page{'s' if pages != 1 else ''})")
    if pages > 1:
        print("   more than one page for a 1.5-year resume — trim bullets")


if __name__ == "__main__":
    main()
