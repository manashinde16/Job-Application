#!/usr/bin/env python3
"""
Act on approvals typed into Telegram.

The daily run numbers each prepared application. Reply in the group with:

    /send 2        send that application's email, resume attached
    /skip 2        drop it, and stop offering it
    /list          show what is pending
    /replied 2     mark it as answered, which cancels its follow-ups

The leading slash is required in groups: Telegram's privacy mode means a bot
never receives plain group messages, only commands, mentions and replies.

Nothing is sent without one of those messages. The reply IS the approval — this
script only ever acts on an instruction a human typed, one application at a time.

    python3 scripts/approve.py            # process new commands, then exit
    python3 scripts/approve.py --watch    # keep polling (local use)

Run it on a short schedule (every 15 minutes is plenty) so an approval typed on a
phone is acted on without anyone opening a terminal.
"""

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mailer  # noqa: E402
from notify import Notifier, esc  # noqa: E402

DB = ROOT / "db"
PENDING = DB / "pending.json"
APPLIED = DB / "applied.json"
OFFSET = DB / "tg_offset.json"

# Leading slash optional, @botname suffix optional.
#
# Telegram bots default to privacy mode in groups, which means a plain "send 9"
# is never delivered to the bot at all — only messages starting with "/", ones
# that mention it, and replies to its own messages get through. So the slash form
# is the one that always works; the bare form is accepted too, for when privacy
# mode has been disabled via BotFather.
COMMAND_RE = re.compile(
    r"^\s*/?(send|skip|replied|list|help)(?:@\w+)?\b\s*#?\s*(\d+)?", re.I
)


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def get_updates(note, offset, long_poll=0):
    """New messages since the last one we handled.

    long_poll seconds > 0 asks Telegram to hold the connection open until a
    message arrives, so it is delivered the moment it is sent rather than on the
    next poll. That is the difference between ~1 second and a full poll interval.
    """
    import urllib.request
    url = (f"https://api.telegram.org/bot{note.token}/getUpdates"
           f"?timeout={int(long_poll)}&allowed_updates=%5B%22message%22%5D")
    if offset:
        url += f"&offset={offset}"
    try:
        with urllib.request.urlopen(url, timeout=int(long_poll) + 25) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001 — a polling failure is not fatal
        print(f"  getUpdates failed: {type(e).__name__}: {e}")
        return []
    if not data.get("ok"):
        print(f"  getUpdates rejected: {data.get('description')}")
        return []
    return data.get("result", [])


def describe_pending(pending, applied):
    if not pending:
        return "Nothing pending. The next run will queue new applications."
    lines = ["<b>Pending applications</b>", ""]
    for num, item in sorted(pending.items(), key=lambda kv: int(kv[0])):
        record = applied.get(item["key"]) or {}
        state = "sent " + record["sent_on"] if record.get("sent_on") else "not sent"
        lines.append(
            f"<b>#{num}</b> {esc(item['company'])} — {esc(item['title'])}  "
            f"<i>({state})</i>"
        )
    lines += ["", "Reply <code>/send 1</code> to send one, "
                  "or <code>/skip 1</code> to drop it."]
    return "\n".join(lines)


HELP = (
    "<b>Commands</b>\n\n"
    "<code>/list</code> — what is pending\n"
    "<code>/send 2</code> — send that application's email, resume attached\n"
    "<code>/skip 2</code> — drop it\n"
    "<code>/replied 2</code> — mark answered, cancels its follow-ups\n\n"
    "<i>The leading slash matters in groups: without it Telegram never "
    "delivers the message to the bot.</i>"
)


