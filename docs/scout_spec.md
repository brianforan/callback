# Scout: one engine, strict matching, the whole catalog

Status: spec, built 2026-11-06 in one pass (chunks 1 to 4). Chunk 5 is not built. Credits
replaced by a daily run cap, and settings moved to `app_config`, 2026-11-09 (built).

Called SmartSearch until 2026-11-09, when it was renamed Scout everywhere but the database: the
tables `smartsearch_settings`, `smartsearch_runs` and `smartsearch_briefs`, their constraint
names, the `app_config` keys `smartsearch_*`, and the posting and application source
`SmartSearch` keep the old name. `/smartsearch` links in the app redirect to `/scout`.

This replaces the three engines (Agent, Pipeline, Companies) with one. It keeps the Companies
engine's shape (catalog first, code filters, one small model call per posting, briefs on request)
and changes what it reads, how it filters and how it judges.

## Why

Measured on this account's runs, 2026-11-04 to 2026-11-06:

- **Matching.** Of the last 80 leads from runs whose description says "Laravel", 1 names Laravel
  in its posting and 3 name PHP. Interns, staff and principal roles were kept too. Two causes:
  - Code lets every plain engineering title ("Software Engineer II") through to the judge when
    the brief names no title family, and treats any technology from the description as enough,
    one at a time.
  - The judge is told to "judge the day-to-day work, not the title", has no line saying which
    technologies are required, and "Wide net" tells it to keep stretches. So it keeps "Senior
    Software Engineer, Backend (AI Platform)" at Affirm as a stretch for a Laravel brief.
- **Volume.** The shared catalog holds 8,350 open jobs at 1,426 companies, 533 of them naming
  Laravel at 301 companies, about 115 of those open to the US. A Companies run reads only the
  companies on the profile's own list (186 rows), so it never sees them. Only 152 companies
  have a readable board of their own, and the catalog worker reads only boards on some list.
- **Level.** Nothing in code or in the judge's instructions knows what level the person wants.
- **Cost.** Companies runs cost $0.09 to $0.18, Pipeline $0.24 to $0.91, Agent $0.98 to $1.81.
  The cheapest engine is also the one that finds most, so it's the base.
- **Three engines** that each filter, judge and store differently. Only Companies remembers
  verdicts, so only it gets cheaper over time.

## Goals

1. A search that names a framework only returns jobs that use it. Strict, checked twice: code
   first (free), then the judge.
2. Level and location stay flexible, within stated rules: one level either side of what's wanted
   is a stretch, not a miss; relocation and remote rules stay as they are. Whether a place is
   a major US metro, for "open to relocating to a major metro", is decided in code from a fixed
   list of the 50 or so largest (`app/scout/metros.py`), and the judge is told the answer.
3. More leads per run, from the whole shared catalog and from more companies' boards.
4. A modest rise in cost: typical runs about $0.40 to $0.90, never more than the run's dollar ceiling.
5. Everything a model decides about a posting that isn't about the person is stored in the shared
   catalog, so later runs, by anyone, decide it in code. Everything about the person is stored
   per profile, as before.

## One engine

- New runs always use one engine. Its stored id stays `companies`, so its history, limits and the
  Spend page's per-engine figures carry on without a data migration.
- `smartsearch_settings.engine` stays in the table and is ignored. The settings form has no
  Engine control. `ScoutSettingsIn.engine` is still accepted from older pages and ignored.
- Old runs keep their engine label (Agent, Pipeline, Companies) on the runs list and report.
- Removed: the agent loop (tailor `POST /scout`), the pipeline's finder and fetcher (tailor
  `POST /pipeline/find`, `POST /pipeline/fetch`), and the backend's pipeline orchestration.
  Kept: `/pipeline/judge` (now screen and verify), `/pipeline/brief` (briefs on request),
  `/companies/discover`, `/companies/careers`, `/companies/same_company`.
- The backend filter helpers that lived in `scout_pipeline.py` move to
  `scout/filters.py`.

## Settings

Three new fields on `smartsearch_settings` (migration 0037), saved, snapshotted on each run and
sent to tailor:

| Field | Type | Meaning |
|---|---|---|
| `must_have` | JSON list of strings, nullable | Technologies the job must use. A job passes with any one of them. `null` means "not set": the server derives it from the description (below). `[]` means no stack rule. |
| `rule_out` | JSON list of strings, nullable | Technologies that rule a job out when it requires them. `null` derives from the description. |
| `levels` | JSON list of `PostingLevel` values | Levels wanted. Empty means any level except intern and manager. |

