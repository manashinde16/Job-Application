#!/usr/bin/env python3
"""
Finish Telegram setup: find the chat, store it, send a test message.

The one thing no script can do is tap "send" in Telegram for you — the bot only
learns a chat exists once a human messages it. So: do that one thing, then run
this, and it handles the rest.

    1. Add @Jobsapplierananya_bot to your Telegram group (or just message it)
    2. Send any message in that chat, e.g. "hello"
    3. python3 scripts/telegram_setup.py

It finds the chat ID, appends it to ~/.job-agent.env, uploads it as a GitHub
secret if gh is available, and sends a formatted test message so you can see
exactly what the daily digest will look like.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm  # noqa: E402,F401 — imported for its side effect of loading the env file
from notify import Notifier  # noqa: E402

ENV_FILE = Path.home() / ".job-agent.env"
REPO = os.environ.get("JOB_AGENT_REPO", "manashinde16/Job-Application")


def api(token, method):
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def discover_chats(token):
    """Every chat the bot currently knows about."""
    data = api(token, "getUpdates")
    if not data.get("ok"):
        return []
    chats = {}
    for update in data.get("result", []):
        for field in ("message", "edited_message", "channel_post",
                      "my_chat_member", "chat_member"):
            payload = update.get(field) or {}
            chat = payload.get("chat") or {}
            if chat.get("id"):
                chats[chat["id"]] = {
                    "id": chat["id"],
                    "type": chat.get("type", "?"),
                    "name": chat.get("title") or " ".join(
                        x for x in (chat.get("first_name"), chat.get("last_name")) if x
                    ) or str(chat["id"]),
                }
    # Prefer a group: that's where both people can see the digest.
    return sorted(chats.values(),
                  key=lambda c: (0 if c["type"] in ("group", "supergroup") else 1,
                                 str(c["name"])))


def write_env(chat_id):
    lines = []
    if ENV_FILE.exists():
        lines = [
            ln for ln in ENV_FILE.read_text().splitlines()
            if not ln.startswith("TELEGRAM_CHAT_ID=")
        ]
    lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def set_github_secret(chat_id):
    try:
        subprocess.run(
            ["gh", "secret", "set", "TELEGRAM_CHAT_ID", "--repo", REPO,
             "--body", str(chat_id)],
            check=True, capture_output=True, text=True, timeout=90,
        )
        return True, ""
    except FileNotFoundError:
        return False, "gh not installed"
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or "").strip()[:160]
    except subprocess.TimeoutExpired:
        return False, "gh timed out"


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set — put it in ~/.job-agent.env")

    try:
        me = api(token, "getMe")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"cannot reach Telegram: {e}")
    if not me.get("ok"):
        sys.exit(f"token rejected: {me.get('description')}")
    username = me["result"]["username"]
    print(f"bot: @{username}")

    # Poll briefly — this is usually run seconds after the message is sent.
    chats = []
    for attempt in range(6):
        chats = discover_chats(token)
        if chats:
            break
        if attempt == 0:
            print(f"\nNo chat found yet. In Telegram:")
            print(f"  1. Add @{username} to your group (or open t.me/{username})")
            print(f"  2. Send any message there, e.g. hello")
            print(f"\nwaiting", end="", flush=True)
        print(".", end="", flush=True)
        time.sleep(5)

    if not chats:
        print("\n\nStill nothing. Two things block this:")
        print("  - Telegram only reveals a group chat AFTER a message is sent in it")
        print("  - If the bot has privacy mode on, send it /start or mention it by name")
        sys.exit(1)

    print(f"\n\n{len(chats)} chat(s) found:")
    for c in chats:
        print(f"  {c['id']:>16}  {c['type']:<11} {c['name']}")

    chosen = chats[0]
    kind = "group" if chosen["type"] in ("group", "supergroup") else chosen["type"]
    print(f"\nusing: {chosen['name']} ({kind}, id {chosen['id']})")

    write_env(chosen["id"])
    print(f"saved to {ENV_FILE}")

    ok, err = set_github_secret(chosen["id"])
    print(f"github secret: {'set' if ok else 'NOT set — ' + err}")

    os.environ["TELEGRAM_CHAT_ID"] = str(chosen["id"])
    note = Notifier()
    sent = note.send("\n".join([
        "<b>job-agent is connected</b>",
        "",
        "This is where the daily digest lands, every weekday at 10:30 IST.",
        "",
        "Each morning you'll get one message per role worth applying to:",
        "· the company, title, location and a fit score",
        "· why it scored that way",
        "· a drafted email you can read here",
        "· a tap-to-open Gmail link with the email already filled in",
        "· the tailored resume as a PDF",
        "",
        "<i>Nothing is ever sent automatically. You read it and press send.</i>",
    ]))
    print(f"test message: {'delivered' if sent else 'FAILED — see the error above'}")

    if sent:
        print("\nSetup complete. Check your Telegram.")


if __name__ == "__main__":
    main()
