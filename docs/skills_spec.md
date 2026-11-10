# Skills, role types and levels for any field

Status: spec, 2026-11-10, revised the same day for shared field pools, catalog suggestions,
model suggestions only if needed, and employer rank words, and again for Scout's work boundary
and Manager titles. Chunks 1, 2, 9, 10 and 11 are built (2026-11-10); the rest isn't. It changes
how postings are parsed and scored (`app/postings/parser.py`, `app/resumes/matching.py`,
`app/resumes/structure.py`, `app/vocabulary.py`) and everything that reads their skills.

## Why

The match score's formula fits any job: required skills, title, seniority, education and role
type. Its word lists only fit software:

- **Skills.** The parser knows about 120 software technologies (`_TECHNOLOGIES`). An accounting
  posting yields none, so the required-skills part, 45 of the 100 points, falls back to a neutral
  50, and "missing skills" is always empty.
- **Role types.** The 11 role families (backend, devops, QA…) are software's. Every other job is
  "other", so the role-type part drops out.
- **Title synonyms** (developer → engineer) are software's.
- **Levels.** Employers use the same words for different rungs. "Staff Accountant" is an
  entry-level job, and an architect who designs buildings isn't a principal engineer. Banks give
  individual contributors titles like Associate, Vice President, Senior Vice President, Director
  and Executive Director, so "Vice President, Software Engineer" is read today as a manager-level
  job with the Management role type (`_TITLE_LEVELS`, `_ROLE_FAMILIES`). That one already hurts
  software searches. Any title with "Manager" is read as a people manager too, though most aren't:
  of 2,794 open catalog jobs with "Manager" in the title, 711 show people management. Scout's
  default levels leave managers out, so Brand, Product, Program and Account Manager jobs never
  reach its judge, and a marketer's main titles are lost.
- **Degree field.** A degree line with no level counts as a bachelor's only when it names
  computer science, engineering or STEM.
- **Scout's titles.** "Titles to search under" offers seven fixed software phrases, and with
  none picked Scout only lets plain engineering titles through. Its judge never sees the
  baselines, so with a vague brief ("anything, I'm desperate") and no fact sheet it keeps
  whatever the title check let in. A pharma marketing baseline got 25 software leads.
- **Cold start.** Even with suggestions from code, a new field needs a few postings before a
  phrase repeats often enough to be suggested, and every user in that field starts from nothing.

## Goals

1. Match scores, missing skills, Insights, tailoring checks, rejection reports and Scout work for
   any field.
2. **Data first, model second.** A field's pool is seeded from the shared catalog's postings by
   code. A model call, if chunk 6 is ever built, sees only the phrases and titles code can't
   place, and its answers are stored, so the same phrase never goes to a model twice in one field.
3. **Nothing a model says affects a score until a person accepts it.**
4. Software profiles work as they do today, with no setup.
5. A new profile gets useful scores within minutes, and at once when someone in its field came
   before.

## Rules that hold everywhere

- **The formula stays.** Weights and parts are unchanged. Only the vocabularies behind them
  change.
- **The built-in software list stays in code** with its hand-tuned patterns ("Go", "Java", "C#").
  A profile turns it on or off, whatever its field: a pharma tech profile may want Python and SQL
  as well as GxP.
- **A field's pool is shared by everyone with a profile in that field.** The pool holds term names,
  aliases, kinds and role types. It never holds postings, resumes, or who added what.
- **A profile's vocabulary** is the built-in list (when its switch is on) plus its field's terms,
  minus the names the profile turned down.
- **A user's vocabulary is the union of their profiles' vocabularies.** Postings belong to the
  user, not a profile, so a posting's skills are parsed once with the union and stored as today.
  Scoring under a profile grades those skills against that profile's resumes.
- **The fact sheet is optional.** Nothing depends on it.
- In the UI, "technologies" becomes "skills". Tables and columns keep their names
  (`posting_technologies`), as Scout's tables kept `smartsearch`.

## Fields

A field is one line of work: Software engineering, Pharma tech, Accounting. Every profile has
exactly one.

- **Choosing one.** Manage profiles → Add asks for a field: pick one from the list of names, or
  type a new one. A typed name that matches an existing one after lowercasing and collapsing spaces
  and hyphens joins that field. A user's first profile, made automatically, is in Software
  engineering with the software list on, as today.
