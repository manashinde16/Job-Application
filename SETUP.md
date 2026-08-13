# Setup — one time, about 15 minutes

Two people are involved. Ananya does part 2 on her phone; you do the rest.

After this, the agent runs itself every weekday at 10:30 IST and neither of you
touches a terminal again.

---

## 1. Put it in a PRIVATE GitHub repo

This repo holds her phone number, email, CV content and case-study notes. It must
be private. Never make it public and never fork it into a public org.

```bash
cd ~/Claude/job-agent
git init
git add .
git commit -m "job-agent: initial"
gh repo create ananya-job-agent --private --source=. --push
```

Confirm it's private before going further:

```bash
gh repo view ananya-job-agent --json isPrivate
```

## 2. Create the Telegram bot and group

The digest goes to a group containing both of you, so she can act on it and you
can see what's going out.

**Ananya, on her phone:**

1. Open Telegram, search **@BotFather**, send `/newbot`
2. Give it any name, then a username ending in `bot` (e.g. `ananya_jobs_bot`)
3. BotFather replies with a token like `8123456789:AAF...`. **Send that to you.**

**Then, either of you:**

4. Create a Telegram group with both of you in it
5. Add the bot to that group as a member
6. Send any message in the group, e.g. `hello`
7. Open this URL in a browser, replacing `<TOKEN>`:

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

8. Find `"chat":{"id":-1001234567890`. **That negative number is the chat ID** —
   group IDs are always negative. Copy it including the minus sign.

## 2b. Let the bot send the email for you (optional)

Each Telegram card is numbered. Reply <code>send 2</code> in the group and the
agent mails that application itself, resume attached — no Gmail, no browser.

It needs a Gmail **app password on the sending account**, which must be HER
account, so replies reach her inbox and the From address matches the signature.

1. Turn on 2-Step Verification for her Google account (app passwords are hidden
   without it — this is the usual reason the option seems missing)
2. https://myaccount.google.com/apppasswords while signed in as her → create one
3. Store it:

```bash
gh secret set SMTP_USER --repo <your-repo>    # her.address@gmail.com
gh secret set SMTP_PASS --repo <your-repo>    # the 16-character app password
gh secret set SMTP_FROM_NAME --repo <your-repo>   # Ananya Saini
```

Set `MAIL_DRY_RUN: "1"` in `.github/workflows/approvals.yml` to make `send`
report what it *would* mail without sending, which is worth doing on first setup.

## 3. Get a free model API key

You already have a Gemini key. If it ever runs out, any one of these works and the
agent auto-detects whichever is present:

| Key | Where | Cost |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com/apikey | free tier |
| `GROQ_API_KEY` | console.groq.com | free tier |
| `CEREBRAS_API_KEY` | cloud.cerebras.ai | free tier |
| `OPENROUTER_API_KEY` | openrouter.ai | some free models |

Gemini's free tier is **20 requests per day per model**, so the agent rotates
across 7 models for ~120/day. Adding a second provider's key raises the ceiling
further, and costs nothing.

## 4. Add the secrets to GitHub

```bash
gh secret set GEMINI_API_KEY      --repo ananya-job-agent
gh secret set TELEGRAM_BOT_TOKEN  --repo ananya-job-agent
gh secret set TELEGRAM_CHAT_ID    --repo ananya-job-agent
```

Each command prompts for the value. Nothing is echoed or committed.

## 5. Test it before trusting the schedule

```bash
gh workflow run "daily job run" --repo ananya-job-agent -f dry_run=true
gh run watch --repo ananya-job-agent
```

A dry run discovers and scores but writes no drafts, so it is safe and cheap. If
the digest arrives in the group, everything is wired.

Then run it properly once:

```bash
gh workflow run "daily job run" --repo ananya-job-agent -f max_drafts=3
```

From then on it fires automatically at 05:00 UTC (10:30 IST), Monday to Friday.

---

## Before the first real run: turn off Telegram's in-app browser

On her phone: **Telegram › Settings › Data and Storage › Browser › turn OFF
"In-app browser."**

Without this, tapping the Gmail link opens Telegram's own browser, which loads
Gmail's mobile web view. That view ignores compose parameters and just shows the
inbox — the draft appears empty. With it off, the link opens in Chrome or Safari,
which hands off to the Gmail app and the draft arrives filled in.

