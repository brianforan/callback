"""Job listings: connectors that turn each public job board's JSON into plain listings (boards),
searching every board at once with shared cached results so no board is polled too often
(search), the search syntax (query), and where results are kept between searches and restarts
(cache, Postgres today).

No model calls.
"""
