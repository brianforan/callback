# Companies: a company-first Scout engine

Status: superseded on 2026-11-06 by [scout_spec.md](scout_spec.md), which made this the
only engine and changed what it reads and how it matches. Kept for its history. Written 2026-11-05. Build one chunk at a time, in order. Each chunk ends
with its tests passing and the stack rebuilt, and is usable on its own.

Companies is a third Scout engine, picked on the form beside Agent and Pipeline. Agent and
Pipeline keep working exactly as they do: their prompts, stages, limits, credit rates and briefs
don't change. Each engine has its strengths (the agent explores freely, the pipeline searches the
open web cheaply), and the user picks per run. Companies adds a new process for what both miss:
volume from companies' own job boards.

## Why

The existing pipeline searches the web for job titles and reads what comes back. On this
account's runs (2026-11-04 and 2026-11-05) that gave 0 to 15 leads a run:

- The searches keep returning the same postings. 38 were skipped as already known or reviewed.
- Search indexes are full of closed postings. On bad runs 16 to 21 of 25 links didn't open.
- The page limit (25 by default) is used up before the target is met.
- The judge spends calls on postings code could have dropped ("Spain only", "Berlin hybrid").

The goal for Companies is dozens of staged, open, in-region leads per run, at or below the
pipeline's cost.

## The idea

Look for companies, not jobs. Greenhouse, Lever and Ashby each list every open job at a company in
one free, unauthenticated request:

| System | List endpoint | Company ID in a posting link |
|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | `boards.greenhouse.io/{token}/jobs/{id}`, `job-boards.greenhouse.io/{token}/jobs/{id}` |
| Lever | `https://api.lever.co/v0/postings/{token}?mode=json` | `jobs.lever.co/{token}/{id}` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true` | `jobs.ashbyhq.com/{token}/{id}` |

Tailor's fetch already reads single jobs from these systems, and its Ashby reader already downloads
the whole board. A job on one of these lists is open, by definition.

So: keep a growing catalog of companies and their hiring systems, and their open jobs, kept fresh by
a background worker of its own. A Companies run reads the stored jobs at the companies that matter
to the profile, filters them in code, and sends only the shortlist to the model. The model's jobs are finding new companies (cheap, Haiku with web search) and judging the
shortlist (Haiku, one small call each). Briefs are written only when the user opens a lead.

## Principles

- Separate, not a replacement. Companies has its own engine value, stages, worker, limits and credit
  rates. The only change to Agent, Pipeline and Job listings is that the listings they already read
  are also recorded in the catalog, which changes nothing they show.
- Code first. A model call is used only where judgment is needed: finding companies that fit a
  brief, judging a posting, an unclear company match, and the brief. Every model decision that
  can be reused is stored.
- A company's identity is its hiring-system ID or website domain. Its names are aliases.
- A fuzzy name match never merges two companies by itself.
- One record per job, with every place it's listed. The employer's own posting is the main link.
- The catalog (companies, their sources, open jobs) is shared across users, because it's built
  from public listings. It never records which user searched for, saw, or turned down what. What
  a profile cares about (its watchlist, its leads, its hidden companies) stays per user.
- Nothing in the shared catalog comes from a user's own typing. A user's saved postings
  contribute only the hiring-system ID in their link.

## Data model

New tables, all in one migration per chunk that introduces them.

### `companies` (shared)

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | |
| name | varchar(200) | Display name: the most common name seen in its own hiring system's feed, else the first seen. |
| key | varchar(200), index | `company_key(name)` |
| compact_key | varchar(200), index | `key` with spaces removed, so "open ai" and "openai" meet. |
| domain | varchar(255), unique, nullable | Lowercased registrable host of its website, without `www.`. |
| kind | varchar(20) | `employer` or `agency`. |
| created_at, updated_at | timestamptz | |

A GIN trigram index on `compact_key` (`pg_trgm`, which the postgres:17-alpine image ships) backs
the fuzzy match.

### `company_aliases` (shared)

| Column | Type | Notes |
|---|---|---|
| alias_key | varchar(200) pk | A `compact_key` known to mean this company. |
| company_id | uuid fk companies, cascade | |
| decided_by | varchar(20) | `exact`, `source` (shared ID or domain), or `model`. |
| created_at | timestamptz | |

### `company_match_checks` (shared)

A fuzzy candidate pair already decided, so it's never asked again.

| Column | Type | Notes |
|---|---|---|
| key_a, key_b | varchar(200), pk together | Ordered so `key_a < key_b`. |
| same | boolean | |
| decided_by | varchar(20) | `source` or `model`. |
| created_at | timestamptz | |

### `company_sources` (shared)

Every place a company's jobs are listed.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | |
| company_id | uuid fk companies, cascade, index | |
| source | varchar(40) | `greenhouse`, `lever`, `ashby`, or a board id from `app.listings.boards.BOARDS` (`indeed`, `linkedin`, `remotive`, ...). |
| identifier | varchar(200) | The hiring-system token, or the company name as that board writes it. |
| unique (source, identifier) | | |
| is_own | boolean | True for the company's own hiring system. |
| status | varchar(10) | `ok`, `empty` (answered with no jobs), or `dead` (404 or invalid). |
| open_jobs | integer | Jobs on the last read. |
| failures | integer | Reads in a row that failed. Reset on success. |
| first_seen, last_seen, last_checked | timestamptz | `last_checked` only for `is_own` sources. |

### `catalog_jobs` (shared)

One record per job, whatever board it's on.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | |
| company_id | uuid fk companies, cascade, index | |
| title | varchar(200) | As the main listing writes it. |
| title_key | varchar(200), index | See "Title key" below. |
| location | varchar(300), nullable | |
| work_mode | varchar(20), nullable | |
| posted_on | date, nullable | |
| text | text | The main listing's full text. |
| fingerprint | bigint | 64-bit SimHash of the text, for near-duplicate checks. |
| first_seen, last_seen | timestamptz | |
| closed_at | timestamptz, nullable | Set when its own hiring system stops listing it. |

### `catalog_job_sources` (shared)

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | |
| job_id | uuid fk catalog_jobs, cascade, index | |
| source | varchar(40) | As in `company_sources`. |
| url | varchar(2048), unique | `canonical_url` of the listing. |
| external_id | varchar(100), nullable | The hiring system's job ID when the link or feed gives one. |
| is_primary | boolean | Exactly one per job: the employer's own posting when there is one, else the earliest seen. |
| first_seen, last_seen | timestamptz | |

### `profile_companies` (per user)

The companies a profile's runs read.

| Column | Type | Notes |
|---|---|---|
| profile_id | uuid fk profiles, cascade | |
| company_id | uuid fk companies, cascade | |
| pk (profile_id, company_id) | | |
| user_id | uuid fk users, cascade, index | For ownership checks. |
| origin | varchar(20) | `postings` (from the user's own saved links), `discovered`, `lead`, or `board`. |
| leads | integer | Leads it has given this profile. |
| dismissed | integer | Leads turned down (Not for me). |
| last_lead_at | timestamptz, nullable | |
| added_at | timestamptz | |

A company the user hides (the existing `HiddenListing` company rule) is skipped at read time, not
removed, so un-hiding brings it back.

## Chunk 1: company identity

Build `companies`, `company_aliases` and `company_match_checks`, and one function every later chunk
uses:

```python
def resolve_company(
    db: Session, name: str, *, domain: str | None = None, source: tuple[str, str] | None = None
) -> Company
```

It finds or creates the company, in this order. The first step that answers wins.

1. `source` (system, token) already in `company_sources`: that company.
2. `domain` already on a company: that company.
3. `compact_key(name)` is an alias: that company.
4. Fuzzy candidates: companies whose `compact_key` has trigram similarity ≥ 0.6 with this one, best
   three. For each, a stored `company_match_checks` answer is used if there is one. Otherwise the
   pair is the same company only if a second signal agrees (same domain, or same hiring-system
   token). With no second signal, the pair is queued for a model check (below) and a new company is
   created for now.
5. Otherwise a new company, with its `compact_key` as its first alias.

Name normalization extends today's `company_key` ([postings/parser.py](../backend/app/postings/parser.py)):

- Agency wording is split off before matching: "X (via Y)", "Y (X)" when Y is a known agency, "Y
  for X", "Y on behalf of X". The employer is resolved as the company; the agency is resolved as
  its own company with `kind = agency`. The patterns are in code, and a judge's `agency` answer
  also marks a company as an agency.
- "The " at the start is dropped, and "&" becomes "and".

**Model check for unclear pairs.** A new tailor endpoint `/companies/same_company` takes two names
with whatever each record has (domain, hiring-system token, a sample job title and location) and
answers `same: bool` with Haiku and structured output. Up to 20 pairs are sent per call. Queued
pairs are checked at the end of the next Companies run whose companies include either side, and
charged to that run; until then the two stay separate. Agent, Pipeline and Job listings never make
this call. A "same" answer adds the alias
and merges the two companies (sources, jobs and profile rows move to the older company; the newer
is deleted; the moved jobs are matched again against the older company's, so duplicates collapse). Every answer is stored in `company_match_checks`.

Merging is the only way two companies become one, and it's logged with both names.

**Tests.** "Acme, Inc." / "ACME" / "acme inc" resolve to one company. "Open AI" and "OpenAI" do.
"Clair" and "Claire" do not merge without a second signal, and do with the same domain. "TLA Tech
(Syllo)" and "Syllo (via TLA Tech)" both resolve to employer Syllo with agency TLA Tech. A stored
"not same" check is never sent to the model again. Merging moves every child row.

## Chunk 2: sources and duplicate jobs

Build `company_sources`, `catalog_jobs` and `catalog_job_sources`, and:

```python
def record_listing(db: Session, listing: CatalogListing) -> CatalogJob
```

`CatalogListing` is a listing from anywhere: a job board result, a hiring-system feed entry, or a
page Scout read. It carries source, url, company, title, location, work mode, posted date,
text, and the hiring-system (system, token, job id) when the link or feed gives one.

**Hiring-system IDs from links.** One parser reads (system, token, job id) from Greenhouse, Lever
and Ashby links, including the embed forms (`boards.greenhouse.io/embed/job_app?for={token}&token={id}`
and `?gh_jid={id}` on a company's own careers page). JobSpy's `job_url_direct`, when present, is
parsed too, so an Indeed or LinkedIn listing that links to the employer's system is tied to it.
Tokens are validated as `[A-Za-z0-9_-]{1,100}` before being stored or used in a request.

**Matching a listing to a job.** The first rule that matches wins:

1. Same canonical URL in `catalog_job_sources`.
2. Same (system, job id).
3. Same company, same title key, and compatible location (both blank, one blank, or one contains
   the other after lowercasing).
4. Same company and SimHash distance ≤ 3 on the text (near-identical postings: a reworded title,
   a repost under a new ID, an agency repost with the company's name changed). An agency listing is
   matched across companies this way only against jobs at companies it has posted for before, or
   when the judge named the employer.

No match: a new job. The listing becomes one of the job's sources either way, and `last_seen`
moves on the job, the source and the company's source row.

**Title key.** Lowercase; "sr"/"sr." to "senior", "jr" to "junior", "eng" to "engineer", "mgr" to
"manager", "&" to "and"; Roman numerals I to IV and digits after a level word kept ("engineer ii");
trailing location or mode in brackets or after " - " dropped ("(Remote)", "- US", "- New York");
punctuation removed; spaces collapsed.

**Primary link.** The employer's own hiring-system posting when the job has one, else the earliest
source. A Companies lead always links to the primary; its card shows the others as "Also on
Indeed, LinkedIn".

**Where listings come from in this chunk.** Job listings search results (every board, as they're
fetched) and every page any Scout engine reads are recorded, after they're returned, so
recording can't slow or fail those searches. Job listings and the existing engines keep their own
duplicate removal; switching Job listings to the catalog's job id is a later, separate decision.

**Board ranking.** `company_sources` doubles as the dynamic list of where companies list jobs. A
per-board count of jobs recorded that were later kept by a judge is kept on a small
`board_stats` table (board id, listings recorded, kept, last_kept_at). It's shown on the admin page
in this chunk and used for ordering in chunk 3. No board is turned on or off automatically.

**Tests.** The same job from Greenhouse's feed, a `job-boards.greenhouse.io` link, an Indeed
listing with that link as its direct URL, and a LinkedIn listing with "Sr." and "- Remote" in the
title is one job with four sources, Greenhouse primary. A repost with a new ID and the same text
is the same job. Two different jobs at one company with the same title in different cities stay
two jobs. A token with a slash or a dot is refused.

## Chunk 3: the catalog worker and the Companies engine

**The engine.** `engine` gains `companies` (it's a varchar(20), so no migration). The Search form
shows a third option, Companies: "Reads the open jobs of companies on your list straight from their
job boards, and Haiku checks the best matches. The most leads for the least, but only from companies
on Greenhouse, Lever or Ashby." The Scout worker sends a `companies` run to its own module
(`app/scout/engine.py`), beside `scout_pipeline.py`, sharing only the judge stage and
the filter helpers.

**Filling the catalog without a model (free).**

- On upgrade, a one-off command `python -m app.cli catalog-seed` reads every Greenhouse, Lever and
  Ashby link in `job_postings`, Scout leads, the find stage's past candidates (run steps), and
  `board_cache`, and records each company with its token. Each user's own links also add the
  company to that profile's `profile_companies` (`origin = postings` or `lead`).
- From then on, `record_listing` does the same as listings arrive.

**The catalog worker.** A new container, `catalog-worker`, from the API image
(`python -m app.companies.catalog_worker`), like the billing worker. It holds no API key and makes no model
calls. On a loop it reads the list endpoint of every `ok` source on any profile's list whose
`last_checked` is over 6 hours old, oldest first, 8 at a time, and records every job with
`record_listing`. One request per company. A read that answers 404 marks the source `dead`; three
failures in a row stop it being read until it appears in a new link. A job no longer listed by its
own source gets `closed_at`, and closed jobs are never judged. A source added to a profile's list
is read within a minute, so a new company doesn't wait for the cycle.

Runs don't wait on company reads: a Companies run reads `catalog_jobs` from the database. A run
that starts with sources never read yet asks the worker to read them first (a `wanted_by` time on
the source) and waits up to 60 seconds for them.

These are fixed hosts built from a validated token, so the backend reads them directly, as it reads
job boards today, rather than through tailor's public-address check.

**Which companies a run reads.** The open jobs of the profile's `profile_companies`, minus hidden
companies, minus companies whose last three leads for this profile were all dismissed.

**Filters, in code, before any model call.** In order, each counted in the run's notes the way the
board filter counts today:

1. Closed, already known, already reviewed, or turned down (by URL or catalog job).
2. Hidden company.
3. Title: matches the brief's title families or the description's technologies, using the same
   `parse_query` matching the board search uses. A title match is required when the brief has
   title families. Without them, a match in the text is enough, and so is a plain engineering
   title (developer, programmer, or software, backend, front-end, full-stack, web, application,
   platform, PHP, product, integration, tools or systems engineer), which the judge then decides
   on. Changed 2026-11-05: on the first real list, requiring the one technology the brief named
   left 16 of 4,468 open jobs.
4. Location: the US check from 2026-11-04 (`_abroad`) and, for onsite or hybrid jobs with a US
   Location, the job's state must be in or next to a state the Location names, when it names one.
5. Work mode and posted-within, as today.

**Ordering and the judge.** Kept jobs are ordered: title match strength (the stack or a title
family in the title, then in the text, then a plain engineering title), then posted date, then
companies that gave leads before. The judge reads them in that order until the lead target, the
judge limit, the time limit or the budget is reached. The judge prompt and schema are unchanged.

**Limits.** Companies has its own limit ranges: "Leads wanted" up to 60 and "Postings to check"
(key `max_pages`) up to 150, with "Web searches" used only by discovery (chunk 4). Agent and
Pipeline keep theirs. `LIMITS` gains a per-engine range, and the form shows the selected engine's.

**Credits.** Replaced 2026-11-09 by daily run and brief caps; see scout_spec.md, "Cost and
limits".

**No web search for jobs.** Companies reads only the catalog. For jobs the catalog doesn't cover,
the user runs Pipeline or Agent, which keep feeding the catalog as they go.

**Tests.** A Companies run with a seeded catalog reaches the lead target from stored jobs alone. A
closed job is never judged. A hidden company's jobs are never judged. Three failed reads stop a
source. The worker reads a source once for two users' profiles. A run waits for unread sources and
goes on without them after 60 seconds. The filters' counts appear in the run's notes. Agent and
Pipeline runs give the same payloads and results as before.

## Chunk 4: finding new companies

A first stage in Companies runs: `/companies/discover` in tailor, Haiku with web search and a
tool `add_companies(companies: [{name, website, careers_url, why}])`, like the find stage's
`add_candidates`.

- Input: the brief (description, title families, work modes, Location), the profile's technologies
  from its fact sheet, and the count of companies already on the profile's list. Not the list
  itself, which would grow without bound; instead, names already known are dropped by code after.
- It's told to search for companies that do the kind of work in the brief, in the brief's region
  when it names one, through lists, directories and the hiring systems themselves (for example
  `site:boards.greenhouse.io "internal tools"` gives tokens directly).
- Budget: 15% of the run's budget and at most 10 searches. It runs only when the profile has fewer
  than 400 companies or the last run's company reads gave fewer leads than wanted.

**Checking a company is real and hiring, in code.** For each company it adds:

1. `careers_url` or any link it saw parses as Greenhouse, Lever or Ashby: read the list endpoint.
   One or more jobs: the source is `ok` and the company joins the profile's list (`origin =
   discovered`).
2. Otherwise, code opens `careers_url` (through tailor's fetch, which has the public-address check)
   and looks for a hiring-system link or embed in the page. Found: as step 1.
3. Otherwise the company is recorded with its domain and no source, and isn't read. It's listed on
   the run report as "found, but its jobs aren't on a system Callback can read".

A company with an open board is treated as legitimate. The judge still flags agencies per posting,
and an agency company is never added to a profile's list from discovery.

**Tests.** A discovered company with a Greenhouse careers URL joins the list after one feed read. A
careers page with a Lever embed is found. A company with no readable system is recorded but not
read. Discovery's cost is within its budget share.

## Chunk 5: briefs on demand

A Companies lead is saved and staged as soon as the judge keeps it. Staging needs only the parsed
posting ([resumes/matching.py](../backend/app/resumes/matching.py) `facts_from_posting`), not the brief.

- The lead stores the judgment (`fit`, `work_match`, `reason`, `agency`) and the posting's own
  fields. Its card shows those and a "Write brief" button.
- The button queues a brief: a new `smartsearch_briefs` table (id, user_id, run_id, lead_id,
  status, cost_usd, usage, request_costs, billed, created_at, started_at,
  finished_at), processed by the Scout worker with the existing `/pipeline/brief` call.
  The card shows it writing, then the brief.
- Each brief's measured cost is recorded when written, and briefs count against a daily cap
  (scout_spec.md, "Cost and limits", which replaced credits on 2026-11-09). The billing
  worker checks briefs' billed usage like runs and tailorings.
- Agent and Pipeline keep writing their briefs during the run.

**Tests.** A Companies run writes no briefs and its leads are staged. Pipeline runs still write
theirs. Writing a brief charges its
cost once, refuses when the allowance is used up, and survives a worker crash (marked failed, no
charge).

## Out of scope

- Workday, iCIMS, SmartRecruiters and custom careers pages beyond finding a Greenhouse, Lever or
  Ashby embed on them. Workday's JSON search is the obvious next system.
- Automatically turning job boards on or off from their stats.
- Company research of any kind. Removed on 2026-11-05 and not coming back here.
- Sharing anything per user across users: leads, dismissals and watchlists stay private.

## Cost, expected

For a run with a seeded catalog of a few hundred companies: discovery about $0.05 to $0.15 (when it
runs), company reads $0, judging 50 to 150 postings at about $0.002 each ($0.10 to $0.30). No
briefs at run time. About $0.15 to $0.45 for 30 to 60 leads, under a cent a lead, against the
pipeline's $0.07 a lead including its brief. A brief written on demand adds about $0.075 to the
lead it's for.
Later runs cost less as the catalog grows and discovery runs less often.
