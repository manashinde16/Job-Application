# job-agent

Automates the grind of a job search: finds roles, scores them against one real
profile, tailors a resume per role, drafts an email to a named human, and tracks
follow-ups. A person still reviews and clicks send.

## Design rules

1. **The daily run costs nothing.** It's plain Python calling a free LLM tier
   (Gemini by default). Claude Code is used to *build* this, never to run it.
2. **Nothing is invented.** Every claim on every tailored resume traces back to
   `profile/profile.md`. The agent reorders and rephrases; it never adds.
3. **A human sends.** The agent prepares to the submit button and stops. Batched,
   so it's one 15-minute review session — not 12 interruptions.
4. **Public data only.** Company board APIs, the same endpoints their own careers
   pages call. No logged-in scraping, no automated portal accounts.
5. **Quality over volume.** Sub-threshold jobs are dropped, not applied to.
   12 strong applications beat 80 sprayed, and cost less energy.

## Status

| Stage | What it does | State |
|---|---|---|
| 0 | Provider layer — any one of 10 LLM backends, auto-detected | done (`llm.py`) |
| 1 | Discover via ATS — 6 providers, 86 boards, 9.5k roles | done (`scripts/fetch_jobs.py`) |
| 1a | Board discovery — work out which ATS a company uses | done (`scripts/find_boards.py`) |
| 1b | Discover via careers pages — model-extracted, any site | done (`scripts/fetch_careers.py`) |
| 1c | Discover via Instahyre / Naukri — her logged-in feed, read-only | todo |
| 2 | Score — prefilter, then rank each role against the profile | done (`scripts/score_jobs.py`) |
| 1d | Listings finder — locate the real listings URL / detect the ATS | done (`scripts/find_listings.py`) |
| 3 | Tailor — per-role resume, rendered to ATS-clean PDF | done (`scripts/tailor.py`, `resume.py`) |
| 3a | Case-study interviewer — asks her, writes the study | done (`scripts/interview.py`) |
| 4 | Contact — find the design lead / recruiter | done (`scripts/outreach.py`) |
| 5 | Outreach — draft the email + day-4 / day-11 follow-ups | done (`scripts/outreach.py`) |
| 6 | Telegram + GitHub Actions — daily run, approve from a phone | done (`run.py`, `notify.py`, `.github/workflows/daily.yml`) |
| 7 | Track — applied log, day-4 and day-11 follow-ups | done (`run.py`, `db/applied.json`) |

Every stage is built and exercised end to end against live data. See
[SETUP.md](SETUP.md) to put it on a schedule — about 15 minutes, one time.

The whole daily run is one command:

```bash
python3 run.py
```

Coverage note: only ~16% of this candidate's target employers are on a public
ATS. Careers-page reading covers another chunk (23 of 46 pages readable by plain
fetch), and the rest need the browser channel — see `db/needs_browser.txt`.

## Setup

Needs Python 3.9+. No pip install — everything is stdlib.

Set **any one** API key. The provider is auto-detected from whichever key is
present, so nothing else needs configuring:

```bash
export GEMINI_API_KEY=...    # free: https://aistudio.google.com/apikey
python3 llm.py               # prints the chosen provider, then OK
```

| Key | Provider | Cost |
|---|---|---|
| `GEMINI_API_KEY` | Gemini | free tier |
| `GROQ_API_KEY` | Groq | free tier |
| `CEREBRAS_API_KEY` | Cerebras | free tier |
| `OPENROUTER_API_KEY` | OpenRouter | 300+ models, some free |
| `DEEPSEEK_API_KEY` | DeepSeek | paid, cheap |
| `MISTRAL_API_KEY` | Mistral | paid |
| `XAI_API_KEY` | Grok | paid |
| `OPENAI_API_KEY` | GPT | paid |
| `ANTHROPIC_API_KEY` | Claude | paid |
| `TOGETHER_API_KEY` | Together | paid |
| *(none)* | Ollama, local | free — `ollama serve` |

Free tiers are preferred over paid ones when both keys exist, so the agent never
spends money it doesn't have to. Override with `LLM_PROVIDER`, and pick a
specific model with `LLM_MODEL`.

Put the export in `~/.zshrc` so scheduled runs pick it up.

## Run

```bash
python3 scripts/fetch_jobs.py
```

Reads `targets/companies.txt`, writes `db/jobs.jsonl` (append-only history) and
`db/jobs_new.jsonl` (this run's new roles only). Re-running is safe — jobs
already seen are skipped, so each run only surfaces genuinely new postings.

## Before stage 2 works

Fill in `profile/INTAKE.md`. Scoring has nothing to compare against until then,
and the achievement bank in section 5 sets the quality ceiling for every resume
and email the agent will ever write.