- **Changing one.** The Skills tab can move a profile to another field. Its turned-down names are
  dropped, since they were against the old pool.
- **Admin → Fields** lists fields with how many profiles each has and, from chunk 5, the average
  leftover phrases per posting over the last 30 days. The admin can rename a field,
  merge two (profiles, terms and role types move; terms with the same name merge their aliases), and
  remove a term or role type from a pool. No one else can remove a pool term; they turn it down for
  their own profile.

## Data model

`fields` (new)
- `id`, `name` varchar(60), `created_at`. Unique on lower(`name`).

`profiles`
- `field_id`, not null, references `fields` (restrict).
- `software_skills` stays as built in chunk 1.

`field_terms` (new): the shared pool of skills.
- `id`, `field_id` (cascade), `name` varchar(80), `aliases` JSON list of strings,
  `kind` varchar(12): `skill` or `credential`, `created_at`, `updated_at`.
- Unique on (`field_id`, lower(`name`)). At most 1,000 a field.

`profile_terms` (built in chunk 1) becomes the names a profile turned down.
- `id`, `profile_id` (cascade), `name` varchar(80), `created_at`. The `aliases` column goes.
- Unique on (`profile_id`, lower(`name`)).
- A name here is left out of the profile's vocabulary when it's a pool term, and is never
  suggested to that profile again.

`field_role_types` (new)
- `id`, `field_id` (cascade), `name` varchar(40), `title_words` JSON list of strings,
  `position` int.
- Unique on (`field_id`, `name`). At most 30 a field.

`field_phrases` (new): the model's answers about phrases, kept so it's never asked twice.
- `id`, `field_id` (cascade), `phrase_key` varchar(120) (lowercased, spaces and hyphens collapsed),
  `verdict` varchar(10): `term` or `not_term`, `name` varchar(80) nullable, `aliases` JSON list,
  `kind` varchar(12) nullable, `model` varchar(40), `created_at`.
- Unique on (`field_id`, `phrase_key`). Never shown to anyone as it is.

