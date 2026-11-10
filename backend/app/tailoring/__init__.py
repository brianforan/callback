"""Tailoring, API side: what's sent to the tailor service and the plain-rule checks on what comes
back (rules), and the worker that runs each tailoring job (worker). The model in the tailor service
only proposes a rewrite; these rules decide whether it's kept, and nothing is ever submitted.

Worker: python -m app.tailoring.worker, run by worker. With no tailoring waiting, the same loop
runs queued company research, rejection analyses and posting-text reads, then compiles PDFs.
"""
