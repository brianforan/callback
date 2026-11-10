"""Rejection research: a report per rejected application (analysis, analysis_worker), shared
company research snapshots (snapshots, snapshot_worker), how a company hires over time worked out
from the catalog (history), the company around a rejection (context), and Insights -> Companies
with the company exports (insights). No model calls here: the one call for each snapshot or report
happens in the tailor service. Snapshots hold company facts, never anything about an application.

Workers: snapshot_worker and analysis_worker run inside the tailor worker's loop
(python -m app.tailoring.worker, run by worker). Spec: docs/rejection_research_spec.md."""
