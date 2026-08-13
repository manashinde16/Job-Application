"""
Load API keys and tokens from an env file.

Shared by llm.py and notify.py — either can be the first thing a script imports,
and both need the file loaded before they read os.environ. Keeping it in one place
means a script can't accidentally get half the configuration.

Looks outside the repo first (~/.job-agent.env), because keeping secrets out of the
working tree is safer than relying on .gitignore. Real environment variables always
win, so GitHub Actions secrets override anything on disk.
"""

import os

CANDIDATES = [
    os.path.expanduser("~/.job-agent.env"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
]

_loaded = False


def load_env(force=False):
    """Read the first env file found. Idempotent."""
    global _loaded
    if _loaded and not force:
        return
    _loaded = True
    for path in CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as fh:
                content = fh.read()
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip().strip("'\"")
            if name and name not in os.environ:
                os.environ[name] = value
