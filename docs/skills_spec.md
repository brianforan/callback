# Skills and role types per profile: matching for any field

Status: spec, 2026-11-10. Chunk 1 is built (2026-11-10); the rest isn't. It changes how postings are parsed and scored
(`app/postings/parser.py`, `app/resumes/matching.py`, `app/resumes/structure.py`) and everything
that reads their skills.

## Why

The match score's formula fits any job: required skills, title, seniority, education and role
type. Its word lists only fit software:

- **Skills.** The parser knows about 120 software technologies (`_TECHNOLOGIES`). An accounting
  posting yields none, so the required-skills part, 45 of the 100 points, falls back to a neutral
  50, and "missing skills" is always empty.
- **Role types.** The 11 role families (backend, devops, QA…) are software's. Every other job is
  "other", so the role-type part drops out.
- **Title synonyms** (developer → engineer) are software's.
- **Levels.** "Staff" and "architect" are read as levels above senior. "Staff Accountant" is an
  entry-level job, and an architect who designs buildings isn't a principal engineer.
- **Degree field.** A degree line with no level counts as a bachelor's only when it names
  computer science, engineering or STEM.

A profile is already "one side of your job search, such as software engineering or accounting".
It becomes the place these lists live.

## Goals

1. Match scores, missing skills, Insights, tailoring checks, rejection reports and Scout work for
   any field, from vocabularies each profile owns.
2. **No model calls.** Every term is added by the user, or suggested by code and accepted by the
   user.
3. Software profiles work as they do today, with no setup.
4. A new profile in another field gets useful scores within minutes: upload a resume, save a few
   postings, accept the suggestions.

## Rules that hold everywhere

- **The formula stays.** Weights and parts are unchanged. Only the vocabularies behind them
  change.
- **The built-in software list stays in code** with its hand-tuned patterns ("Go", "Java", "C#").
  A profile turns it on or off. It's on for every profile that exists when this ships, and for a
  new user's first profile.
- **A user's vocabulary is the union of their profiles' terms.** Postings belong to the user, not a
  profile, so a posting's skills are parsed once with the whole union and stored as today. Scoring
  under a profile grades those skills against that profile's resumes. An accounting posting viewed
  under a software profile scores low because its skills are missing, which is right.
- **The fact sheet is optional.** Nothing depends on it. Its Skills status rows only show up as
  suggestions, and tailoring reads it as it does now.
- In the UI, "technologies" becomes "skills". Tables and columns keep their names
  (`posting_technologies`), as Scout's tables kept `smartsearch`.

## Data model

`profiles`
- `software_skills` boolean, not null, default false. The migration sets it true for every
  existing profile. When true, the profile's vocabulary includes the built-in software list and its
  title synonyms.

`profile_terms` (new)
- `id`, `profile_id` (cascade), `name` varchar(80), `aliases` JSON list of strings,
  `kind` varchar(12): `skill` or `credential`, `status` varchar(10): `active` or `ignored`,
  `created_at`.
- Unique on (`profile_id`, lower(`name`)). At most 300 rows a profile.
- `ignored` rows are suggestions the user turned down, kept so they're never suggested again.

`profile_role_types` (new)
- `id`, `profile_id` (cascade), `name` varchar(40), `title_words` JSON list of strings,
  `position` int.
- Unique on (`profile_id`, `name`). At most 30 a profile.
- When `software_skills` is turned on, or by the migration for existing profiles, the 11 current
  role families are added as rows in today's order, with today's title patterns written as words
  ("back end", "backend", "server" for Backend). After that they're the user's to edit.

`users`
- `vocabulary_version` int, default 0. Goes up whenever any of the user's profiles changes its
  terms or turns the software list on or off.

`job_postings`
- `skills_version` int, default 0: the `vocabulary_version` its skills were parsed with.
- `role_family` is replaced by `role_type` varchar(40), nullable, holding only a user's
  correction. The migration copies `role_family` into `role_type`, as its label ("Backend"), only
  where `role_family` is in `corrected_fields`, then drops the column. The `RoleFamily` enum goes.

`resumes.variant_rules` stores role type names instead of `RoleFamily` values. The migration maps
each value to its label.

Nothing changes on the shared catalog.

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

All of these show on the Skills tab. Only the first is active without a click.

