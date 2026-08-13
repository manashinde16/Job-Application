# Portfolio audit — Ananya Saini

Read from source on 2026-08-13: the three UX case studies on Behance, opened as
images and read slide by slide, plus the portfolio site at
ananya-portfolio-nu.vercel.app.

This corrects an earlier assessment made from the portfolio site alone. The
portfolio site shows one-line summaries, so the case studies looked thin. They
are not thin. Some of them are good. The problems are different from what the
site suggests, and more fixable.

Ordered by urgency.

---

## 1. URGENT — Foodbank is published with template placeholder text

https://www.behance.net/gallery/202746803/FOODBANK-DONATION-APP-DESIGN
Published 8 July 2024. Linked from her portfolio site as project 07.

This is a bought or downloaded case-study template that was never filled in. The
visual design was replaced; the writing was not. Live on her public profile:

- Problem statement reads: *"The design of **[Product]** looks increasingly
  outdated when compared to its competitors"* — the literal `[Product]`
  placeholder token, three times on one slide
- Under it: *"List major problems here."* followed by five bullets of
  *"Lorem ipsum dolor sit amet, consectetur adipiscing elit ut quis"*
- Proposed Solution: *"List major solutions here."* plus five more lorem ipsum
  bullets
- User Research slide: a "10+ Users" badge, a question card reading
  *"Why would you use a donation app?"* — answered with lorem ipsum
- Information Architecture: real top-level nodes (Splash, Onboarding, Sign Up /
  Sign In, Email, Password, Google Sign Up, Location, Landing Page, then
  Donation List, Wallet, Home, Transaction History, Settings) — but **every
  single leaf node under all five sections reads "Lorem ipsum"**
- Flow chart: same, real spine and lorem ipsum leaves

**Why this is the most urgent item.** A recruiter who opens this sees lorem ipsum
inside a document labelled "case study". That is worse than having no third case
study at all — it reads as work submitted without being read. And it sits one
click from her portfolio's front page.

**The UI screens themselves are real and decent.** The Foodbank app screens carry
genuine content — "Fresh Food for Improving lives in Africa", $540,000 raised,
80% progress, 8 hours left, a working search and category structure. The design
work happened. Only the narrative is placeholder.

Two honest fixes, either is fine:
- **Fast (today):** relabel it. Move it from "case study" to "UI design" or
  "interface exploration" and cut the placeholder narrative slides entirely.
  Keep the screens. This is truthful — the screens are the deliverable — and it
  removes the damage in under an hour.
- **Better (a few hours):** write the four narrative slides properly. The IA and
  flow spines already exist; they need real leaf labels instead of lorem ipsum.

Until one of those is done, **this project should not be linked from anything
sent to an employer.**

---

## 2. Systematic defect — the personas don't match the products

This is one habit showing up across projects, which makes it a single fix rather
than three.

### Learnara — persona is from a recruiting product

https://www.behance.net/gallery/202736011/Learnara-UIUX-Case-Study

Persona: "Cameron Williamson, 27, Masters in Business, Sales Manager, Sydney,
single, extrovert, tech-savvy." Recorded core needs:

- "Need to find people with similar skills that can help her tackle company goals"
- "View all her hirings in an overview"
- "The price of the service is very important"

Frustrations: finding "perfect people from past work relations, family, friends
and within my circle", which is "tedious".

Those are hiring-manager needs. This is an unedited stock persona — placeholder
name, stock photo, copy lifted from a recruitment-product example — dropped into
an e-learning case study. **The persona's goals contradict the product's purpose.**

### Bounceless — persona is a student developer, and its bio is hers

https://www.behance.net/gallery/202732003/Bouncless-UX-Case-Study

Bounceless is a B2B email-verification tool for marketing teams. Its persona's
goals are:

- "Complete at least one programming project each month to improve his coding
  skills and build his portfolio"