`field_titles` (new): the model's answers about titles that matched no role type.
- `id`, `field_id` (cascade), `title_key` varchar(200) (the catalog's title key),
  `role_type` varchar(40) nullable, `new_word` varchar(40) nullable, `model` varchar(40),
  `created_at`.
- Unique on (`field_id`, `title_key`). `role_type` with no `new_word` means an existing role type
  fits; with one, it's a new role type marked by that word; both empty means none fits.

`field_catalog_phrases` (new): candidate phrases from catalog jobs in a field, rebuilt daily.
- `field_id` (cascade), `phrase` varchar(120), `companies` int, `updated_at`. Primary key
  (`field_id`, lower(`phrase`)). Only phrases used by 3 or more companies are kept.

`users`
- `vocabulary_version` as built. It goes up for every user with a profile in a field whenever that
  field's terms change, and for one user when their profile changes field, turns the software list
  on or off, or turns down a name.

`job_postings`
- `skills_version` as built.
- `role_family` is replaced by `role_type` varchar(40), nullable, holding only a user's
  correction. The migration copies `role_family` into `role_type`, as its label ("Backend"), only
  where `role_family` is in `corrected_fields`, then drops the column. The `RoleFamily` enum goes.
- `leftover_phrases` int nullable: how many candidate phrases the posting had that the
  profile's vocabulary didn't place, set when a new or edited posting is parsed (chunk 5).
- `vocab_checked_at` timestamptz nullable and `vocab_check_attempts` int default 0, for the model
  check below (chunk 6 only).

`smartsearch_settings.title_families` keeps its name and JSON type but holds the profile's
phrases instead of `TitleFamily` values. The migration turns each stored value into its phrase
("forward_deployed" into "forward deployed", "solutions" into "solutions engineer", and so on, as
`_TITLE_FAMILY_QUERIES` writes them), and the `TitleFamily` enum goes.

`resumes.variant_rules` stores role type names instead of `RoleFamily` values. The migration maps
each value to its label.

The shared catalog's existing tables don't change.

**Migration from chunk 1.** It creates a Software engineering field and puts every profile with the
software list on into it. Each profile with the list off gets a field named after the profile,
with matching names joined. Each profile's terms move into its field's pool (a duplicate name
merges aliases), and `profile_terms` is emptied and loses `aliases`. Software engineering gets the
11 current role families as role types, in today's order, with today's title patterns written as
words ("back end", "backend", "server" for Backend).

## Terms

A term is a name plus aliases, such as "Accounts Payable" with alias "AP". Code builds one pattern
per term:

- Case-insensitive, with spaces and hyphens interchangeable, and the same word boundaries the
  built-in list uses (`_BEFORE`, `_AFTER`).
- A name or alias of four characters or fewer, written in capitals ("AP", "GL", "CPA"), matches
  only in capitals.
- A name of five or more characters that ends in a letter also matches with "s" or "es" added.
- A term with the same name as a built-in one uses the built-in pattern.

A **credential** is a term that works like security clearance does today: when a posting requires
it and the resume doesn't name it, the score is capped at 40, and the checklist shows it as a hard
filter. Security clearance stays built in for every profile.

## Where terms come from

All of these show on the Skills tab.

1. **The field's pool.** Active for the profile without a click.
2. **Added by hand** on the Skills tab. It joins the field's pool.
3. **Suggested from postings.** Code reads requirement and nice-to-have lines (the same section
   labelling the parser uses today) and collects candidates: runs of one to four capitalised
   words, and all-caps tokens of two or more letters ("Accounts Payable", "GAAP", "NetSuite"),
   leaving out a fixed list of common English and job-ad words ("Strong", "Experience",
   "Ability", "Team"). It reads two sets of postings:
   - **The shared catalog.** Open catalog jobs whose title has a title word of one of the field's
     role types. A candidate counts once per company and is suggested once 3 companies use it.
     Counting companies rather than jobs keeps out company names, product names and one
     employer's boilerplate, which only a large set of postings can do. Worked out once a day by
     the catalog worker and stored per field (`field_catalog_phrases`: `field_id`, `phrase`,
     `companies`, `updated_at`), since it reads thousands of jobs.
   - **The user's own postings** made under profiles in this field. A candidate counts once per
     posting and is suggested once it appears in 3, or 2 when the user has fewer than 10 in the
     field.

   Neither suggests anything already in the profile's vocabulary or turned down. The 30 most common
   show, each with how many companies or postings named it.
4. **Suggested from the profile's resumes**: the items on lines in a Skills-type section ("Skills",
   "Technical skills", "Tools", "Certifications") split on commas, semicolons and pipes, after the
   "Label:" part.
5. **Suggested from the fact sheet**, when there is one: each Skill cell of the Skills status table.
6. **Suggested by the model** (next section), labelled "From the model".

**Add** puts a suggestion in the field's pool, **Credential** adds it as a credential, and
**Ignore** turns it down for this profile. **Add all from resumes** adds every resume suggestion at
once. **Edit** on a pool term changes its name, aliases or kind for everyone in the field, and says
so. **Remove** on a pool term turns it down for this profile only. Suggestions are worked out when
the tab opens.

A suggestion of any kind only ever comes from the user's own postings, resumes and fact sheet, so
no one learns what anyone else applied to. Other people in the field see a term only after
someone accepted it into the pool.

## Model suggestions

**Built only if needed.** Code suggestions from the catalog may leave little for a model to do.
From chunk 5, the worker stores each new or edited posting's leftover count (below) in
`job_postings.leftover_phrases`, and Admin → Fields shows each field's average over the last 30
days. Chunk 6 is built only if, after a few weeks of real use, fields outside Software engineering
still average enough leftovers to matter. That threshold is the admin's call, made with the
number in front of them. Until then nothing in this spec calls a model.

**When.** After the worker parses a new or edited posting, it collects the posting's leftovers:

- **Phrases.** The candidates from source 3 on its requirement and nice-to-have lines, minus
  anything in the profile's vocabulary or the built-in list, anything already in the field's
  `field_phrases`, anything the profile turned down, and the stop list.
- **The title**, when it matches none of the field's role types and its title key isn't in
  `field_titles`.

No leftovers means no call, which should be most postings once a field fills in. A re-read after a
vocabulary change never triggers a call. Postings saved before this chunk ships aren't sent.

**The call.** One Haiku call (`claude-haiku-5-5`) from the tailor worker, the way rejection analysis
calls it. It's sent the field's name, the field's role type names, up to 25 leftover phrases, each
with the line it came from, and the title. It isn't sent the rest of the posting, the resume or
anything about the user. It answers in JSON: for each phrase, `term` (with a name, aliases and
`skill` or `credential`) or `not_term`; for the title, an existing role type, a new role type with
the title word that marks it, or none.

