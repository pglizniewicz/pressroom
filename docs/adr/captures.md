# Captures

Why every fetched byte lives in the database, and how archive.org is asked.

**`page_cache` means a parser fix costs no refetch.** Every fetched page's raw
bytes live in the DB (not on disk — auxiliary data belongs in the database), so
reparsing is free and refetching is a mistake. The table was called
`wayback_cache` when only `archive.fetch_snapshot()` used it; the four live
sources went straight through `session.get`, and when the flat-text extraction
turned out to be wrong they all had to be crawled again from scratch.
`politeness.fetch_cached()` closed that hole. **A new scraper that fetches a page
any other way is a bug.**

**Ask archive.org through `archive._cdx()`** — the public CDX API. Not
`__wb/sparkline` or `__wb/calendarcaptures`: internal endpoints needing a forged
`Referer`, and 3 requests where CDX takes 1.

**Every HTTP attempt against archive.org is logged to `wayback_calls`.** Before
tuning a timeout or sleep constant in `archive.py` from a handful of manual
`curl` calls, query this table — that is the mistake that got `CDX_TIMEOUT` tuned
twice on thin evidence before this existed.

**Re-extracting the whole corpus is free.** Everything fetched is in
`page_cache`, so `pressroom-<source> --offline --force` re-runs `clean()` over
rows that already have `body_html` without a single request.
