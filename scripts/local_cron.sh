#!/bin/bash
# Entry point for the launchd timers. One script, two modes.
#
#   local_cron.sh daily      the 10:30 job search, scoring, drafting and digest
#   local_cron.sh approvals  pick up any /send typed in Telegram
#
# launchd gives a job almost no environment — no PATH, no shell profile — so the
# interpreter is referenced absolutely and secrets are read from
# ~/.job-agent.env by env.py rather than expected in the environment.

set -uo pipefail

REPO="/Users/manas/Claude/job-agent"
PY="/Users/manas/.pyenv/versions/3.13.12/bin/python3"
LOG="$REPO/db/local_cron.log"
MODE="${1:-daily}"

cd "$REPO" || exit 1
mkdir -p "$REPO/db"

# Fetch state written by a cloud run first. Acting on a stale applied.json is how
# the same application gets emailed twice.
git pull -q --rebase --autostash origin main >/dev/null 2>&1 || true

# Keep the log from growing without bound.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 2000000 ]; then
  tail -c 500000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

echo "" >> "$LOG"
echo "=========== $MODE  $(date '+%Y-%m-%d %H:%M:%S %Z') ===========" >> "$LOG"

case "$MODE" in
  daily)
    "$PY" run.py >> "$LOG" 2>&1
    ;;
  approvals)
    "$PY" scripts/approve.py >> "$LOG" 2>&1
    ;;
  *)
    echo "unknown mode: $MODE" >> "$LOG"
    exit 2
    ;;
esac

STATUS=$?
echo "--- $MODE exited $STATUS ---" >> "$LOG"

# Commit whatever state changed, so the record survives and matches the repo.
# Never fail the timer over a git problem — the digest already went out.
if [ -n "$(git status --porcelain db out 2>/dev/null)" ]; then
  git add db out >/dev/null 2>&1
  git -c user.name="job-agent" -c user.email="job-agent@local" \
      commit -q -m "$MODE run $(date -u +%Y-%m-%dT%H:%MZ)" >> "$LOG" 2>&1
  git push -q origin main >> "$LOG" 2>&1 || echo "push failed (offline?)" >> "$LOG"
fi

exit $STATUS