**Checks before anything is stored.** Each answer must be about a phrase that was sent. A term's
name or one of its aliases must appear in its phrase or line, ignoring case. Kinds must be one of
the two. Names are at most 80 characters and role type names at most 40. An item that fails is
dropped and not stored, so it's asked again the next time it turns up. A call that fails stores
nothing; the posting is tried again on the worker's next pass, up to 3 attempts, then marked
checked and the failure logged.

**What's stored.** Answers go into `field_phrases` and `field_titles` for the whole field, so
nobody in the field pays for that phrase again. A `term` answer shows as a suggestion to any user
whose own posting contains that phrase. A title answer shows as a role type suggestion ("Add
'validation' to Quality", or "New role type Clinical data, marked by 'clinical data'") to any user
with a posting under that title key. A `not_term` answer is never shown; it only stops the phrase
from being sent again.

**Limits.** `vocabulary_daily_limit` in Admin → Settings (`app_config`), per user per UTC day,
default 20 calls. `override_limits` lifts it. Postings over the limit wait for the next day. The
cost shows in Spend like other calls.

## Role types

Each field has an ordered list of role types, each a name and the title words that mark it. A
title gets the first role type with a word in it, matched as whole words, case-insensitively. Order
matters, as it does today: Management comes before Full stack so "Engineering Manager, Full Stack"
is management. A director or VP who names an individual contributor's job marks a management
role type only when the posting's text shows people management (Levels, step 4), so "Vice
President, Software Engineer" isn't Management.

Anyone with a profile in the field can add, edit, delete and reorder its role types on the Skills
tab, and accept the model's role type suggestions.

A job's role type isn't stored. It's read from the title when it's needed:

- for match scores, Job listings filters, baseline rules and "which baseline to start from": the
  active profile's field;
- for Insights → Postings: the field of the profile each posting was made under;
- a correction on the posting (`role_type`) wins over both.

The role-type part of the score uses the active profile's applications. It drops out when the
field has no role types or the title matches none, as "other" does today.

Title synonyms apply only when the profile uses the software list.

## Levels

The level words are built in and the same for every field. Most mean the same thing everywhere
(intern, junior, senior, principal, head of, staff, architect, the numbered ladders I to IV).
"Manager" doesn't: most Manager titles (product, brand, program, project, account, marketing,
customer success) are individual contributors, so it means a people manager only as step 4
says. **Rank words** don't, because employers use them for different rungs: associate, senior
associate, assistant vice president (AVP), vice president (VP), senior vice president (SVP),
director, executive director and managing director. "Staff" and "architect" are rank words only in
a few known uses, since at most employers they're the staff and principal rungs for any job (Staff
Product Designer, Staff Scientist): Staff Accountant and staff auditor, nurse, attorney,
pharmacist, photographer, writer and assistant; Member of Technical Staff; and landscape,
interior, building and licensed architects.

A posting's level is the first of these that applies:

1. A correction on the posting.
2. A title with no rank word and no "manager": today's title rules, then years asked, as today.
   One with "manager" goes on to step 4, then the plain level words and years of steps 5 and 6.
