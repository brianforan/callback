# Callback

Track the jobs you apply to and the resume you sent to each, then find out which resumes actually get a call back.

Callback is a self-hosted web app for a job search. It searches public job boards, scores every job against your resumes, helps you pick or tailor the resume to send, and learns from what happens next: callbacks, rejections and the companies behind them. Nothing is ever applied to, submitted or sent for you.

## Pages

### Dashboard

Your search at a glance: applications, callback rate, interviews and the median time to a callback, activity by week, a funnel of the furthest stage each application reached, and applications worth a follow-up because they've waited too long (14 days by default, `STALE_AFTER_DAYS`).

A **callback** is an application that reached screening or beyond at any point. A rejection after an interview still counts, because the resume did its job.

### Applications

Every job you've applied to, as a table or a drag-and-drop board by status, with search, status filters and CSV export. Open one to see its status timeline, the resume you sent, its posting, and the emails, calls, messages and notes you've logged on it.

Every application gets a structured posting: level, role type, years required, salary, tech stack and referral, read from the posting's text by fixed rules with no model calls. Paste a link and Callback fetches the text for you (Greenhouse, Lever and Ashby from their public feeds, other pages as plain text). Any field you correct is never overwritten.

### Job listings

Search public job boards from one page: Remotive, Remote OK, Himalayas, The Muse, Jooble, Findwork and more. Each board's status shows under the results. A few boards need a free API key; `.env.example` lists them. Results are cached for 6 hours (`LISTINGS_CACHE_MINUTES`), and newer ones arrive in the background without reshuffling the rows you're reading.

| Write | To find |
| --- | --- |
| `python django` | Both words |
| `"platform engineer"` | The exact phrase |
| `python OR go` | Either word |
| `(python OR go) aws` | Grouped choices |
| `python -clearance` | Leave out a word, or `-(clearance OR citizenship)` for several |
| `title:senior`, `company:example` | A word in that field only |

Filters cover level, stack, salary, work mode and whether you've tracked a job. Every search and filter is in the page's URL, so you can bookmark one.

Every job gets a **match score** from 0 to 100 against each of your resumes, the way an ATS or a recruiter's keyword search would see it. A skill named in a dated role counts in full, one only in a skills list counts half, and the posting's years, degree and clearance are checked too. The score weighs the required stack most, then title, seniority, education, role type and nice-to-haves. Beside it are the closest resume, the skills you're missing, and a callback estimate drawn from your past applications with the most similar requirements.

Click any job to open its drawer, which has two steps:

1. **Your resume for this job:** the baseline that fits best, with **Download PDF** or **.tex**, or **Use another resume**. A box says whether tailoring could make it a better fit.
2. **Apply:** a link to the posting, notes for the application form, and **I applied**, which logs the application with that resume.

### Scout (feature flagged)

Scout finds the jobs keyword search misses. Describe the work you want, pick what a job must use, what rules one out and the levels you want, type the job titles to search under, and start a run.

A run searches the job boards and Callback's shared catalog of companies' own job boards, rules out in code whatever can't fit, and has Claude Haiku screen each remaining job and Claude Sonnet take a second look. A lead is kept only when both agree. Each run stops at the first limit it reaches (enough leads, postings checked, web searches, time, or a spending ceiling), all enforced in code.

- **Search** sets up a run and its brief.
- **Leads** lists what runs found. Each lead is saved as a posting in Job listings, ready to apply. **Not for me** hides it from later runs.
- **Tracking** follows the applications that came from Scout.
- **Runs** shows past runs, each with a step-by-step report and what it cost.

The full design is in [docs/scout_spec.md](docs/scout_spec.md).

### Resumes

- **Baselines:** up to three resumes you start from for different kinds of jobs, such as backend or full-stack. Rules say which jobs each suits: role types and levels, focus skills, deal-breakers, most years asked and a minimum score. Every job names the baseline to start from.
- **All versions:** every `.tex` or PDF you've uploaded. Identical content is recognised and reused. Each `.tex` gets a PDF, compiled in a sandbox. A PDF resume is read for matching but can't be edited or tailored.
- **Fact sheet:** what you can truthfully claim, in your own words, as one Markdown document. Tailoring treats it as the rules.
- **Skills:** what match scores, missing skills and Insights look for in postings and resumes. Turn on the built-in software skills (Python, React, PostgreSQL, AWS and about a hundred more), add your own field's skills with the other ways postings write them ("AP" for Accounts Payable), or both. Saved postings are read again with your skills in the background.

**Tailor**, in a job's drawer, rewrites the best baseline for that job using only what your fact sheet allows. It usually takes a minute or two. Before a result is kept, code checks that every job title, company and date matches the baseline, that every technology it names is backed by the baseline, your fact sheet or another baseline, and that it scores no lower than the baseline. A result that fails is retried once, then shown with its reasons and not saved.

The fact sheet's skills come from a table under a `## Skills status` heading:

| Skill | Status |
| --- | --- |
| Python (Flask), AWS (Lambda, S3) | Claimable |
| Kubernetes, Helm | Inside the Example Corp bullet only |
| Terraform, Go, EC2 | Never claim |

