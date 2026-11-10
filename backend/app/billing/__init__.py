"""What Callback's model use costs: measured by Callback and billed by Anthropic, for the owner
(spend), reading Anthropic's usage and cost reports (worker), and pricing past Scout runs
again after a fix to how tailor prices model use (reprice).

No model calls. Only the billing worker holds the Admin API key, and it reaches the internet only
to call api.anthropic.com. Worker: python -m app.billing.worker, run by billing.
"""