3. "Intern" in the title: intern.
4. Manager, when any of these holds:
   - the title names running people or a place: engineering manager, software development
     manager, "manager of" or "manager," followed by an engineering field ("Manager, Machine
     Learning Engineering"), people manager, general, store, district, branch, plant, shift,
     restaurant or nurse manager, head of, CTO;
   - the title has "manager" and the text shows people management ("direct reports", "manage a
     team", "lead a team of", "people manager", "hire and develop", "performance reviews", "build
     a team");
   - the title has a director or VP rank word and either names no individual contributor's job
     (engineer, developer, analyst, scientist, programmer, specialist, consultant, designer,
     administrator, accountant, architect, associate, technologist, researcher) or has text
     showing people management.

   "Director, Embedded Systems" and "Engineering Manager" are managers, as before; "Vice
   President, Software Engineer", "Brand Manager" and "Senior Product Manager" aren't unless
   their text says they manage people. A Manager title that isn't a manager takes its level from
   the steps below, as a title without "manager" would: "Senior Product Manager" senior,
   "Associate Brand Manager" junior, "Brand Manager, 5+ years" senior, and with neither, no level,
   so Scout's judge decides. "Business development manager" and "leadership development manager"
   aren't on the list: they're individual contributors.
5. A plain level word beside the rank word (principal, senior, junior, the numbered ladders):
   that level. "Principal Architect" is principal, "Vice President, Software Engineer III"
   senior.
6. Years asked, when the posting names them: under 2 junior, under 5 mid, under 8 senior, else
   staff, as `_parse_level` does today when a title names no level. "Senior Associate Scientist,
   3+ years" is mid ("senior associate" is one rank word, so "senior" isn't a plain word here).
7. The built-in individual-contributor ladder: associate junior, senior associate mid, AVP mid,
   VP senior, SVP, director and executive director staff, managing director principal, the staff
   uses junior, Member of Technical Staff and the architect uses no level.

The role family follows step 4: a title is Management exactly when step 4 makes it a manager, so
a Product or Brand Manager takes the next role family its title matches, or other. Company hiring
history, which has only titles, keeps today's title-only reading.

Measured on the local catalog's 2,794 open jobs with "Manager" in the title: 433 stay managers by
title and 476 by text; 628 become senior, 108 staff, 37 principal and 44 junior or mid from title
words; 638 take a level from years asked; 419 have no level. Saved postings keep the level they
were parsed with until they're read again (`make postings`), as after chunk 2; catalog jobs are
read when Scout scans them.

Levels never call a model and use no stored data. Measured on the local catalog (18,853 jobs),
only 137 reach step 7, mostly Associate titles that name no years, so a ladder learned per company
was left out.

## Scout's title phrases

Scout's "Titles to search under" are today seven fixed software phrases (`TitleFamily` in
`app/scout/limits.py`, their searches in `_TITLE_FAMILY_QUERIES`). They become the profile's own
phrases. What someone searches for is personal, so the phrases stay on the profile and never join
the field's pool, and they aren't role types: role types say what kind of job a title is, title
phrases say which titles to look at.

- **Editing.** The chips become editable: type a phrase to add it, × to remove one. Up to 15
  phrases a profile, each 2 to 60 characters, unique ignoring case. A profile that uses the
  built-in software list shows today's seven as suggestions to click on; other profiles start
  empty.
- **Matching.** Each phrase is matched as an exact phrase, as the seven are today
  (`"forward deployed"`), wherever Scout uses title families: the title gate, the +2 rank for a
  phrase in the title, and the board searches when there's no Must use list.
- **With none picked.** Built in chunk 9 as today's plain engineering title (`_ENGINEERING_TITLE`
  in `app/scout/engine.py`) for every profile. Chunk 10 replaces it with the titles of the
  profile's work boundary (see Scout's work boundary), and the engineering fallback goes.
- **Earlier verdicts still apply.** Scout's judgment memory and its skip list of pages earlier
  runs turned down are keyed by the brief, and the brief includes the titles (`brief_key` in
  `app/scout/judgment_memory.py`, `_JUDGED_BY` in `app/scout/leads.py`). Both read the titles
  through one function that turns each of the seven old phrases back into its old value
  ("forward deployed" into `forward_deployed`) and leaves any other phrase as typed, lowercased. A
  brief whose titles didn't change keeps its key, so the migration doesn't make Scout judge every
  job again; a new phrase makes a new key, as any changed brief does today.
- **Fenced in prompts.** The phrases are typed by users, so tailor's judge and brief prompts
  (`tailor/tailor/pipeline.py`, `tailor/tailor/companies.py`) wrap them with `_fence` like the
  description and Must use list, instead of listing them as plain text.

## Scout's work boundary

A run with no Must use list and no title phrases still has to know what work to look for. Today
it falls back to engineering titles for every profile, and the judge, which sees the brief and
fact sheet but not the baselines, keeps anything a vague brief allows. Instead, one Haiku call
reads the profile's own material and sets the boundary of the work, and Scout never falls back to
titles unrelated to the profile.