def handle(command, number, pending, applied, note):
    """Run one typed instruction. Returns the reply text."""
    if command == "help":
        return HELP
    if command == "list":
        return describe_pending(pending, applied)

    if number is None:
        return "Which one? Try <code>/list</code>, then <code>/send 2</code>."

    item = pending.get(str(number))
    if not item:
        return (f"No pending application #{number}. "
                f"Send <code>/list</code> to see the current numbers.")

    key = item["key"]
    record = applied.setdefault(key, {})
    label = f"{item['company']} — {item['title']}"

    if command == "skip":
        record["skipped"] = True
        record["skipped_on"] = date.today().isoformat()
        pending.pop(str(number), None)
        return f"Skipped <b>{esc(label)}</b>. It won't be offered again."

    if command == "replied":
        record["replied"] = True
        return (f"Marked <b>{esc(label)}</b> as replied. "
                f"Follow-ups for it are cancelled.")

    # command == "send"
    if record.get("sent_on"):
        return (f"Already sent on {record['sent_on']} — not sending again. "
                f"Use <code>/replied {number}</code> if they answered.")

    to, subject, body = item.get("to"), item.get("subject"), item.get("body")
    if not (to and subject and body):
        return f"Draft for #{number} is incomplete — nothing was sent."

    attachments = [p for p in (item.get("resume"),) if p and Path(p).exists()]
    try:
        result = mailer.send(to, subject, body, attachments=attachments,
                             reply_to=item.get("reply_to"))
    except mailer.MailError as e:
        return f"<b>Not sent.</b>\n\n{esc(str(e))}"

    if mailer.dry_run():
        return f"<b>{esc(label)}</b>\n\n{esc(result)}"

    record.update({
        "company": item["company"],
        "title": item["title"],
        "url": item.get("url"),
        "sent_on": date.today().isoformat(),
        "sent_to": to,
        "followups_sent": record.get("followups_sent") or [],
        "replied": False,
    })
    pending.pop(str(number), None)
    return (f"<b>Sent</b> — {esc(label)}\n"
            f"to {esc(to)}\n\n"
            f"<i>Follow-up reminders will appear on day 4 and day 11 "
            f"unless you mark it <code>/replied {number}</code>.</i>")


def commit_state():
    """Persist state so a cloud run and this watcher agree on what was sent."""
    import subprocess
    try:
        if not subprocess.run(["git", "status", "--porcelain", "db"], cwd=ROOT,
                              capture_output=True, text=True, timeout=30).stdout.strip():
            return
        subprocess.run(["git", "add", "db"], cwd=ROOT, capture_output=True, timeout=30)
        subprocess.run(
            ["git", "-c", "user.name=job-agent",
             "-c", "user.email=job-agent@local", "commit", "-q", "-m",
             f"approvals {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())}"],
            cwd=ROOT, capture_output=True, timeout=60)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=ROOT,
                       capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001 — a git failure must not stop the watcher
        print(f"  commit skipped: {type(e).__name__}")


def process_once(note, long_poll=0):
    pending = load_json(PENDING, {})
    applied = load_json(APPLIED, {})
    state = load_json(OFFSET, {})
    offset = state.get("offset")

    updates = get_updates(note, offset, long_poll)
    if not updates:
        return 0

    handled, highest = 0, offset or 0
    for update in updates:
        highest = max(highest, update.get("update_id", 0) + 1)
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id", ""))

        # Only obey the configured chat. Anyone else messaging the bot is ignored.
        if note.chat_id and chat_id != str(note.chat_id):
            continue

        match = COMMAND_RE.match(text)
        if not match:
            continue

        command = match.group(1).lower()
        number = int(match.group(2)) if match.group(2) else None
        who = (message.get("from") or {}).get("first_name", "someone")
        print(f"  {who}: {command} {number if number is not None else ''}".rstrip())

        reply = handle(command, number, pending, applied, note)
        note.send(reply)
        handled += 1

    save_json(PENDING, pending)
    save_json(APPLIED, applied)
    save_json(OFFSET, {"offset": highest})
    if handled:
        commit_state()
    return handled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="keep polling every 20s")
    args = ap.parse_args()

    note = Notifier()
    if not note.enabled:
        sys.exit("Telegram is not configured — set TELEGRAM_BOT_TOKEN and "
                 "TELEGRAM_CHAT_ID (see SETUP.md)")

    mode = "DRY RUN (MAIL_DRY_RUN is set)" if mailer.dry_run() else (
        "live" if mailer.configured() else "no SMTP credentials — sends will fail")
    print(f"approve.py — mail mode: {mode}")

    if not args.watch:
        n = process_once(note)
        print(f"{n} command(s) handled")
        return

    # Long poll: Telegram holds the request open until something arrives, so a
    # typed command is acted on in about a second. No busy-waiting either — the
    # process is idle in a blocking read between messages.
    print("watching (long poll, ~1s response), ctrl-c to stop")
    while True:
        try:
            process_once(note, long_poll=50)
        except KeyboardInterrupt:
            print("\nstopped")
            return
        except Exception as e:  # noqa: BLE001 — never let one bad poll end the watch
            print(f"  poll error: {type(e).__name__}: {e}; retrying in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
