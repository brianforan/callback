# Rejection research: posting text on log, company history, and a better-informed report

Status: spec, 2026-11-06. Nothing here is built. It extends the rejection analysis built the same
day (`app/research/analysis.py`, `app/research/analysis_worker.py`, tailor `/reject-analysis`).

## Why

The rejection report compares the posting with the resume sent and the fact sheet. It can't say
anything about the company, and some rejections have nothing to do with the person: a layoff, a
hiring freeze, a role reposted for months, a team that only hires from a narrow background. The
user also wants what's learned about companies kept as history, for hiring insights now and to
pair with a financial tool later (not in this spec).

## Goals

1. Every application has its posting's text. When one is logged by hand without it, Callback
   gets it: from the shared catalog when the job is there (free), else by reading the link.
2. Each company a rejection names gets a research snapshot: what it is, how it's funded, how it's
   hiring, what it builds with, the kind of people it employs, and how it interviews. Each fact
   carries its source and date.
3. Snapshots are history. A new one is added, never overwriting the last, so changes over time
   (headcount, funding, layoffs, stack) can be charted and exported.
4. How a company hires over time is also worked out in code from the catalog, which already
   keeps when each job was first seen and when it closed. Free, and back-dated.
5. The rejection report uses all of it: what the company's people typically have beside what the
   resume shows, a rough estimate of how much the rejection may have been about the person's
   profile (labeled as a rough estimate that can be wrong), and what's known about the company
   now. It never states the reason for a rejection as fact.

## Rules that hold everywhere

- **No personal data.** The workforce profile is aggregate only: typical years of experience,
  seniority mix, common prior industries and kinds of employer, degree mix, where teams sit. No
  names of employees, no links to people's profiles, no emails or contact details. Executives
  are recorded by role ("a new CTO in 2026-08"), not by name. The model is told this, and code
  enforces it on what's stored:
  - Sources on `linkedin.com/in/`, `/pub/` or any other profile path, and on people-search sites,
    are dropped, along with every fact that cites only them.
  - Free-text fields are dropped when they hold an email address or a phone number.
  - Free text is short (300 characters a field), and most fields are enumerations or numbers.
- **No scraping behind logins or bot checks.** Facts come from Anthropic's web search tool and
  from public pages read by tailor's existing fetcher, which refuses private addresses on every
  redirect. LinkedIn and Glassdoor are not read directly; a fact found in a search result that
  quotes them is kept with that result as its source.
- **Facts about a company are shared** by every user, like the catalog. What a user's own
  rejection says about them stays theirs. A snapshot never records who asked for it, except in
  the cost record for the Spend page.
- **Every fact has a confidence**: `stated` (a source says it), `estimated` (a source gives a
  range or a dated figure), or `unknown`. Unknown is the expected answer for a small private
  company, and the report shows it as unknown.

## Chunk 1: posting text when an application is logged

No model calls.

1. When an application or posting is saved with a link and no text (the form, the board, HTTP
   intake, or a posting added by hand):
   - Look the link up in the catalog, by `catalog_url(url)` against `catalog_job_sources.url`.
     When it's there with text, copy the text into the posting in the same request and re-parse
     it with `apply_parse` (fields the user corrected stay as they are).
   - Otherwise mark the posting `text_status = "pending"`.