- **What it is.** A **work line**, one sentence of at most 200 characters ("Pharmaceutical brand
  marketing, commercial analytics and pharma sales, in cardiometabolic and respiratory
  medicines"), and up to 10 **boundary titles**. The titles are the roles the person has held,
  without level or specialty words ("sales representative", not "senior specialty sales
  representative"), plus the natural next steps and close neighbours ("brand manager", "product
  manager", "marketing manager"). Each title follows the title phrase rules (2–60 characters,
  unique ignoring case); code drops any that don't.
- **What it reads.** The profile's baselines as text, its fact sheet and the brief's description,
  each fenced. When the description names different work from the baselines ("moving into UX
  research"), the description wins.
- **When it's made.** By the Scout worker at the start of a run, before the scan, when the profile
  has none or its inputs changed since it was made (a hash of the baselines' text, the fact sheet
  and the description). It's tailor's pipeline Haiku model, metered like any stage: its cost is
  the run's, and the run's report shows it as a step ("Read your work as: …"). If the call fails,
  the run uses the last boundary with a note; with none, the run fails without counting toward
  the day's runs.
- **Stored** on `smartsearch_settings.work_boundary` (JSON: work, titles, inputs key, made at,
  edited), so it's per profile like the rest of Scout's settings.
- **The title check.** With no Must use list and no title phrases picked, the boundary titles act
  as the picked phrases: the title gate, the +4 rank for one in the title, the SQL prefilter and
  the board searches. With phrases picked or a Must use list, the boundary titles do nothing
  there. `_ENGINEERING_TITLE`, `_ENGINEERING_WORDS` and the +1 rank for an engineering title go.
- **The judge.** The brief gains `Their work: <work line>`, fenced like the description, for every
  run. The keep rule adds: when the brief's description doesn't name the work, the job's work
  must be the work line's kind of work or a natural next step from it. Verify reads the same
  brief.
- **The brief key** includes the work line and, when they're used, the boundary titles (through
  `title_keys`). So a changed boundary judges jobs afresh. Adding it changes every existing brief's
  key once, so the next run of each profile judges its remembered jobs again, about the cost of
  one ordinary run.
- **Nothing to go on.** Starting a run already needs a description, so the rule is the read's:
  when it finds no work (no line or no usable title), as with "anything" and no baselines or fact
  sheet, the run fails before any posting is judged, without counting toward the day's runs. The
  message says what to add: a baseline, a title to search under or a Must use list.
- **Used at once, shown and editable.** The boundary is used as soon as it's made; nothing waits
  for the person to accept it, since a run with too little to go on is exactly when it's needed.
  The Scout page shows it under the description: "Scout reads your work as: …" with **Edit**, and,
  when no titles are picked, "Searching under titles from your baselines: …" with **Use and
  edit**, which copies them into Titles to search under. An edited work line is kept as edited and
  not remade; when the inputs changed since, the page says so with **Read again**, which clears
  the edit so the next run remakes it.
- **Every profile.** Software profiles get a boundary too. Their runs with a Must use list keep
  today's title check; only the judge line is new.

## Parser changes

- `parse_posting` and `parse_requirements` take the user's vocabulary (built in chunk 1).
- `parse_role_family` is replaced by `role_type(title, text, role_types)`.
- `_parse_level(title, years)` becomes `_parse_level(title, years, text)` and follows Levels
  (built).
- **Degree:** a line that says "degree in" a field, with no level named, counts as a bachelor's
  whatever the field.

## Keeping postings current

When `vocabulary_version` goes up, the worker re-reads skills for the user's postings whose
`skills_version` is behind, a batch at a time, after its other work (built in chunk 1). Postings
with skills in `corrected_fields` keep them. The posting drawer says "Updating skills…" while a
posting is behind.

Job listings and Scout read posting text when they need it, so they use the new vocabulary at
once.

## What else reads the vocabulary

| Where | Today | After |
| --- | --- | --- |
| Insights → Resumes, skills over time | `TECHNOLOGY_NAMES` | The user's vocabulary |
| Insights → Postings, stack charts | Stored posting skills | Unchanged |
| Resume evidence (`skill_evidence`) | Built-in patterns | The term's pattern |
| Job listings search words | `technology_in_text` | Unchanged for plain words; a word that is a term uses its pattern |
| Tailoring's skill check | Built-in list | The active profile's vocabulary |
| Rejection reports | Stored posting skills | Unchanged |
| Scout's Must use and Rule out picker | `TECHNOLOGY_NAMES` | The active profile's vocabulary |
| Scout's code gate | Built-in patterns | The term's pattern |
| Category labels (language, framework…) | Built-in | Built-in for software; "skill" or "credential" for terms |

## The Skills tab

Resumes has a fourth tab, **Skills**, for the active profile:

- **Field**: its name, with **Change**.
- **Use the built-in software skills**: the switch for `software_skills`, with how many terms it
  adds.
- **Skills**: the field's pool, each term with its aliases, kind and how many of your postings name
  it. Edit (for everyone in the field) or Remove (for this profile).
- **Suggestions**: from postings, resumes, the fact sheet and the model, each labelled with where
  it came from.
- **Role types**: the field's ordered list, with title words, and the model's role type
  suggestions.
- **Turned down**: names this profile turned down, with **Restore**.

## Chunks

1. **Terms and the parser.** Built 2026-11-10, with terms per profile; chunk 3 moves them into
   field pools.
2. **Levels.** Built 2026-11-10. Rank words, the people-management check, years, the
   individual-contributor ladder, the rank-word rule for the Management role family, and the
   "staff" and "architect" exceptions. Needs no fields, and fixes bank postings for software
   profiles.
3. **Fields.** `fields`, `field_terms`, `profiles.field_id`, `profile_terms` as turned-down names,
   the migration, version bumps across a field, the Field line and shared Edit on the Skills tab,
   and Admin → Fields.
4. **Role types.** `field_role_types`, the `role_type` correction, baseline rules and Job listings
   filters by name, the migration from `RoleFamily`, and the Role types section. Comes before
   suggestions, because catalog suggestions pick a field's jobs by its role types' title words.
5. **Code suggestions.** From the catalog (`field_catalog_phrases`), the user's postings, resumes
   and the fact sheet, with Add, Ignore and Add all; `leftover_phrases` and its average on
   Admin → Fields.
6. **Model suggestions, only if needed** (see Model suggestions). Leftovers, `field_phrases`,
   `field_titles`, the tailor worker's call, `vocab_checked_at`, the daily limit and Spend.
7. **Credentials and degrees.** Credential terms as hard filters, and "degree in" any field.
8. **Scout and tailoring.** The Must use picker, the code gate and tailoring's check read the
   active profile's vocabulary.
9. **Scout's title phrases.** Built 2026-11-10. Editable chips, phrases stored per profile, the
   migration of `title_families`, phrase matching in the title gate, rank and board searches, the
   brief-key mapping and fencing in tailor's prompts.
10. **Scout's work boundary.** Built 2026-11-10. The tailor call, `work_boundary` on Scout's
    settings, the boundary as the no-titles fallback in the gate, rank and board searches, the
    Their work line and keep rule in the judge, the brief key, failing a run with nothing to go
    on, the Scout page's lines, and dropping `_ENGINEERING_TITLE`.
11. **Manager titles.** Built 2026-11-10. Step 4 of Levels for "manager": the list of titles
    that run people or a place, the text check for other Manager titles, and the role family
    following it.

Each chunk is usable on its own, and the README changes with each.

## Not in this spec

- Company hiring history (`app/research/history.py`) counts engineering roles with the software
  role families. It stays software-only for now.
- Built-in lists for other fields (accounting, nursing). Field pools are meant to make them
  unnecessary; one can be added later the way the software list is.
- Sending postings saved before chunk 6 to the model. A "Check my postings" button could do it
  under the daily limit.
- A model check for near-duplicate field names ("pharma tech" and "life sciences IT"). The admin
  merges them.
- Company ladders: what each employer's rank words mean, learned from its catalog jobs that ask
  for years. Measured at 47 of 18,853 catalog jobs it would change, so left out; worth another
  look if catalog postings without years grow.
- Company industry as a level signal (research's `industry` covers only researched companies).
- Weights per field or profile.
- Suggesting title phrases from the catalog (phrases that recur across companies' titles among
  jobs the user saved or applied to).
- A **Read my baselines** button that remakes the work boundary before a run. The API never calls
  tailor directly, so it would need a queued job like briefs; until then a run remakes it.
- Scout's fallback by field: the title words of a field's role types when a profile has no
  titles. The work boundary replaces it.
- A warning when a title phrase is so broad ("engineer", "analyst") that it matches most of the
  catalog. Without a Must use list such a phrase lets nearly everything through to the judge.
