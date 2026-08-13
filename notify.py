"""
Telegram delivery for the daily digest. Pure stdlib.

Telegram is the whole mobile interface: no app to install, works the same on iOS
and Android, and a bot token is free.

Deliberately no inline buttons. Callback buttons need a webhook server to receive
the tap, and a cron job has nowhere to receive it. Instead each job card carries a
pre-filled mailto: link — she taps it, her mail app opens with the subject and body
already in place, she reads it and presses send. Stateless, and the human still
presses send.

    export TELEGRAM_BOT_TOKEN=...   # from @BotFather
    export TELEGRAM_CHAT_ID=...     # from /getUpdates after messaging the bot

With no token set, everything is written to db/digest.md instead, so the pipeline
is testable end to end before any Telegram setup exists.
"""

import html as _html
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from env import load_env

load_env()

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # Telegram's hard cap is 4096; leave room for the chunk suffix

ROOT = Path(__file__).resolve().parent
FALLBACK = ROOT / "db" / "digest.md"


class Notifier:
    """Sends to Telegram when configured, otherwise appends to db/digest.md."""

    def __init__(self, token=None, chat_id=None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat_id)
        self._buffer = []

    # --- transport -----------------------------------------------------------

    def _call(self, method, payload):
        req = urllib.request.Request(
            API.format(token=self.token, method=method),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode())

    def _upload(self, path, caption=""):
        """sendDocument needs multipart/form-data, hand-rolled to stay stdlib."""
        path = Path(path)
        boundary = f"----jobagent{uuid.uuid4().hex}"
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        def part(name, value):
            return (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()

        body = bytearray()
        body += part("chat_id", self.chat_id)
        if caption:
            body += part("caption", caption[:1000])
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
            f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'
        ).encode()
        body += path.read_bytes()
        body += f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            API.format(token=self.token, method="sendDocument"),
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())

    # --- public api ----------------------------------------------------------

    def send(self, text, preview=False):
        """Send one message, splitting on paragraph boundaries if oversized."""
        self._buffer.append(text)
        if not self.enabled:
            return True

        ok = True
        for chunk in _split(text, LIMIT):
            try:
                self._call("sendMessage", {
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": not preview,
                })
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:200]
                print(f"  [telegram] {e.code}: {detail}")
                ok = False
            except Exception as e:  # noqa: BLE001 — a failed notification must not
                print(f"  [telegram] {type(e).__name__}: {e}")  # kill the pipeline
                ok = False
        return ok

    def document(self, path, caption=""):
        self._buffer.append(f"[attached: {Path(path).name}] {caption}")
        if not self.enabled:
            return True
        try:
            self._upload(path, caption)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"  [telegram] upload failed: {type(e).__name__}: {e}")
            return False

    def flush_fallback(self):
        """Write everything to db/digest.md — the no-Telegram path."""
        if not self._buffer:
            return None
        FALLBACK.parent.mkdir(parents=True, exist_ok=True)
        FALLBACK.write_text("\n\n---\n\n".join(self._buffer) + "\n")
        return FALLBACK


def _split(text, limit):
    """Split on blank lines, then on newlines, only cutting mid-line as a last resort."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            chunks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        current = block
    if current:
        chunks.append(current)
    return chunks


def esc(text):
    """Escape for Telegram's HTML parse mode."""
    return _html.escape(str(text or ""), quote=False)


def mailto(address, subject, body):
    """A tap-to-open draft link.

    Opens her mail app with subject and body pre-filled so the only remaining
    action is reading it and pressing send.
    """
    query = urllib.parse.urlencode(
        {"subject": subject or "", "body": body or ""}, quote_via=urllib.parse.quote
    )
    return f"mailto:{address}?{query}"
