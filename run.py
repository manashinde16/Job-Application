#!/usr/bin/env python3
"""
The daily run. One command, no arguments — this is the whole agent.

    python3 run.py
    python3 run.py --dry-run          # discovery + scoring only, no drafts
    python3 run.py --max-drafts 5

Sequence:
  1. discover   — ATS boards, then careers listings pages
  2. score      — regex prefilter, then the model on what survives
  3. prepare    — for each role worth applying to: tailored resume PDF, contact
                  search, drafted email, follow-up dates
  4. notify     — one Telegram card per role with a tap-to-send Gmail compose link,
                  plus the resume attached
  5. follow up  — surface any application due a day-4 or day-11 nudge

Designed for an unattended cron run, so nothing here raises on a single failure —
one bad board or one failed draft must not cost the whole morning's digest.

Free-tier quota is finite, so model calls go to the highest-scoring roles first.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from notify import Notifier, esc, gmail_compose  # noqa: E402

DB = ROOT / "db"
OUT = ROOT / "out"
SCORED = DB / "scored.jsonl"
APPLIED = DB / "applied.json"
PENDING = DB / "pending.json"
STATE = DB / "state.json"
RUN_LOCK = DB / "run.lock"

FOLLOWUP_DAYS = (4, 11)


def run_step(name, args, timeout=1800):
    """Run a pipeline script, capture output, never raise."""
    print(f"\n{'=' * 62}\n{name}\n{'=' * 62}")
    try:
        proc = subprocess.run(
            [sys.executable, *args], cwd=ROOT, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        print(f"  timed out after {timeout}s")
        return False, ""
    output = (proc.stdout or "") + (proc.stderr or "")
    print(output.strip()[-2500:])
    return proc.returncode == 0, output


def load_scored():
    if not SCORED.exists():
        return {}
    latest = {}
    for line in SCORED.read_text().splitlines():
        if line.strip():
            try:
                row = json.loads(line)
                latest[row.get("key")] = row
            except json.JSONDecodeError:
                continue
    return latest


def load_applied():
    if APPLIED.exists():
        try:
            return json.loads(APPLIED.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_applied(state):
    DB.mkdir(parents=True, exist_ok=True)
    APPLIED.write_text(json.dumps(state, indent=2, sort_keys=True))


def slug(text):
    import re
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "role"


def job_dir(job):
    return OUT / f"{slug(job.get('company'))}-{slug(job.get('title'))}"


# Tailoring can fail: the model refuses, a claim fails the fact check, or the
# quota is spent. An application must still go out with a resume attached, so
# fall back to the best available — the generic tailored one first, then her
# original CV. Never send with nothing attached.
GENERIC_RESUME = OUT / "generic" / "resume.pdf"
BASELINE_RESUME = ROOT / "profile" / "current-resume.pdf"


def pick_resume(tailored, tailored_ok):
    """Best resume available for this application, and why."""
    if tailored_ok and tailored.exists():
        return tailored
    for fallback, label in ((GENERIC_RESUME, "generic tailored"),
                            (BASELINE_RESUME, "her original CV")):
        if fallback.exists():
            print(f"  tailoring unavailable — falling back to {label}")
            return fallback
    print("  no resume available at all — the card will go out without one")
    return None


def parse_email_md(path):
    """Pull the recipient, subject and body back out of a drafted email.md."""
    import re
    text = path.read_text()
    to = (re.search(r"^- To: `([^`]+)`", text, re.M) or [None, ""])[1]
    subject = (re.search(r"^## Subject\n\n(.+)$", text, re.M) or [None, ""])[1]
    body = (re.search(r"## Body\n\n(.*?)\n\n## Follow-ups", text, re.S) or [None, ""])[1]
    return to.strip(), subject.strip(), body.strip()


def card(job, prepared, number=None):
    """One Telegram message for one role."""
    fit = job.get("fit")
    tag = f"<b>#{number}</b>  " if number is not None else ""
    lines = [
        f"{tag}<b>{esc(job.get('company'))}</b> — {esc(job.get('title'))}",
        f"{esc(job.get('location'))}  ·  fit <b>{fit}/10</b>  ·  {esc(job.get('tier'))}",
        "",
        esc(job.get("why") or ""),
    ]
    if job.get("angle"):
        lines += ["", f"<b>Angle:</b> {esc(job['angle'])}"]

    lines += ["", f'<a href="{esc(job.get("apply_url") or job.get("url"))}">Apply on the posting</a>']

    if prepared.get("email"):
        to, subject, body = prepared["email"]
        if to and "TBD" not in to:
            # Two routes on purpose.
            #
            # The Gmail compose URL pre-fills everything, but only in a real
            # browser: Telegram's in-app browser loads Gmail's mobile web view,
            # which ignores ?view=cm and just shows the inbox. Works on desktop,
            # and on mobile once the in-app browser is turned off.
            #
            # So the address and body are also given as <code>, which Telegram
            # renders as tap-to-copy. That path works on every client regardless
            # of browser settings.
            link = gmail_compose(to, subject, body)
            lines += [
                "",
                f'➡️ <a href="{esc(link)}">TAP HERE — opens Gmail, already filled in</a>',
                "",
                "<b>To</b> (tap to copy)",
                f"<code>{esc(to)}</code>",
                "<b>Subject</b> (tap to copy)",
                f"<code>{esc(subject)}</code>",
                "<b>Body</b> (tap to copy)",
                f"<pre>{esc(body)}</pre>",
            ]
            if number is not None:
                lines += [
                    f"✅ Or just reply <code>/send {number}</code> and I'll send it "
                    f"for you, resume attached — no Gmail needed.",
                    f"<i>Reply <code>/skip {number}</code> to drop it.</i>",
                ]
            else:
                lines.append("<i>Check it still sounds like you, then press send.</i>")
    if prepared.get("contacts"):
        lines += ["", "<b>Other contacts:</b>"]
        for c in prepared["contacts"][:4]:
            who = f"{c.get('name')} — {c.get('role')}" if c.get("name") else c.get("role", "")
            lines.append(f"· {esc(c['email'])}  <i>{esc(who)} ({esc(c['confidence'])})</i>")
    return "\n".join(lines)


def due_followups(applied, today):
    due = []
    for key, record in applied.items():
        sent = record.get("sent_on")
        if not sent or record.get("replied"):
            continue
        try:
            sent_date = datetime.strptime(sent, "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - sent_date).days
        for day in FOLLOWUP_DAYS:
            if age == day and day not in (record.get("followups_sent") or []):
                due.append((key, record, day))
    return due


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="discover and score only; no resumes, no drafts")
    ap.add_argument("--max-drafts", type=int, default=6,
                    help="cap prepared applications per run (protects free quota)")
    ap.add_argument("--min-fit", type=int, default=6,
                    help="only prepare roles at or above this fit score")
    ap.add_argument("--skip-discovery", action="store_true",
                    help="reuse what's already in the db")
    args = ap.parse_args()

    started = time.time()
    today = date.today()
    note = Notifier()

    # /pause has to stop the SCHEDULED run too, not just manual ones — otherwise
    # pausing from Telegram would be silently ignored at 10:30 tomorrow.
    if STATE.exists():
        try:
            if json.loads(STATE.read_text()).get("paused"):
                print("paused via /pause — exiting without searching")
                note.send("Daily search skipped: the agent is <b>paused</b>. "
                          "Send <code>/resume</code> to turn it back on.")
                RUN_LOCK.unlink(missing_ok=True)
                return
        except json.JSONDecodeError:
            pass
    print(f"job-agent daily run — {today}")
    print(f"telegram: {'configured' if note.enabled else 'not configured, writing db/digest.md'}")

    if not args.skip_discovery:
        run_step("1/4  discover — ATS boards", ["scripts/fetch_jobs.py"])
        run_step("1/4  discover — careers listings pages", ["scripts/fetch_careers.py"])
        run_step("1/4  discover — global remote boards", ["scripts/fetch_remote.py"])
        run_step("1/4  discover — LinkedIn (public jobs-guest)", ["scripts/fetch_linkedin.py"])
        run_step("1/4  discover — Instahyre", ["scripts/fetch_instahyre.py"])

    run_step("2/4  score", ["scripts/score_jobs.py"])

    scored = load_scored()
    applied = load_applied()

    fresh = [
        job for key, job in scored.items()
        if (job.get("fit") or 0) >= args.min_fit
        and job.get("years_ok") is not False
        and key not in applied
    ]
    fresh.sort(key=lambda j: -(j.get("fit") or 0))
    shortlist = fresh[: args.max_drafts]

    print(f"\n{'=' * 62}\n3/4  prepare\n{'=' * 62}")
    print(f"{len(scored)} scored total, {len(fresh)} new at fit >= {args.min_fit}, "
          f"preparing {len(shortlist)}")
    if len(fresh) > len(shortlist):
        print(f"  deferring {len(fresh) - len(shortlist)} to a later run (quota)")

    prepared = {}
    pending = {}
    if not args.dry_run:
        for job in shortlist:
            key = job["key"]
            print(f"\n--- {job.get('company')} / {job.get('title')}")
            entry = {}

            ok, _ = run_step("resume", ["scripts/tailor.py", "--job", key], timeout=600)
            entry["resume"] = pick_resume(job_dir(job) / "resume.pdf", ok)

            ok, _ = run_step("outreach", ["scripts/outreach.py", "--job", key], timeout=900)
            email_md = job_dir(job) / "email.md"
            if ok and email_md.exists():
                entry["email"] = parse_email_md(email_md)
            contacts_json = job_dir(job) / "contacts.json"
            if contacts_json.exists():
                try:
                    entry["contacts"] = json.loads(contacts_json.read_text()).get("candidates", [])
                except json.JSONDecodeError:
                    pass

            prepared[key] = entry
            # Numbered so an approval can name it: "send 2" in the group.
            number = str(len(pending) + 1)
            to, subject, body = entry.get("email") or ("", "", "")
            pending[number] = {
                "key": key,
                "company": job.get("company"),
                "title": job.get("title"),
                "url": job.get("apply_url") or job.get("url"),
                "to": to,
                "subject": subject,
                "body": body,
                "resume": str(entry["resume"]) if entry.get("resume") else "",
            }
            applied[key] = {
                "company": job.get("company"),
                "title": job.get("title"),
                "url": job.get("url"),
                "fit": job.get("fit"),
                "prepared_on": today.isoformat(),
                "sent_on": None,
                "followups_sent": [],
                "replied": False,
            }

    print(f"\n{'=' * 62}\n4/4  notify\n{'=' * 62}")

    header = [
        f"<b>job-agent · {today.strftime('%d %b %Y')}</b>",
        "",
        f"{len(scored)} role{'s' if len(scored) != 1 else ''} scored · "
        f"{len(fresh)} new worth applying to"
        + (f" · {len(shortlist)} prepared" if not args.dry_run else " · dry run"),
    ]
    followups = due_followups(applied, today)
    if followups:
        header.append(f"{len(followups)} follow-up(s) due today")
    if not fresh:
        header += ["", "Nothing new cleared the bar today. The junior design market "
                       "in Pune / Mumbai / Hyderabad is thin — this is normal."]
    note.send("\n".join(header))

    number_of = {item["key"]: num for num, item in pending.items()}
    for job in shortlist:
        entry = prepared.get(job["key"], {})
        number = number_of.get(job["key"])
        result = note.send(card(job, entry, number), preview=False)
        # Recording the message id lets someone reply to a card and type "send"
        # instead of remembering which number it was — with twenty cards a
        # morning, replying is the natural way to point at one.
        if number and isinstance(result, int):
            pending[number]["message_id"] = result
        if entry.get("resume"):
            generic = Path(entry["resume"]).parent.name in ("generic", "profile")
            label = "general resume (tailoring unavailable)" if generic else "tailored resume"
            note.document(entry["resume"],
                          f"{job.get('company')} — {job.get('title')} · {label}")

    for key, record, day in followups:
        email_md = OUT / f"{slug(record.get('company'))}-{slug(record.get('title'))}" / "email.md"
        text = ""
        if email_md.exists():
            import re
            pat = rf"\*\*Day {day}\*\* — (.*?)(?=\n\n\*\*|\n\n## |\Z)"
            m = re.search(pat, email_md.read_text(), re.S)
            text = m.group(1).strip() if m else ""
        note.send("\n".join([
            f"<b>Follow-up due — day {day}</b>",
            f"{esc(record.get('company'))} — {esc(record.get('title'))}",
            f"<i>applied {record.get('sent_on')}</i>",
            "",
            esc(text) or "(draft not found — see out/ for the original email)",
        ]))
        record.setdefault("followups_sent", []).append(day)

    if not args.dry_run:
        save_applied(applied)
        PENDING.write_text(json.dumps(pending, indent=2, sort_keys=True))

    if not note.enabled:
        path = note.flush_fallback()
        if path:
            print(f"\ndigest written to {path.relative_to(ROOT)}")

    if pending:
        note.send(
            "Reply <code>/send 1</code> (or any number above) and I'll send that "
            "email for you with the resume attached.\n"
            "<code>/list</code> shows what's pending · <code>/skip 1</code> drops one."
        )

    RUN_LOCK.unlink(missing_ok=True)   # a /run can start again immediately
    print(f"\ndone in {time.time() - started:.0f}s")
    print("\nNothing was sent to anyone. Drafts wait for a typed 'send N' approval.")


if __name__ == "__main__":
    main()
