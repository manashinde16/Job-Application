#!/usr/bin/env python3
"""
Turn an undocumented portfolio project into a real case study.

The candidate has 8 projects and only one written up. For design hiring the
portfolio *is* the screen, so this is the highest-leverage gap in the whole
pipeline. An agent cannot invent a case study — the decisions, the research and
the trade-offs are hers, and a portfolio review will interrogate them for 45
minutes. So this ghostwrites instead: she supplies the facts, the model does the
structuring and the prose.

    python3 scripts/interview.py --list
    python3 scripts/interview.py --new learnara      # writes the question sheet
    ...she fills it in, on a phone if she likes...
    python3 scripts/interview.py --probe learnara    # model asks sharper follow-ups
    python3 scripts/interview.py --build learnara    # model writes the case study

Answers live in  profile/case_studies/<slug>.answers.md
Case study lands in profile/case_studies/<slug>.md

Anything she left blank comes out as [NEEDS INPUT] in the draft. It is never
filled in with a plausible guess — a fabricated metric that collapses in a
portfolio review is worse than a missing one.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STUDIES = ROOT / "profile" / "case_studies"
PROFILE = ROOT / "profile" / "profile.md"

# The questions a portfolio reviewer actually asks. Ordered so the story builds.
QUESTIONS = [
    ("project", "What is the project, and who was it for? "
                "(real client, self-initiated, college brief, redesign exercise)"),
    ("role", "What exactly was your role? Did anyone else work on it, and on what?"),
    ("timeline", "When was it, and roughly how long did it take?"),
    ("problem", "What problem were you solving, and whose problem was it? "
                "Describe the user, not the feature."),
    ("evidence", "How did you know it was a real problem? "
                 "(interviews, a survey, reviews you read, your own observation, "
                 "or an assumption — say so honestly if it was an assumption)"),
    ("constraints", "What constrained you? Time, budget, tech, an existing brand, "
                    "a client's opinion, your own skill gaps at the time?"),
    ("first_attempt", "What was your first attempt, and what was wrong with it?"),
    ("changes", "What changed between the first version and the final one, "
                "and what made you change it?"),
    ("testing", "Did anyone else use or review it? What did they say, "
                "and what did you change because of it?"),
    ("rejected", "What did you deliberately decide NOT to do, and why? "
                 "(this is the question that separates designers from decorators)"),
    ("outcome", "What was the outcome? Any number at all — users, screens shipped, "
                "time saved, client signed off, marks awarded. If there is no "
                "number, say who ended up using it."),
    ("reflection", "What would you do differently now, with what you know today?"),
]

SHEET_HEADER = """# Case study answers — {title}

Answer in your own words. Rough notes are fine — bad grammar is fine, fragments
are fine. Detail matters, polish does not; the writeup handles polish.

Two rules:
- If you don't remember or it didn't happen, write "n/a". Do not guess.
- Never invent a number. A made-up metric will be picked apart in a portfolio
  review, and that is much worse than having no metric.

Write your answer under each question, replacing the `>` line.

---
"""

PROBE_SYSTEM = """You are a design hiring manager reviewing a junior designer's case
study notes before a portfolio review. Your job is to find the weak spots that an
interviewer will press on.

Ask about missing specifics, unsupported claims, and decisions with no stated
reason. Be direct and concrete. Never ask something already answered. Never
suggest what the answer should be — that would put words in her mouth."""

PROBE_PROMPT = """Here are her notes for a case study.

{answers}

Identify the 4-6 places an interviewer would push hardest, and write one sharp
follow-up question for each. Prioritise:
- claims with no evidence behind them
- design decisions with no stated reason
- anything vague where a specific would be far stronger
- the absence of any outcome or validation

Return JSON: {{"questions": ["...", "..."]}}
Each question must be answerable from memory, and phrased plainly."""

BUILD_SYSTEM = """You write design case studies. You are a ghostwriter working
strictly from the designer's own notes.

