# Skills, role types and levels for any field

Status: spec, 2026-11-10, revised the same day for shared field pools, model suggestions and
employer title ladders. Chunk 1 is built (2026-11-10); the rest isn't. It changes how postings are
parsed and scored (`app/postings/parser.py`, `app/resumes/matching.py`, `app/resumes/structure.py`,
`app/vocabulary.py`) and everything that reads their skills.

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
  software searches.
- **Degree field.** A degree line with no level counts as a bachelor's only when it names
  computer science, engineering or STEM.
- **Cold start.** Even with suggestions from code, a new field needs a few postings before a
  phrase repeats often enough to be suggested, and every user in that field starts from nothing.

## Goals

1. Match scores, missing skills, Insights, tailoring checks, rejection reports and Scout work for
   any field.
2. **Data first, model second.** Code matches what's already known. A model call sees only the
   phrases and titles code can't place, and its answers are stored, so the same phrase never goes
   to a model twice in one field. Calls fall off as a field's vocabulary fills.
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
- **Admin → Fields** lists fields with how many profiles each has. The admin can rename a field,
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

`company_ladders` (new): what a company's rank words mean, learned from the shared catalog.
- `company_key` varchar(200), `word` varchar(40), `level` varchar(20) (a `PostingLevel` value),
  `jobs` int, `updated_at`. Primary key (`company_key`, `word`).

`users`
- `vocabulary_version` as built. It goes up for every user with a profile in a field whenever that
  field's terms change, and for one user when their profile changes field, turns the software list
  on or off, or turns down a name.

`job_postings`
- `skills_version` as built.
- `role_family` is replaced by `role_type` varchar(40), nullable, holding only a user's
  correction. The migration copies `role_family` into `role_type`, as its label ("Backend"), only
  where `role_family` is in `corrected_fields`, then drops the column. The `RoleFamily` enum goes.
- `vocab_checked_at` timestamptz nullable and `vocab_check_attempts` int default 0, for the model
  check below.

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
3. **Suggested from the profile's postings.** Code reads the requirement and nice-to-have lines of
   the user's postings made under profiles in this field (the same section labelling the parser
   uses today) and collects candidates:
   - runs of one to four capitalised words, and all-caps tokens of two or more letters ("Accounts
     Payable", "GAAP", "NetSuite"), leaving out a fixed list of common English and job-ad words
     ("Strong", "Experience", "Ability", "Team");
   - a candidate counts once per posting. It's suggested once it appears in 3 postings, or 2 when
     the user has fewer than 10 in the field, and isn't already in the profile's vocabulary or
     turned down. The 30 most common show, each with how many postings named it.
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
is management. A rank word (see Levels) marks a role type only when the posting's text shows
people management, so "Vice President, Software Engineer" isn't Management.

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
(intern, junior, senior, principal, manager, head of, the numbered ladders I to IV). **Rank words**
don't, because employers use them for different rungs: associate, assistant vice president (AVP),
vice president (VP), senior vice president (SVP), director, executive director, managing director,
staff and architect.

A posting's level is the first of these that applies:

1. A correction on the posting.
2. "Staff" or "architect" in a title that also has a software role word (engineer, developer,
   software, SRE, data, cloud, platform, solutions, systems, security, ML): staff or principal, as
   today.
3. A title with no rank word: today's title rules, then years asked, as today.
4. A rank word other than associate, staff and architect, when the text shows people management
   ("direct reports", "manage a team", "lead a team of", "people manager", "hire and develop"):
   manager.
5. Years asked, when the posting names them: under 2 junior, under 5 mid, under 8 senior, else
   staff, as `_parse_level` does today when a title names no level. "Senior Associate Scientist,
   3+ years" is mid.
6. The company's ladder: `company_ladders` for the posting's company key and that word.
7. The built-in individual-contributor ladder: associate junior, AVP mid, VP senior, SVP, director
   and executive director staff, managing director principal, staff (outside software) junior,
   architect (outside software) no level.

**Company ladders.** Once a day the catalog worker reads the shared catalog. For each company and
rank word, it takes the jobs with that word in the title whose text asks for years, turns each
into a level by the bands in step 5, and stores the most common one when there are at least 2 such
jobs. Only the catalog is read, not anyone's postings.

Levels never call a model: step 4's text check settles the case a model would have been asked
about, a lone posting with a rank word and no years.

## Parser changes

- `parse_posting` and `parse_requirements` take the user's vocabulary (built in chunk 1).
- `parse_role_family` is replaced by `role_type(title, text, role_types)`.
- `_parse_level(title, years)` becomes `_parse_level(title, years, text, ladder)` and follows
  Levels.
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
2. **Levels.** Rank words, the people-management check, years, the individual-contributor ladder,
   company ladders, the rank-word rule for the Management role family, and the "staff" and
   "architect" rule. Needs no fields, and fixes bank postings for software profiles.
3. **Fields.** `fields`, `field_terms`, `profiles.field_id`, `profile_terms` as turned-down names,
   the migration, version bumps across a field, the Field line and shared Edit on the Skills tab,
   and Admin → Fields.
4. **Code suggestions.** From postings, resumes and the fact sheet, with Add, Ignore and Add all.
5. **Role types.** `field_role_types`, the `role_type` correction, baseline rules and Job listings
   filters by name, the migration from `RoleFamily`, and the Role types section.
6. **Model suggestions.** Leftovers, `field_phrases`, `field_titles`, the tailor worker's call,
   `vocab_checked_at`, the daily limit and Spend.
7. **Credentials and degrees.** Credential terms as hard filters, and "degree in" any field.
8. **Scout and tailoring.** The Must use picker, the code gate and tailoring's check read the
   active profile's vocabulary.

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
- Company industry. Ladders come from a company's own catalog jobs, not from research's `industry`.
- "Product Manager" and "Account Manager" are still read as manager level. "Manager" isn't a rank
  word.
- Weights per field or profile.