2. The tailor worker reads pending postings after tailorings and rejection analyses, one at a
   time, through a new tailor endpoint `POST /postings/fetch` that wraps `Fetcher.fetch` (the
   Greenhouse, Lever and Ashby feeds, else the page's text). No model call, no API key needed.
   - Text of at least 500 characters is kept: saved to `raw_text`, re-parsed, `text_status =
     "fetched"`, `text_fetched_at` set.
   - Otherwise `text_status = "failed"` with `text_error` in plain words ("The page needs
     JavaScript to show the job", "The page couldn't be reached"). Not retried on its own.
3. A posting whose text arrives after its application was already analyzed with the note "no
   text" has its analysis queued again, free of the "once a day" rule.
4. The posting drawer shows "Fetching the posting's text…", or the failure with **Try again**
   and a reminder that pasting the text works too. Pasting text clears the status.
5. `python -m app.cli fetch-posting-texts --email you@example.com` marks every posting with a
   link and no text as pending, for the ones logged before this chunk.

New columns on `job_postings`: `text_status` (null, pending, fetched, failed), `text_error`,
`text_fetched_at`.

Limits: one fetch at a time, the fetcher's own 45-second deadline per page. Sites that build the
page in the browser (Workday, some iCIMS) usually fail; the failure says so.

## Chunk 2: company research snapshots

1. **Which company.** The application's company name goes through `resolve_company` (the
   catalog's identity rules), which finds or adds a `companies` row. Agencies are skipped: the
   report says the employer behind an agency posting is unknown.
2. **When.** Whenever a rejection report is asked for (the rejection itself, **Analyze** on the
   report page, **Analyze them** in Insights, or **Run again**), when the company's newest snapshot is
   older than `company_research_max_age_days` (30) or there's none. The worker runs queued
   research before any queued report, so the order never depends on which button was pressed
   (changed 2026-11-06 at the user's request). Also from **Research this company** in the
   application drawer, under the same age rule. Fresh snapshots are reused by every user at no
   cost.
3. **How.** One tailor call, `POST /companies/research`: Claude Haiku with the web search tool,
   at most `COMPANY_RESEARCH_MAX_SEARCHES` (6) searches, structured JSON output, and the rules
   above in its cached instructions. It's given the company's name, its domain when known, the
   posting's title and location, and what the catalog already knows (sources, open-job count).
   Expected cost about $0.05 to $0.08.
4. **Limits.** Each user's rejections may start `company_research_daily_limit` (10) snapshots a
   UTC day. Over it, the analysis runs with the newest snapshot there is, or none, and says so.
   A failed research call never blocks the analysis.
5. **Checks in code** before anything is stored: the personal-data rules above; every fact's
   source index points at a listed source; dates aren't in the future; numbers are in range
   (headcount 1 to 5,000,000, amounts non-negative). A fact that fails is dropped, not the
   snapshot.

`company_research` table, append-only, one row per snapshot:

| Column | What |
| --- | --- |
| `company_id`, `researched_at`, `model`, `usage`, `cost_usd` | |
| `sources` | `[{url, title, published_on}]`, up to 15 |
| `profile` | Where it is and what it does: industry, HQ city and country, founded year, website domain found |
| `ownership` | public, private_vc, private_pe, private_bootstrapped, subsidiary, nonprofit, government, unknown; ticker and exchange when public; parent when a subsidiary |
| `funding` | stage, total raised (USD), and rounds `[{date, round, amount_usd, source}]` |
| `size` | headcount estimate, as-of date, source, and trend (growing, flat, shrinking, unknown) |
| `events` | `[{date, kind, detail, source}]`: layoff (with count or percent), hiring_freeze, reorg, acquisition, acquired, ipo, leadership_change (role only), office_opened, office_closed |
| `engineering` | stack seen outside the posting `[{name, evidence: blog, github, other_postings, talk}]`, eng blog and open-source presence, team size estimate |
| `workforce` | aggregate only: typical years of experience for engineers, seniority mix, common prior industries, common prior employer kinds (big tech, startup, consulting, agency, enterprise), share with a CS degree when stated, where teams sit, remote policy |
| `interviewing` | stages as reported, take-home or not, typical length, what candidates say it weighs |
| `confidence` | per section: stated, estimated or unknown |

Technologies in `engineering` go through the posting parser's dictionary, so they line up with
checklist names. Snapshots are never deleted or edited; a correction is a new snapshot.

## Chunk 3: hiring history from the catalog

No model calls, no new table. From `catalog_jobs` (first seen, last seen, closed at) and
`company_sources` (whether Callback can read the company's own board):

- **Open roles by week**, all and engineering only, back to the company's first catalog row.
- **Opened and closed per week**, and the median days a role stays open.
- **Reposts**: a job closed and a near-identical one (same title key, text fingerprint within the
  catalog's distance) opened within 60 days.
- **This job after the rejection**: whether the posting applied to, matched by link or by title
  and fingerprint, was still open, closed, or reposted in the 30 days after the rejection date.

Shown only for companies whose own board Callback reads, since job-board listings come and go
for reasons that aren't the company's. Elsewhere it says "Callback can't see this company's
hiring directly."

`app/research/history.py` holds these as pure functions over rows, with tests on fixed data.

## Chunk 4: the report uses it

Revised 2026-11-06 with the user. The report isn't a verdict on the person. It says what the
company's people typically have, what this resume shows, a rough estimate of how much the
rejection may have been about that difference, and what's known about the company now. The
earlier "possibly not about you" flags are folded into the estimate and the company lines.

1. **Who they hire, beside you.** From the snapshot the analysis used (its newest when the
   analysis ran), the workforce profile set beside the resume sent, aggregate to aggregate:
   - years of experience: the company's typical range against the resume's years, worked out in
     code as the match checklist does;
   - seniority mix, common prior industries and kinds of employer: the company's, against what
     the model reads from the resume.
   Unknown on the company's side shows as unknown, and that line isn't compared. Written by the
   model in the existing analysis call, as a few lines of {measure, typical, yours, note}.
   Research rarely finds who works there, so "typical" also comes from what the company's own
   engineering postings asked for in the last year, read in code by the posting parser: the
   middle half of the years they ask for and the median, the levels they hire at, and the
   technologies they most often require. Said only with at least 3 postings, and always as
   "its postings ask for", never as who works there (added 2026-11-06 after the first real
   reports came back with no workforce profile).
2. **A rough estimate.** How much the rejection may have been about the person's profile:
   `likely`, `possibly`, `unlikely` or `cant_tell`, with the two or three reasons it rests on.
   The model decides it from the checklist score, the stage reached, the comparison above, and
   what the company was doing when the person was rejected: a layoff or hiring freeze in the 90
   days before, and open engineering roles down by half or more over the 60 days before (both
   worked out in code and given to the model). It's always shown as "a rough estimate that can
   be wrong", never as the reason.
3. **This company now.** Lines built in code, each with its source and date, and unknowns shown
   as unknown: what it is, size and trend, events in the last 12 months, and from the catalog
   (chunk 3) open engineering roles now and 60 days before the rejection, median days a role
   stays open, reposts, and what this job did after the rejection (still listed N days later,
   closed, or reposted). These are worked out each time the report is opened, so what happened
   after the rejection fills in as time passes. For a company whose own board Callback doesn't
   read, the catalog lines say "Callback can't see this company's hiring directly."
4. The model's instructions add: never state why the company rejected the person; the estimate
   is a hedged band with its reasons; a company with no snapshot gets `cant_tell` unless the
   checklist alone makes the answer plain.
5. **Insights → Rejections** gains the count of rejections in each estimate band, and a list of
   companies rejected from more than once with what's known about each.

A report written before this chunk keeps its text; the company lines show for it too, from the
company's newest snapshot. Running it again writes the comparison and the estimate.

## Chunk 5: hiring insights and export

1. **Insights → Companies**: the companies the user applied to that have a snapshot, with
   ownership, stage, size and trend, recent events, and its hiring history chart from chunk 3.
   Not every researched company: research runs on rejections, so that list would show where other
   users were turned down (decided with the user 2026-11-06). The facts are still shared. One company's page lists its
   snapshots over time, so changes show (headcount 420 → 380, a Series C, a layoff).
2. Export for other tools, the planned financial tool among them:
   - `GET /api/company-research/export.jsonl`: one line per snapshot, every field, with sources.
   - `GET /api/company-research/events.csv`: one row per dated event (company, domain, ticker,
     date, kind, detail, source).
   - `GET /api/companies/{id}/hiring-history.csv`: the weekly series from chunk 3.
   Exports hold company facts only, never anyone's applications, who asked for a snapshot, or
   what it cost. The first two are the admin's; the hiring history is for any company on the
   user's list.
3. The Spend page shows rejection analysis and company research costs with the other model
   calls.

## Settings

| Setting | Default | Where |
| --- | --- | --- |
| `COMPANY_RESEARCH_MODEL` | `claude-haiku-5-5` | tailor |
| `COMPANY_RESEARCH_MAX_SEARCHES` | 6 | tailor |
| `COMPANY_RESEARCH_MAX_TOKENS` | 8,000 | tailor |
| `company_research_max_age_days` | 30 | `app_config` (scout_spec.md) |
| `company_research_daily_limit` | 10 per user | `app_config` (scout_spec.md) |

## Not in this spec

- The financial tool itself, and any stock or market data.
- Researching every company applied to, not just rejections. It's one setting away once costs
  are known; left out so spend stays tied to rejections.
- Names, profiles or contact details of anyone who works anywhere.
- Glassdoor or LinkedIn readers.

## Chunks

1. Posting text when an application is logged (no model).
2. Company research snapshots (tailor `/companies/research`, `company_research` table).
3. Hiring history from the catalog (pure functions).
4. The report and Insights → Rejections use it.
5. Insights → Companies, exports, and costs on the Spend page.

Chunks 1 and 3 need no API key and can be built first and in either order. Chunk 4 needs 2 and 3.