Absolute rules:
- Use ONLY facts present in the notes. Invent nothing — no metrics, no research
  that didn't happen, no users who weren't consulted, no team members.
- Where the notes are empty or say n/a, write [NEEDS INPUT: <what is missing>].
  Never paper over a gap with generic filler.
- If she says something was an assumption, present it as an assumption. Honesty
  about method reads as maturity; a fake research phase reads as a liar.
- Plain, specific, first person. No "passionate", no "seamless experience",
  no "I embarked on a journey".
- She is a junior designer. Do not inflate scope. A small project described
  precisely beats a small project described grandly."""

BUILD_PROMPT = """Write a case study from these notes.

PROJECT NAME: {title}
Use this as the H1 exactly. It is the project's real name — do not mark it as
missing and do not invent an alternative.

CANDIDATE CONTEXT (for voice and honest level — do not import facts from here)
{profile}

NOTES FOR THIS PROJECT
{answers}

Structure it as markdown:

# <project name>
<one sentence: what it is and who it's for>

**Role** · **Timeline** · **Tools**

## The problem
## What I found out
## Constraints
## What I designed
(walk through the actual decisions — first attempt, what was wrong, what changed
and why. This is the section reviewers read.)
## What I chose not to do
## Outcome
## What I'd do differently

Target 500-700 words. Every claim traceable to the notes. Mark gaps as
[NEEDS INPUT: ...]."""


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def sheet_path(slug):
    return STUDIES / f"{slug}.answers.md"


def study_path(slug):
    return STUDIES / f"{slug}.md"


def cmd_new(slug, title):
    STUDIES.mkdir(parents=True, exist_ok=True)
    path = sheet_path(slug)
    if path.exists():
        sys.exit(f"{path.relative_to(ROOT)} already exists — edit it, or delete it first")
    body = [SHEET_HEADER.format(title=title)]
    for key, question in QUESTIONS:
        body.append(f"## {key}\n{question}\n\n> \n")
    path.write_text("\n".join(body))
    print(f"created {path.relative_to(ROOT)}")
    print(f"\n{len(QUESTIONS)} questions. Roughly 60-90 minutes to answer properly.")
    print(f"When done:  python3 scripts/interview.py --probe {slug}")


def parse_answers(slug):
    """Read the sheet into {key: answer}, keeping only answers she actually wrote."""
    path = sheet_path(slug)
    if not path.exists():
        sys.exit(f"no answer sheet at {path.relative_to(ROOT)} — run --new {slug} first")
    text = path.read_text()
    answers, blank = {}, []
    for key, question in QUESTIONS:
        m = re.search(rf"^## {re.escape(key)}\s*$(.*?)(?=^## |\Z)", text, re.S | re.M)
        if not m:
            blank.append(key)
            continue
        # Everything after the question line that isn't the unfilled "> " stub.
        lines = [
            re.sub(r"^>\s*", "", ln).strip()
            for ln in m.group(1).splitlines()
            if ln.strip() and ln.strip() != question.strip() and ln.strip() != ">"
        ]
        joined = " ".join(l for l in lines if l and l != question.strip()).strip()
        if joined:
            answers[key] = joined
        else:
            blank.append(key)
    return answers, blank


def render_answers(answers):
    return "\n\n".join(
        f"{key}: {answers[key]}" for key, _ in QUESTIONS if key in answers
    )


def cmd_probe(slug):
    from llm import complete_json

    answers, blank = parse_answers(slug)
    if len(answers) < 4:
        sys.exit(f"only {len(answers)} of {len(QUESTIONS)} answered — "
                 f"fill in more of {sheet_path(slug).relative_to(ROOT)} first")

    print(f"{len(answers)}/{len(QUESTIONS)} answered"
          + (f", still blank: {', '.join(blank)}" if blank else ""))
    print("\nasking the model where an interviewer would push...\n")

    result = complete_json(
        PROBE_PROMPT.format(answers=render_answers(answers)),
        system=PROBE_SYSTEM,
        max_tokens=1500,
    )
    questions = result.get("questions") or []
    if not questions:
        print("no follow-ups returned")
        return

    block = ["\n---\n\n# Follow-up questions\n",
             "These are the weak spots an interviewer will press on. "
             "Answer under each.\n"]
    for i, q in enumerate(questions, 1):
        print(f"  {i}. {q}")
        block.append(f"\n## followup_{i}\n{q}\n\n> \n")

    with sheet_path(slug).open("a") as fh:
        fh.write("\n".join(block))
    print(f"\nappended to {sheet_path(slug).relative_to(ROOT)}")
    print(f"Answer them, then:  python3 scripts/interview.py --build {slug}")


def cmd_build(slug):
    from llm import complete

    answers, blank = parse_answers(slug)
    if len(answers) < 6:
        sys.exit(f"only {len(answers)} of {len(QUESTIONS)} answered — "
                 "a case study built on this would be mostly [NEEDS INPUT]")

    # Follow-up answers are extra signal; fold them in if present.
    text = sheet_path(slug).read_text()
    extra = []
    for m in re.finditer(r"^## followup_\d+\s*$\n(.+?)\n+>\s*(.*?)(?=\n## |\Z)",
                         text, re.S | re.M):
        question, answer = m.group(1).strip(), m.group(2).strip()
        if answer:
            extra.append(f"{question}\n  -> {answer}")

    notes = render_answers(answers)
    if extra:
        notes += "\n\nFOLLOW-UPS\n" + "\n\n".join(extra)

    print(f"building from {len(answers)} answers"
          + (f" + {len(extra)} follow-ups" if extra else ""))
    if blank:
        print(f"blank sections will be marked [NEEDS INPUT]: {', '.join(blank)}")

    title_match = re.search(r"^# Case study answers — (.+)$", text, re.M)
    title = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()

    study = complete(
        BUILD_PROMPT.format(
            title=title, profile=PROFILE.read_text()[:4000], answers=notes
        ),
        system=BUILD_SYSTEM,
        max_tokens=3000,
    )
    study_path(slug).write_text(study.strip() + "\n")

    gaps = len(re.findall(r"\[NEEDS INPUT", study))
    print(f"\nwrote {study_path(slug).relative_to(ROOT)} ({len(study.split())} words)")
    if gaps:
        print(f"{gaps} gap(s) marked [NEEDS INPUT] — fill those in the sheet and rebuild")


def cmd_list():
    STUDIES.mkdir(parents=True, exist_ok=True)
    sheets = sorted(STUDIES.glob("*.answers.md"))
    if not sheets:
        print("no case studies started yet\n")
        print("Her portfolio has 7 undocumented projects. Best two to write up first,")
        print("because product roles screen on product thinking:\n")
        print("  python3 scripts/interview.py --new learnara --title Learnara")
        print("  python3 scripts/interview.py --new foodbank --title Foodbank")
        return
    print(f"{'slug':<22} {'answered':<12} {'follow-ups':<12} written")
    for sheet in sheets:
        slug = sheet.name.replace(".answers.md", "")
        answers, _ = parse_answers(slug)
        followups = len(re.findall(r"^## followup_\d+", sheet.read_text(), re.M))
        written = "yes" if study_path(slug).exists() else "-"
        print(f"{slug:<22} {len(answers)}/{len(QUESTIONS):<10} {followups:<12} {written}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--new", metavar="SLUG", help="create the question sheet")
    g.add_argument("--probe", metavar="SLUG", help="generate sharper follow-up questions")
    g.add_argument("--build", metavar="SLUG", help="write the case study from answers")
    g.add_argument("--list", action="store_true", help="status of all case studies")
    ap.add_argument("--title", help="display name for --new (defaults to the slug)")
    args = ap.parse_args()

    if args.list:
        cmd_list()
    elif args.new:
        slug = slugify(args.new)
        cmd_new(slug, args.title or args.new.title())
    elif args.probe:
        cmd_probe(slugify(args.probe))
    elif args.build:
        cmd_build(slugify(args.build))


if __name__ == "__main__":
    main()
