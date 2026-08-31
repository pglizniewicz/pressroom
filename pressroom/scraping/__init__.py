"""The structure a source's run has, independent of which source it is.

A scraper owns its whole job - discovery, and the catch-up over everything an
earlier run could not get - and a rerun is expected to pick up exactly what the
last one missed. There is no backfill, repair or migrate family: a pass that
has to be re-run after a crawl belongs in the write path or in the scraper, and
a fix that is genuinely finished gets deleted.

The catch-up strategies take the parser, the collector and the fetcher from
their caller rather than looking a source up in a registry, so nothing here
imports a source and every source can import this.

Free work first, network last. A network error is never a verdict: a failed
fetch writes nothing and reports uncertain, so a rerun retries exactly that
row; only a confirmed absence is recorded as dead.
"""