Deriving from the description (code, no model):

- `must_have`: the technologies the description names, by the posting parser's own list, most
  mentioned first, at most 4, leaving out any named after "no", "not", "without", "avoid",
  "except" or "exclude".
- `rule_out`: technologies named right after those words ("NO MULESOFT" gives MuleSoft).

`GET /api/scout` returns the effective lists (derived when unset), so the form shows what
a run will use, and saving makes them explicit. The form offers the parser's technology names as
suggestions (`technology_names` on the response).

## A run, in order

1. **Match spec** (code). `must_have` (each with its implied names: Livewire, Lumen and Filament
   imply Laravel), `rule_out`, `levels`, and the location rules, from the run's settings.
2. **Harvest** (free). Callback's job boards (the ones Job listings searches, LaraBench, Indeed
   and LinkedIn among them) are searched once per `must_have` technology, up to 3, or with the
   title families' phrases when there's no stack rule. Everything returned is recorded in the
   catalog before the scan, so it's judged and remembered like any other catalog job. The
   searches run at once and deep: Indeed up to 300 jobs and LinkedIn up to 75, each from the last
   30 days (Job listings asks for 100 and 25 from 14 days), cached apart from Job listings'
   searches, with 3 minutes to answer. A board still searching then is named as unavailable,
   and its listings reach the catalog for the next run. Other boards answer as they do for Job
   listings: LaraBench's whole feed, the API boards' first answer.
3. **Catalog companies** (free). Unchanged `discover_from_catalog`, with the stack terms as its
   search, trying up to 60 companies' names as Greenhouse, Lever and Ashby boards (was 25).
4. **Scan** (code). Every fresh, open catalog job that could match, from the whole catalog,
   not just the profile's list (see "Code gate"). The profile's list still counts: its companies'
   boards are read first, and their jobs rank higher.
5. **Discover** (Haiku with web search). Only when the scan left fewer than 3 × the lead target
   and the run has searches left. Its technologies are `must_have` when set (not the fact
   sheet's top 8). The scan runs again after.
6. **Refresh** (free). The own boards of the companies behind the first candidates, up to 40, are
   read again when last read over 6 hours ago, so closed jobs drop out before they're judged.
7. **Screen** (Haiku, one posting per call, as today, with the new fields below).
8. **Verify** (Sonnet, one call per posting the screen kept). Same form, stricter effort. A
   lead is kept only when both keep it; its fit is the verifier's.
9. **Lead.** Saved and staged as today. Briefs are written when the user asks.

The run stops at the lead target, the screen limit (`max_pages`), the time limit or the run's dollar
ceiling. Before each model call, the run checks it can afford that call plus one verify.

## Code gate

A job is left out, in this order, with each reason counted in the run's notes:

| Rule | Detail |
|---|---|
| known | Already a posting, hidden, turned down, or passed on by an earlier run (unchanged). |
| stale | Not seen in the last 30 days (`last_seen`). Board-only jobs are never closed by a feed. |
| agency | At a company the catalog knows as an agency, when agency listings are skipped. |
| stack | `must_have` set and none of them (or their implied names) in the title or in the text outside a nice-to-have section. A technology named only under "Nice to have", "Bonus" or "Preferred", or in a line saying it's a plus, doesn't count. |
| rule_out | A `rule_out` technology in the title or in a required line. |
| title | No `must_have`: a title phrase, or with none picked, a title of the profile's work boundary (docs/skills_spec.md, Scout's work boundary). |
| level | The job's level is more than one rung from every wanted level, or is intern or manager and not wanted. |
| old, mode, abroad, far | Unchanged. |
| not open to the US | A US location, and the catalog's stored fact says the posting can't hire in the US. |
| learned | The profile's learned filters (below). |

Levels, lowest first: intern, junior, mid, senior, staff, principal. Manager stands apart: it's
kept only when wanted. The job's level is the catalog's stored level (a model read it from the
posting) when stored for the posting's current text, else the parser's (title words, then years
asked). A job with no level found goes to the judge.

Rank: stack in the title (12), else in a required line (8), else elsewhere (4); exact level (+2);
a title family in the title (+2); a company on the profile's list (+1). Then, as today, newest
first and companies taking turns, so one company can't fill a run.

