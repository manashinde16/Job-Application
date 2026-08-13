# Intake — fill this in once

This file is the ceiling on everything the agent produces. A tailored resume can
only reselect facts that exist here. Vague input here means generic output there,
and generic output is exactly what gets ignored.

Budget 60–90 minutes. It is the highest-leverage hour in the whole project.

Nothing here is ever invented or embellished by the agent. If it isn't written
down here, it does not appear in an application.

---

## 1. Basics

- Full name:
- Email (the one she'll apply from):
- Phone:
- Current city:
- LinkedIn URL:
- GitHub / portfolio / other:

## 2. Current situation

- Current company and title:
- Total years of experience:
- Notice period (days):
- Current CTC (fixed + variable):
- Expected CTC (a range is fine):
- Currently interviewing anywhere? (y/n):

## 3. What she's looking for

- Target titles (list 3–5 exact strings, e.g. "Backend Engineer", "SDE II"):
- Seniority band she'll accept (e.g. "SDE II to Senior, not Lead"):
- Locations: (Bengaluru / Hyderabad / Pune / NCR / Mumbai / remote-India / remote-global)
- Onsite / hybrid / remote — and is she willing to relocate?
- Work authorization (Indian citizen, needs visa sponsorship for X, etc.):
- Company sizes she wants (seed / growth startup / midsize / big tech / service company):
- **Hard nos** — things that should auto-reject a job:
  (e.g. no service companies, no on-call rotations, no crypto, no sub-₹X CTC,
   no relocation outside these cities, no 6-day weeks)

## 4. Skills — be specific, no filler

Split honestly. Recruiters screen on the first list; the third list is a liability.

- **Strong** (could be interviewed on tomorrow, no prep):
- **Working** (used in production, would need a weekend of revision):
- **Exposure only** (touched it once — will NOT be claimed on any resume):

## 5. Achievement bank — the important part

Write **15–30 entries.** Not resume bullets. Raw facts, ugly is fine.

Each one needs: what she did, what changed because of it, and a number. Any
number — latency, ₹, users, hours saved, team size, tickets, %, uptime, rows.
If she genuinely can't find a number, write the scope instead ("owned it alone",
"for 40 internal users", "across 3 teams").

Format:

```
### <short label>
- Context:     what was broken / what the goal was
- Did:         what SHE specifically did (not the team — her)
- Tech:        languages, frameworks, infra, tools
- Result:      the number, or the scope
- Timeframe:   e.g. 2024 Q3, ~4 months
```

Two filled examples so the shape is clear:

```
### Cut checkout API latency
- Context:   checkout p99 was 2.4s, ~8% cart drop-off blamed on it
- Did:       profiled the endpoint, found an N+1 across 3 tables, added a
             composite index and a Redis read-through cache
- Tech:      Python, Django, PostgreSQL, Redis, Datadog
- Result:    p99 2.4s -> 310ms, cart drop-off fell to 5.1%
- Timeframe: 2024 Q3, ~6 weeks

### Onboarded the payments vendor migration
- Context:   old PSP was deprecating its v1 API in 90 days, hard deadline
- Did:       owned the integration end to end, wrote the dual-write layer and
             the reconciliation job, ran the phased cutover
- Tech:      Node.js, TypeScript, AWS SQS, Terraform
- Result:    migrated ~₹40 Cr/month of volume with zero failed settlements
- Timeframe: 2023 Oct–Dec
```

Now hers:

```
### 
- Context:
- Did:
- Tech:
- Result:
- Timeframe:
```

(repeat — aim for 15 minimum)

## 6. Story answers

The agent reuses these verbatim. Write them once, in her own voice, not ChatGPT's.

- Why is she leaving her current job? (2–3 sentences, no bitterness — this
  exact text ends up in emails and screening calls)
- What does she want to be doing more of?
- What does she want to stop doing?
- 3-sentence version of "tell me about yourself":

## 7. Screening answers

Every portal asks these. Answer once, reuse forever.

- Willing to relocate?
- Earliest start date:
- Are you legally authorized to work in <country>?
- Do you require sponsorship?
- How did you hear about us? (default: "company careers page")
- Gender / ethnicity / veteran / disability (EEO — optional everywhere, say
  "prefer not to disclose" if she'd rather not answer):
- Anything she wants disclosed proactively (career gap, notice period concern):

## 8. Reference material

- Paste her current resume text below, or drop the PDF at
  `profile/current-resume.pdf` and tell the agent it's there.

```
<resume text here>
```
