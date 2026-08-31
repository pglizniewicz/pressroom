"""The local read-only HTTP browser over the corpus.

Routing, query-parameter parsing and JSON only - no SQL of its own, and the
database is opened read-only, so the browser cannot touch a row or the search
index. Stdlib plus vanilla JavaScript: no build step, no framework, no auth,
bound to localhost.

It renders provenance in plain text next to an archive link, because an
unannotated link to another page reads as the article's own capture - which is
the misreading the provenance table was created to fix.
"""