## The judge

Tailor's `ScoutRequest` gains `must_have`, `rule_out` and `levels`. The brief's lines say:

- `Must use (any one): Laravel, Livewire` or `Must use: no stack rule`.
- `Rule out: MuleSoft`.
- `Levels wanted: senior. One level either side is a stretch.` or `Levels wanted: any`.

The judgment form, in this order, so facts are settled before the verdict:

| Field | Meaning |
|---|---|
| `location_ok` | Unchanged. |
| `us_hireable` | `yes`, `no` or `unclear`: can someone living in the US be hired, read from the posting alone. |
| `posting_level` | The level the posting hires at, from the posting alone: one of the levels above, or `unclear`. |
| `stack_ok` | With a Must use line: the job's own day-to-day work uses one of them. False for a nice-to-have, a list of alternatives the team doesn't use, a system to integrate with, or a past-experience example. True with no stack rule. |
| `keep`, `fit`, `work_match`, `agency`, `reason` | As today. keep needs location_ok and stack_ok. |

Code decides the level, not the model: `keep` is overruled when `posting_level` is outside the
level rule. "Wide net" now means a wide net on the person's gaps, never on the stack or level.

`JudgeRequest.tier` is `screen` (Haiku, effort low, the default) or `verify` (Sonnet,
`pipeline_verify_model`, effort medium).

## What's stored, and what it teaches

Shared, on `catalog_jobs` (migration 0037), from the posting alone and never from a user's brief:

| Column | Meaning |
|---|---|
| `level` | `posting_level` as the judge read it. |
| `us_hireable` | `yes`, `no`, `unclear`. |
| `facts_key` | The posting's text fingerprint (`text_key`) when they were read; a changed posting is read again. |

Per profile, on `job_judgments` (migration 0037): `stack_ok`, `posting_level` and `verified`
(true when Sonnet made the final call). The verdict stored is the final one.

What code learns, by counting, with no model call:

- From shared facts: any later run, by anyone, uses the stored level and US hiring for that
  posting instead of asking.
- From verdicts (unchanged): a job judged before for the same brief isn't judged again; places
  always turned down; title words always passed on; twins of a rejection.
- From the user's "Wrong level" choices: a level turned down that way twice, never kept, is
  left out for that profile.
- `brief_key` now covers `must_have`, `rule_out` and `levels`, so changing them starts fresh
  verdicts.

## Catalog growth

- The catalog worker reads every company's own board: those on some profile's list every 6 hours
  (unchanged), all others every 24 hours (new). So jobs found catalog-wide stay open or close.
- A kept lead's company joins the profile's list (unchanged), so the boards that pay off are read
  most often.

## Cost and limits

Replaces credits (2026-11-09). Credits stood for an estimate of cost that was always wrong, and
the cap set a run's limits behind the user's back. Now each run records and shows only its real
cost in US dollars (`cost_usd`, `request_costs`), on the run report and the Spend page, and what
a user may do is counted in runs and briefs, not money.

**Stop limits.** The user sets them on the form, within these ranges:

| Limit | Range | Default |
|---|---|---|
| Leads | 1 to 60 | 25 |
| Web searches to find new companies | 1 to 10 | 6 |
| Postings to check | 10 to 300 | 150 |
| Time limit | 2 to 20 minutes | 10 |

A run at every maximum costs about $1.50 (Haiku screens about $0.0005 each, Sonnet verifies
about $0.015, a discovery search about $0.03). The form shows what past runs cost, from their
recorded costs, and no estimate for the next one.

**Caps,** all in `app_config` (below), all per user and per UTC day:

| Setting | Default | Meaning |
|---|---|---|
| `smartsearch_daily_runs` | 2 | Runs a user may start a day. A failed run doesn't count, since it's charged nothing and can be asked again. |
| `smartsearch_daily_briefs` | 10 | Briefs a user may ask for a day. A failed brief doesn't count. |
| `smartsearch_run_max_usd` | 2.00 | The most one run may spend, checked before every model call (`budget_usd`, as before). A safety stop, never shown as an estimate. |

- At the cap, `POST /runs` and the brief endpoint answer 429: "You've used all 2 Scout
  runs for today. They reset at midnight UTC." (and the same for briefs).