1. **Added by hand** on the Skills tab.
2. **Suggested from the profile's postings.** Code reads the requirement and nice-to-have lines of
   the postings made under the profile (the same section labelling the parser uses today) and
   collects candidates:
   - runs of one to four capitalised words, and all-caps tokens of two or more letters ("Accounts
     Payable", "GAAP", "NetSuite"), leaving out a fixed list of common English and job-ad words
     ("Strong", "Experience", "Ability", "Team");
   - a candidate counts once per posting. It's suggested once it appears in 3 postings, or 2 when
     the profile has fewer than 10, and isn't already a term or ignored. The 30 most common show,
     each with how many postings named it.
3. **Suggested from the profile's resumes**: the items on lines in a Skills-type section ("Skills",
   "Technical skills", "Tools", "Certifications") split on commas, semicolons and pipes, after the
   "Label:" part.
4. **Suggested from the fact sheet**, when there is one: each Skill cell of the Skills status table.

**Add** makes a suggestion an active term, **Credential** adds it as a credential, and **Ignore**
stores it as ignored. **Add all from resumes** adds every resume suggestion at once. Suggestions
are worked out when the tab opens, with no stored state beyond the ignored rows.

## Role types

Each profile has an ordered list of role types, each a name and the title words that mark it. A
title gets the first role type with a word in it, matched as whole words, case-insensitively. Order
matters, as it does today: Management comes before Full stack so "Engineering Manager, Full Stack"
is management.

A job's role type isn't stored. It's read from the title when it's needed, which is cheap:

- for match scores, Job listings filters, baseline rules and "which baseline to start from": the
  active profile's role types;
- for Insights → Postings: the role types of the profile each posting was made under;
- a correction on the posting (`role_type`) wins over both.

The role-type part of the score uses the active profile's applications. It drops out when the
profile has no role types or the title matches none, as "other" does today.

Title synonyms apply only when the profile uses the software list.

## Parser changes

- `parse_posting` and `parse_requirements` take the user's vocabulary (built-in patterns plus
  every active term of every profile) instead of reading `_TECHNOLOGY_PATTERNS` directly. The
  section labelling, years-per-skill and requirement levels are unchanged.
- `parse_role_family` is replaced by `role_type(title, role_types)`.
- **Levels:** "staff" and "architect" count as levels only in a title that also has a software
  role word (engineer, developer, software, SRE, data, cloud, platform, solutions, systems,
  security, ML). Otherwise the level comes from the years asked, as it does when a title names
  none.
- **Degree:** a line that says "degree in" a field, with no level named, counts as a bachelor's
  whatever the field.

## Keeping postings current

When `vocabulary_version` goes up, the worker re-reads skills for the user's postings whose
`skills_version` is behind, a batch at a time, after its other work. Postings with skills in
`corrected_fields` keep them. The posting drawer says "Updating skills…" while a posting is
behind.

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

Resumes gets a fourth tab, **Skills**, for the active profile:

- **Use the built-in software skills**: the switch for `software_skills`, with how many terms it
  adds.
- **Your skills**: the active terms, each with its aliases, kind and how many of your postings name
  it. Edit, delete, or switch between skill and credential.
- **Suggestions**: from postings, resumes and the fact sheet, each labelled with where it came
  from.
- **Role types**: the ordered list, with title words. Add, edit, delete and reorder.
- **Ignored**: suggestions you turned down, with **Restore**.

**Manage profiles → Add** asks whether the new profile uses the built-in software skills, off by
default.

## Chunks

1. **Terms and the parser.** `profile_terms`, `software_skills`, `vocabulary_version`,
   `skills_version`; patterns from terms; the parser, resume evidence, Insights and the re-read
   worker use the user's vocabulary; Skills tab with the switch and hand-added terms. Software
   profiles score exactly as before, which the existing tests check. Built with only the columns
   it uses: `profile_terms.status` comes with chunk 2 and `kind` with chunk 4. Job listings'
   search words, and the UI's "technologies" wording outside the Skills tab, are left for
   chunk 5.
2. **Suggestions.** From postings, resumes and the fact sheet, with Add, Ignore and Add all.
3. **Role types.** `profile_role_types`, the `role_type` correction, baseline rules and Job
   listings filters by name, the migration from `RoleFamily`, and the Role types section.
4. **Credentials, levels and degrees.** Credential terms as hard filters, the "staff" and
   "architect" rule, and "degree in" any field.
5. **Scout and tailoring.** The Must use picker, the code gate and tailoring's check read the
   active profile's vocabulary.

Each chunk is usable on its own, and the README changes with each.

## Not in this spec

- Company hiring history (`app/research/history.py`) counts engineering roles with the software
  role families. It stays software-only for now.
- Built-in lists for other fields (accounting, nursing). Suggestions are meant to make them
  unnecessary; one can be added later the way the software list is.
- Learning role types from the titles you've applied to. Role types are written by hand, apart
  from the software set.
- Weights per profile.