Every card also carries the address, subject and body as tap-to-copy blocks, so
there is a working path even if this setting is left on.

## Her daily routine

One Telegram message per role, each carrying:

- Company, title, location, fit score, and why it scored that way
- A link to the posting
- The full drafted email, readable in the message
- **A tap-to-open Gmail link with the email already filled in**
- The tailored resume as a PDF attachment
- Backup contacts if the first address bounces

She reads it, taps the Gmail link (or copies the three blocks), checks it still
sounds like her, presses send. Attaches the PDF from the same chat. About 10
minutes for the whole batch.

**Nothing is ever sent automatically.** The agent prepares; a human sends. That is
deliberate — a wrong auto-send to a company she wants is not recoverable, and one
tap costs her seconds.

Day-4 and day-11 follow-up reminders appear in the same group, with the text
already written. Follow-ups roughly double reply rate and are the first thing
people drop when they're tired, so the agent owns the remembering.

---

## How it actually runs

**GitHub Actions is authoritative.** Both workflows are live and verified:

| Workflow | Schedule | Does |
|---|---|---|
| `daily job run` | 05:00 UTC = 10:30 IST, Mon–Fri | discover, score, tailor, draft, post cards |
| `process approvals` | every 15 min, 09:30–20:00 IST | acts on any `/send` typed in Telegram |

```bash
gh run list --repo <repo> --limit 5        # recent runs
gh workflow run "daily job run" --repo <repo> --field dry_run=true
```

Nothing needs to be switched on locally — this runs on GitHub's machines.

## Optional local backup on a Mac (currently disabled)

Two launchd timers are installed but **unloaded**, because running both them and
GitHub Actions would post every digest twice and fight over git state. Enable them
only as a fallback if Actions is ever unavailable:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobagent.daily.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobagent.approvals.plist
```


| Timer | Schedule | Does |
|---|---|---|
| `com.jobagent.daily` | 10:30, Mon–Fri | full run: discover, score, tailor, draft, post cards |
| `com.jobagent.approvals` | every 3 minutes | picks up any `/send` typed in Telegram |

```bash
launchctl list | grep jobagent          # are they loaded?
tail -f db/local_cron.log               # watch a run happen
launchctl kickstart -k gui/$(id -u)/com.jobagent.daily     # force a run now
launchctl bootout gui/$(id -u)/com.jobagent.daily          # turn it off
```

Plists live in `~/Library/LaunchAgents/`. If the Mac is asleep at 10:30, launchd
runs the job when it wakes rather than skipping the day. The Mac does have to be
switched on at some point — for a run that happens whether any laptop is on, use
the GitHub Actions workflows instead (they need `gh auth refresh -s workflow` once).

## Running it manually

```bash
export GEMINI_API_KEY=...          # or put it in ~/.job-agent.env
python3 run.py --dry-run           # safe: discovery and scoring only
python3 run.py                     # the real thing
```

With no Telegram credentials the digest is written to `db/digest.md` instead, so
everything is testable before any Telegram setup exists.

## Knobs

| Flag | Does |
|---|---|
| `--dry-run` | Discover and score only. No model calls for drafts, no PDFs. |
| `--min-fit N` | Only prepare roles scoring N or above. Default 6. |
| `--max-drafts N` | Cap prepared applications per run. Default 6, protects quota. |
| `--skip-discovery` | Reuse the existing database. Useful when iterating. |

Search parameters — target titles, cities, the 3-year seniority cap — live in
`targets/search.json` and are edited by hand, no code change needed.

## If something breaks

| Symptom | Cause |
|---|---|
| Digest arrives but no jobs | Normal. The junior design market in her 3 cities is genuinely thin. |
| `daily quota spent, switching model` | Working as designed — model rotation. |
| All 7 models spent | Add a second provider key. Resets at midnight Pacific. |
| Telegram 400 "chat not found" | Chat ID wrong, or the bot was never added to the group. |
| Telegram 403 | The bot was removed from the group, or blocked. |
| A board reports HTTP 404 | Wrong slug in `targets/companies.txt`. Harmless — prune it. |
