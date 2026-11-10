"""Scout: a run reads the shared catalog of open jobs, rules out in code what the brief
can't want, and has Haiku screen and Sonnet verify the rest (engine). The package also owns a run's
stop limits and charges (limits), the code gate (match, filters, metros), what runs remember from
the judge (judgment_memory), briefs on demand (briefs), leads turned into postings (leads) and the
run read back (report). Model calls go to the tailor service; nothing here calls Anthropic.

Worker: python -m app.scout.worker, run by scout-worker. Spec: docs/scout_spec.md.
"""