"Claimable" or "Secondary" allows a skill, "Inside" allows it only where the baseline already has it, and "do not claim", "never claim" or "do not list" forbids it.

### Insights

- **Resumes:** the keywords you use most, those that faded or rose over time, callback rate by resume, and the terms that track with callbacks.
- **Baselines:** each baseline scored on its own against every saved posting, the way an ATS would see it, plus the postings no baseline covers and why.
- **Postings:** level, years required, salary and tech stack over time, with role types, sources, referrals and repeat companies.
- **Rejections:** every application marked rejected gets a report on what the job asked for that your resume didn't show. It lists up to five things to work on, each technology with what to do about it (add it, show it in a role, take it from another resume, or learn it), the experience the posting wanted, how your resume compares with the engineers the company hires, and a labelled estimate of how much the rejection was about your profile. This tab adds the reports up across your search.
- **Companies:** the companies you've applied to that Callback has researched: what they do, ownership and funding, size and trend, recent events such as layoffs or funding, and their open roles over time.

Company research is one Claude Haiku call with web search. Code checks every fact before it's stored: each needs a dated source, and anything from a person's profile, a people-search site or a social site is dropped, along with any text holding an email address or phone number. No employee is ever named.

### Admin (admins only)

- **Users:** every user's role (`member` or `admin`) and plan (`free` or `premium`), and a **No daily limits** switch per user.
- **Spend:** what model calls cost, per run, lead, tailoring, user and day, compared with what Anthropic actually billed. Anthropic's usage and cost reports need an Admin API key in `ANTHROPIC_ADMIN_API_KEY`.
- **Boards:** where the shared catalog's listings came from.
- **Settings:** daily limits on Scout runs and briefs, tailorings, rejection reports and company research, and the most one Scout run may spend. Changes take effect at once.

### Profiles

A profile is one side of your job search, such as software engineering or accounting, with its own resumes, baselines, skills, fact sheet and Scout settings. Switch profiles from the foot of the sidebar. Applications and saved jobs are shared by every profile and note the one they were made under.

## Run it locally

You need Docker with Compose v2, `make` and `openssl`.

```bash
make up
```

Then open the URL that `make up` prints and choose **Create an account**. On first run, `make up` writes `.env` with random secrets and the first free port from 8080. Add any job board keys, and `ANTHROPIC_API_KEY` for tailoring, Scout and rejection reports, to `.env`, then run `make up` again. Run `make help` to see every command.

Make an admin, or give someone Scout:

```bash
docker compose exec api python -m app.cli set-role --email you@example.com --role admin
```

```bash
docker compose exec api python -m app.cli set-plan --email you@example.com --plan premium
```

## Log applications over HTTP

One request stores an application with its posting and resume. `GET /api/intake/applications` describes the fields with an example, and `POST /api/intake/applications` stores everything together, or nothing if it fails. `make token EMAIL=you@example.com` saves an API token for it to `~/.config/callback/env`.

## Stack

| Layer | Choice |
| --- | --- |
| Frontend | React, TypeScript, Vite, Mantine, Recharts, TanStack Query |
| Backend | FastAPI, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 17 |
| Model calls | Claude Haiku and Sonnet, from the `tailor` container only |
| Edge | nginx: static files, reverse proxy, rate limiting, security headers |
| Packaging | Docker Compose |

Docker Compose runs these containers:

![Callback's containers: the browser reaches web, which proxies to api. api, worker, scout-worker, catalog-worker and billing all use db. worker and scout-worker send work to tailor, the only container that calls the Anthropic API, and tailor sends LaTeX to compiler.](docs/architecture.svg)

| Container | Does |
| --- | --- |
| `web` | nginx: serves the frontend and proxies `/api`. The only container with a published port |
| `api` | The FastAPI app, which also searches the job boards |
| `db` | Postgres, on an internal network with no internet access |
| `worker` | The queue: tailorings, company research, rejection reports, posting text and PDF builds |
| `scout-worker` | Scout runs, one at a time |
| `catalog-worker` | Reads companies' own job boards into the shared catalog |
| `tailor` | The only container that makes model calls |
| `compiler` | Runs LaTeX with shell escape off, no secrets, no database and no internet |
| `billing` | Reads Anthropic's usage and cost reports for the Spend tab |

## Develop

```bash
make test
```

Start the stack in dev mode, and the API restarts whenever you save a file under `backend/app`:

```bash
make dev
```

In a second terminal, run the frontend with hot reload on http://localhost:5173:

```bash
make web
```

Run `make up` again after changing Python dependencies or `backend/Dockerfile`. Schema changes go through Alembic. With the stack running, generate a migration and commit it:

```bash
docker compose run --rm -v "$PWD/backend/alembic:/srv/alembic" migrate alembic revision --autogenerate -m "describe change"
```

Scout was once called SmartSearch, and its database tables and settings keys keep that name.

## License

MIT. Inter is under the SIL Open Font License (`frontend/src/fonts/OFL.txt`).
