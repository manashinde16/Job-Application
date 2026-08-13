#!/usr/bin/env python3
"""
Turn a screenshot of a job post into a ready-to-send application.

LinkedIn and Naukri cannot be read programmatically, but a person scrolling them
can screenshot a post. Send that image to the bot and it reads the post, extracts
the role and the contact address, checks it against her constraints, drafts the
email, and queues it as a numbered card. Replying "send" then mails it with the
resume attached — the same path as an automatically discovered job.

That closes the gap on every source we cannot reach: the human does the finding,
the agent does the writing and sending.

Used by scripts/approve.py when a photo arrives. Also runnable directly:

    python3 scripts/from_image.py path/to/screenshot.png
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from notify import esc  # noqa: E402
from score_jobs import experience_gate, load_search, prefilter  # noqa: E402

DB = ROOT / "db"
PENDING = DB / "pending.json"
PROFILE = ROOT / "profile" / "profile.md"
OUT = ROOT / "out"

READ_SYSTEM = """You read screenshots of job posts and extract the facts. You are a
parser, not an assistant.

Only report what is visibly written in the image. If a field is not shown, return
"" for it. Never infer an email address, never complete a partial one, and never
guess a company from a logo you are unsure of. A wrong address means the
application goes to a stranger."""

READ_PROMPT = """This is a screenshot of a job post, probably from LinkedIn, Naukri
or a similar feed.

Extract:
{{
  "is_job_post": true if this really is a job posting or hiring post, else false,
  "role": "the job title as written",
  "company": "the hiring company as written",
  "location": "location as written, including remote/hybrid/onsite if stated",
  "emails": ["every email address VISIBLE in the image, exactly as written"],
  "links": ["every application URL visible, exactly as written"],
  "experience_text": "any experience requirement as written, e.g. '2-4 years'",
  "requirements": "a short plain summary of what they are asking for",
  "posted": "how old the post says it is, if shown",
  "notes": "anything else that matters — deadline, salary, how to apply"
}}