- `GET /api/scout` returns, under `allowance`, `runs_today`, `daily_runs`, `briefs_today`,
  `daily_briefs` (null when the caps are lifted) and `run_max_usd`. The page shows "1 of 2 runs
  left today", and the Run button says when none are left. Each run stores the ceiling it was
  queued with in its settings (`budget_usd`), so a later change doesn't alter a queued run.
- The one-run-at-a-time rule is unchanged.
- A user with `override_limits` (below) has no daily caps. The dollar ceiling and the
  one-run-at-a-time rule still hold for them.
- The run report's "budget" stop reads "Reached this run's spending ceiling ($2.00)".

**Removed:** `SCOUT_MONTHLY_CREDITS`, `SCOUT_MAX_RUN_CREDITS`,
`SCOUT_USD_PER_CREDIT`, the credit rates, `limits_for_cap`, `worst_case_credits`,
`run_cost_ceiling`, `charged_credits`, the monthly allowance, and the Spend page's Credits
column. `CreditTopUp` (money added to the Anthropic account, for the Spend page's balance) is a
different thing and stays.

**Migration 0043:** drops `credit_cap` and `credits_used` from `smartsearch_runs` and
`smartsearch_briefs`, and `credit_cap` from `smartsearch_settings`. Saved `limits` stay and are
now what runs use. `cost_usd` is untouched, so every past run keeps its recorded cost. Downgrade
adds the columns back, `credits_used` as `cost_usd / 0.05` up to the cap, and `credit_cap` as
40 for runs and settings, 3 for briefs.

## Settings in the database (`app_config`)

Settings that tune what Callback spends on the API platform live in the database, so the admin
can change them as costs come in, without a deploy. Secrets and deploy wiring stay in the
environment (`POSTGRES_PASSWORD`, `SECRET_KEY`, API keys, `HTTP_BIND`, `COMPOSE_FILE`,
`COOKIE_SECURE`), and so do settings that don't affect API spend (`LISTINGS_CACHE_MINUTES`,
`STALE_AFTER_DAYS`).

**Table** `app_config` (migration 0042): `key` (text, primary key), `value` (JSON), `updated_at`, `updated_by`
(user id, nullable). A row exists only for a setting the admin changed.

**Registry** in code (`app/app_config.py`): every setting's key, label, help, type, range and
default. The value in force is the saved row when it's valid for today's range, else the
default. Read from the database each time it's used, with no cache, so the api and every worker
see a change at once. Each default equals its old `docker-compose.yml` default, so moving a
setting changes nothing on a server whose `.env` holds only secrets; a test checks they match
until the compose entries are gone.

| Key | Default | Was |
|---|---|---|
| `smartsearch_daily_runs` | 2 | new |
| `smartsearch_daily_briefs` | 10 | new |
| `smartsearch_run_max_usd` | 2.00 | new (was the credit cap × $0.05) |
| `tailor_daily_limit` | 5 | `TAILOR_DAILY_LIMIT` |
| `reject_analysis_daily_limit` | 20 | `REJECT_ANALYSIS_DAILY_LIMIT` |
| `company_research_daily_limit` | 10 | `COMPANY_RESEARCH_DAILY_LIMIT` |
| `company_research_max_age_days` | 30 | `COMPANY_RESEARCH_MAX_AGE_DAYS` |

The moved env variables are removed from `config.py`, `docker-compose.yml` and `.env.example`.

**Changed** in **Admin → Settings**: each setting with its value, default and range, a reset to
the default, and who changed it last. `GET /api/admin/config`, `PUT /api/admin/config/{key}`,
`DELETE /api/admin/config/{key}` (back to the default), admins only; a value out of range is
refused with 422.

**`users.override_limits`** (boolean, default false, migration 0042, with the table): the user has no daily
caps on Scout runs, briefs, tailorings, rejection analyses or company research. Set in
**Admin → Users**, beside the plan, including on the admin's own account. Pages show "No daily
limit" instead of a count for them.

## Chunks

1. Match spec, code gate, levels, `must_have` and `rule_out` settings, the judge's new fields
   and the verify tier. Migration 0037.
2. Whole-catalog scan, harvest, refresh, discovery on the stack, shared facts, learned levels,
   catalog worker reading every board.
3. One engine: worker, router, settings form, removal of the agent and the pipeline.
4. Tests and DECISIONS.md.
5. Not built: Workable, Recruitee and SmartRecruiters readers (smaller PHP shops often hire on
   these), and probing every catalog company's name for a board in the background rather than
   only the ones a run matches.
