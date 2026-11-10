"""Job postings: the pure rule-based parser that turns a posting's text into normalized fields
(parser), creating and updating postings while keeping the parser away from fields the user
corrected (store), and the text of postings saved with a link and no text (text).

No model calls. Posting text is read by the tailor worker through the tailor service's fetcher
(docs/rejection_research_spec.md, chunk 1).
"""