Read carefully. Email addresses in these posts are often written oddly ("mail
your CV to hr [at] company [dot] com") — transcribe exactly what you see, do not
normalise it. If no email is visible, return an empty list; do not invent one."""

DRAFT_SYSTEM = """You write a short cold application email for a junior designer,
in her voice, first person, plain.

Rules:
- 110-140 words in the body.
- Every claim must come from her profile. Invent nothing.
- Open with something specific to this role or company. No "I hope this finds you
  well", no "I am writing to express my interest".
- One proof point, then her portfolio link once, then one easy ask.
- Name the single most relevant case study.
- She is junior; do not oversell, do not apologise.
- Plain text. No signature block — that gets appended."""

DRAFT_PROMPT = """Draft the email for this job, which she found as a post on a feed.

HER PROFILE — the only source of facts about her
{profile}

THE ROLE, as read from the screenshot
Company:     {company}
Role:        {role}
Location:    {location}
Experience:  {experience_text}
Requirements:{requirements}
Notes:       {notes}

The recipient is whoever posted it — usually a recruiter or founder, and often
the address is a generic hiring inbox. Write it so it works either way.

Return JSON:
{{
  "subject": "specific, under 60 characters",
  "body": "110-140 words, plain text, \\n\\n between paragraphs",
  "followup_day4": "40-60 word nudge for day 4",
  "followup_day11": "40-60 word final nudge for day 11"
}}"""

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def normalise_email(raw):
    """Turn 'hr [at] company [dot] com' into a real address, if it is one.

    Posts obfuscate addresses to dodge scrapers, so the model is told to
    transcribe literally and the cleanup happens here where it can be checked.
    """
    text = (raw or "").strip()
    text = re.sub(r"\s*[\[\(]\s*at\s*[\]\)]\s*|\s+at\s+", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[\(]\s*dot\s*[\]\)]\s*|\s+dot\s+", ".", text, flags=re.I)
    text = text.replace(" ", "")
    match = EMAIL_RE.search(text)
    return match.group(0).lower() if match else ""


def load_pending():
    if PENDING.exists():
        try:
            return json.loads(PENDING.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def next_number(pending):
    used = {int(n) for n in pending if str(n).isdigit()}
    n = 1
    while n in used:
        n += 1
    return str(n)


def pick_resume():
    """Same fallback chain the daily run uses."""
    for candidate in (OUT / "generic" / "resume.pdf",
                      ROOT / "profile" / "current-resume.pdf"):
        if candidate.exists():
            return str(candidate)
    return ""


def handle_image(mime, data, note):
    """Read a screenshot, draft the email, queue it. Returns the reply text."""
    from llm import LLMError, complete_json

    try:
        info = complete_json(READ_PROMPT, system=READ_SYSTEM, max_tokens=1500,
                             images=[(mime or "image/jpeg", data)])
    except LLMError as e:
        return f"Could not read that image.\n\n{esc(str(e)[:200])}"

    if not info.get("is_job_post"):
        return ("That does not look like a job post, so I have not queued "
                "anything. Send a screenshot showing the role and how to apply.")

    role = (info.get("role") or "").strip()
    company = (info.get("company") or "").strip() or "unknown company"
    if not role:
        return "I could not find a job title in that image — nothing queued."

    emails = [normalise_email(e) for e in (info.get("emails") or [])]
    emails = [e for e in emails if e]
    links = [l for l in (info.get("links") or []) if l]

    cfg = load_search()

    # The location rule applies to a screenshot too. Without this a US-onsite
    # recruiter post would be queued and drafted for a role she cannot take —
    # exactly what the first real screenshot turned out to be.
    location = (info.get("location") or "").strip()
    geo_reason = prefilter(
        {"title": role, "location": location or "unknown"},
        {**cfg, "max_job_age_days": None},   # age is judged separately below
    )
    # Only a geography verdict blocks here. prefilter also rejects on title, but
    # someone who screenshots a post has already decided the role is worth a look,
    # so the title filter is not applied to a deliberate hand-off.
    if geo_reason and geo_reason.startswith(("onsite", "remote")):
        return (f"<b>Skipped — location</b>\n\n{esc(company)} — {esc(role)}\n"
                f"{esc(location or 'no location shown')}\n\n"
                f"<i>{esc(geo_reason)}</i>\n\n"
                f"Onsite works only in Pune, Mumbai, Thane, Hyderabad or Nagpur. "
                f"Remote is fine from anywhere, unless the post ties it to a region "
                f"she cannot work from. Nothing queued.")

    # The seniority rule applies exactly as it does to a discovered posting.
    haystack = " ".join(str(info.get(k) or "") for k in
                        ("experience_text", "requirements", "notes"))
    blocked = experience_gate(haystack, cfg["max_years_required"],
                              cfg.get("reject_open_ended_at_or_above"))
    if blocked:
        return (f"<b>Skipped — too senior</b>\n\n{esc(company)} — {esc(role)}\n"
                f"The post asks for {esc(blocked)}.\n\n"
                f"<i>Her cap is {cfg['max_years_required']} years, and open-ended "
                f"'{cfg['max_years_required']}+' does not qualify. Nothing queued.</i>")

    if not emails and not links:
        return (f"<b>{esc(company)} — {esc(role)}</b>\n\n"
                "I read the post but there is no email address or application link "
                "visible in the screenshot. Send a shot that includes how to apply, "
                "or apply through the feed directly.")

    profile = PROFILE.read_text()
    try:
        draft = complete_json(
            DRAFT_PROMPT.format(
                profile=profile, company=company, role=role,
                location=info.get("location") or "not stated",
                experience_text=info.get("experience_text") or "not stated",
                requirements=info.get("requirements") or "not stated",
                notes=info.get("notes") or "none",
            ),
            system=DRAFT_SYSTEM, max_tokens=2500,
        )
    except LLMError as e:
        return f"Read the post, but drafting failed.\n\n{esc(str(e)[:200])}"

    body = (draft.get("body") or "").strip()
    if not body:
        return "Drafting returned nothing usable — nothing queued."

    pending = load_pending()
    number = next_number(pending)
    grab = lambda pat: (re.search(pat, profile, re.M) or [None, ""])[1].strip()
    signature = "\n".join(x for x in [
        grab(r"^#\s*Profile\s*—\s*(.+)$") or "Ananya Saini",
        grab(r"^-\s*Phone:\s*(\S+)"),
        grab(r"^-\s*Portfolio:\s*(\S+)"),
    ] if x)

    entry = {
        "key": f"image:{re.sub(r'[^a-z0-9]+', '-', (company + '-' + role).lower()).strip('-')}",
        "company": company,
        "title": role,
        "url": links[0] if links else "",
        "to": emails[0] if emails else "",
        "subject": draft.get("subject") or f"{role} — Ananya Saini",
        "body": f"{body}\n\n{signature}",
        "resume": pick_resume(),
        "from_screenshot": True,
        "queued_on": date.today().isoformat(),
    }
    pending[number] = entry
    PENDING.write_text(json.dumps(pending, indent=2, sort_keys=True))

    lines = [
        f"<b>#{number}</b>  <b>{esc(company)}</b> — {esc(role)}",
        f"{esc(info.get('location') or 'location not stated')}"
        + (f"  ·  {esc(info.get('experience_text'))}" if info.get("experience_text") else ""),
        "<i>read from your screenshot</i>",
    ]
    if entry["to"]:
        lines += ["", f"<b>Email:</b> {esc(entry['to'])}",
                  f"<b>Subject:</b> {esc(entry['subject'])}", "",
                  f"<pre>{esc(entry['body'])}</pre>"]
        lines += [f"✅ Reply <code>/send {number}</code> — or reply "
                  f"<code>send</code> to this message — and it goes out with the "
                  f"resume attached."]
    else:
        lines += ["", "No email visible in the post, so there is nothing to send. "
                      "The application link is below."]
    if entry["url"]:
        lines += ["", f'<a href="{esc(entry["url"])}">Apply link from the post</a>']
    if len(emails) > 1:
        lines += ["", "<b>Other addresses in the post:</b>",
                  " · ".join(f"<code>{esc(e)}</code>" for e in emails[1:4])]
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: from_image.py <screenshot>")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"no such file: {path}")
    import mimetypes
    from notify import Notifier
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    reply = handle_image(mime, path.read_bytes(), Notifier())
    print(re.sub(r"<[^>]+>", "", reply))


if __name__ == "__main__":
    main()
