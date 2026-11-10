"""Companies: one catalog record per company however it's written (identity), the catalog's jobs
with every place each is listed (catalog), each profile's list of companies (lists), every open job
read from a company's own Greenhouse, Lever or Ashby board (feeds), and finding companies for a
profile from the catalog with no model call (catalog_discovery) or, failing that, from the web
(discovery). Model calls go to the tailor service; nothing here calls Anthropic.

Worker: python -m app.companies.catalog_worker, run by catalog-worker; it holds no API key and
makes no model calls. Spec: docs/scout_spec.md (docs/companies_engine_spec.md is history)."""
