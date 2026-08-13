"""
Send one prepared application email over SMTP, resume attached. Pure stdlib.

This is the only place in the agent that sends anything to a third party, and it
runs exactly once per explicit human approval — a "send <n>" reply in Telegram.
Nothing here is triggered by a schedule.

Two guards that matter:

  DRY_RUN — set MAIL_DRY_RUN=1 and it builds the message, prints it, and stops.
            The default for anything untested.
  ONE SHOT — the caller records the send in db/applied.json before returning, so
            a repeated approval can be refused rather than mailing twice.

Gmail needs an app password, not the account password:
  https://myaccount.google.com/apppasswords

  SMTP_USER=her.address@gmail.com
  SMTP_PASS=<16-character app password>
  SMTP_FROM_NAME=Ananya Saini      (optional)
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from env import load_env

load_env()

HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
PORT = int(os.environ.get("SMTP_PORT", "587"))


class MailError(RuntimeError):
    pass


def configured():
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"))


def dry_run():
    return os.environ.get("MAIL_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def build(to, subject, body, attachments=(), reply_to=None):
    """Compose the message. Plain text only — HTML mail from an unknown sender
    lands in spam far more often, and a cold application should look like a
    person typed it."""
    user = os.environ.get("SMTP_USER", "")
    name = os.environ.get("SMTP_FROM_NAME", "").strip()

    msg = EmailMessage()
    msg["From"] = formataddr((name, user)) if name else user
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=user.split("@")[-1] or None)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    for item in attachments:
        # Either a path, or (path, "name recipient sees.pdf"). The filename shows
        # up in the recipient's inbox, so "Ananya Saini - Resume.pdf" beats
        # "resume.pdf" sitting among fifty other resume.pdf files.
        path, shown = item if isinstance(item, (tuple, list)) else (item, None)
        path = Path(path)
        if not path.exists():
            continue
        subtype = "pdf" if path.suffix.lower() == ".pdf" else "octet-stream"
        msg.add_attachment(
            path.read_bytes(),
            maintype="application",
            subtype=subtype,
            filename=shown or path.name,
        )
    return msg


def _attachment_names(attachments):
    """Display names, tolerating both plain paths and (path, shown) pairs."""
    names = []
    for item in attachments:
        path, shown = item if isinstance(item, (tuple, list)) else (item, None)
        names.append(shown or Path(path).name)
    return ", ".join(names) or "none"


def send(to, subject, body, attachments=(), reply_to=None):
    """Send it. Returns a short human-readable description of what happened."""
    if not to or "@" not in to:
        raise MailError(f"refusing to send: {to!r} is not an address")
    if not subject.strip() or not body.strip():
        raise MailError("refusing to send an empty subject or body")

    msg = build(to, subject, body, attachments, reply_to)

    if dry_run():
        attached = _attachment_names(attachments)
        return (f"DRY RUN — not sent. To {to}, subject {subject!r}, "
                f"{len(body.split())} words, attachments: {attached}")

    if not configured():
        raise MailError(
            "SMTP_USER and SMTP_PASS are not set.\n"
            "Gmail needs an app password (not the account password): "
            "https://myaccount.google.com/apppasswords\n"
            "Then: gh secret set SMTP_USER / SMTP_PASS on the repo, "
            "or add them to ~/.job-agent.env"
        )

    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"].replace(" ", "")  # Google shows it spaced

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(HOST, PORT, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(
            "SMTP login rejected. With Gmail this almost always means the value "
            "in SMTP_PASS is the account password rather than a 16-character app "
            f"password. ({e.smtp_code})"
        ) from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"send failed: {type(e).__name__}: {e}") from e

    return f"sent to {to} — attachments: {_attachment_names(attachments)}"