- "Access to high-quality education in computer science and related fields"
- "Mentors and role models who can provide guidance on pursuing a career in
  technology"

Pain points: "Balancing academic responsibilities with extracurricular
activities", "Struggling to find mentors or role models who share his background".

A student looking for mentors is not the buyer of a B2B email verification tool.
The pronouns also switch between "his" and "she" mid-card.

Worse, the persona's bio paragraph is **Ananya's own professional summary**:
*"As a skilled graphic and UI/UX designer, I bring a unique blend of creativity
and technical expertise to my work. With a strong command of Adobe Illustrator,
Adobe XD, and Adobe Photoshop..."* — pasted into the persona card.

**Fix, once, for both:** rewrite each persona around the actual user named in
that project's own problem statement. Learnara's problem statement already names
its user — a student juggling recorded courses, instructor-led training,
assignment submission and instructor contact. Bounceless needs a marketer or ops
person who owns list quality and gets billed for bounces. Roughly 30 minutes each,
and it converts the weakest slide in each study into a coherent one.

This is the highest-value fix in the whole portfolio. Personas are where reviewers
look to see whether a designer thinks about users or decorates screens.

---

## 3. Bounceless — factual errors in the competitor analysis

The competitor matrix compares ZeroBounce, SendGrid, GetResponse and ConvertKit
across Marketing, Reports & Analytics, Customer Support and Bounce Management.
The matrix itself is a good idea and well presented. The written analysis under it
has two problems:

- **Only two unique analyses across four competitors.** ZeroBounce and
  GetResponse carry word-for-word identical strengths and weaknesses. SendGrid
  and ConvertKit likewise. Copy-pasted and not differentiated.
- **The ZeroBounce description is wrong.** It is credited with "Marketing
  Automation" and "Conversion Funnels: provides a tool for creating sales and
  conversion funnels". ZeroBounce is an email *validation* service — that
  description belongs to GetResponse. An interviewer in this space spots it
  immediately, and the whole study is about email verification, so it is the one
  competitor she most needed to get right.

Also: the Ideate phase is labelled *"Duration: 15 April 2024 - 22 April 2024
(3 Week)"*. That range is one week.

---

## 4. Her portfolio site undersells the work

The site shows a one-line summary per project and an "OPEN CASE STUDY" button.
The real substance — problem statements, competitive matrices, personas, user
flows, design systems, phase plans with dates — is all on Behance.

So a recruiter on her site sees eight taglines. A recruiter on Behance sees
actual process. **The site is hiding her best evidence.**

Also: **three projects exist on Behance that are not on her portfolio at all** —
a Travel and Tourism App UX Case Study, Dynaboard, and a Subscription Management
Mobile UI. The travel one is a full UX case study and may be stronger than
Foodbank in its current state.

---

## What's actually good — worth saying plainly

The earlier read of this portfolio was too harsh, and it was wrong about the cause.

Bounceless contains, fully written and with no placeholders: a problem statement,
a four-competitor feature matrix, a persona card with technology-proficiency
scales and the apps the user already lives in, a phased plan with real calendar
dates, and a user flow. Learnara contains a real problem and solution statement,
a four-way competitive analysis with audience/strengths/weaknesses/cost columns,
a user flow, and a design system.

That is a competent junior product designer's body of work. It is being
undermined by three specific, fixable things — placeholder text in one project,
mismatched personas in two, and a portfolio site that shows none of it.

**Total remediation: roughly one focused day.** Not the two weeks estimated before
reading the source material.

## Priority order

1. Relabel or fix Foodbank so nothing with lorem ipsum is reachable from her
   portfolio — **today**, this one is actively costing her
2. Rewrite the Learnara and Bounceless personas to match their products — ~1 hour
3. Fix the ZeroBounce competitor entry and de-duplicate the other three — ~30 min
4. Surface the Behance case-study content on the portfolio site, or link straight
   to Behance from each project
5. Add the Travel & Tourism case study to the portfolio if it holds up
