#!/usr/bin/env python3
"""Progress markers and end-of-run summaries, shared by every scraper.

Each scraper used to keep its own ints, print its own marker chars, and build
its own summary f-string. The counters were the same three-to-five outcomes
everywhere, but the spelling drifted: "never archived successfully" vs
"confirmed never archived" vs "unrecoverable", 'x' for dead in one file and
'd' in the rest, 't' for a title-only stub in one file and 's' in another.

So the outcome vocabulary is fixed here instead. Every scraper reports into
the same seven buckets, which also means the marker stream is readable
without knowing which scraper produced it:

    +  added        a new row was stored
    U  upgraded     an existing teaser/stub row gained real content
    t  teaser       stored, but only the short listing teaser was available
    s  stub         stored, but only a title/date - no body at all
    .  skipped      already stored and already as good as it gets
    ?  uncertain    a network error, not a verdict - will retry next run
    d  dead         confirmed: nothing recoverable from the archive

`uncertain` is deliberately its own bucket rather than folded into `skipped`
(as most scrapers used to do): those rows are the ones a rerun will pick up,
which is exactly what someone reading the summary wants to know.
"""

import db

# Not an outcome: wayback.sample_all_captures prints this once per historical
# capture it fetches, which happens before any row is stored. Deliberately not
# '+' so the two phases stay tellable apart in one run's output.
CAPTURE = ","

# outcome -> (marker char, summary label). Order is the summary's order.
_OUTCOMES = {
    "added": ("+", "{n} added"),
    "upgraded": ("U", "{n} upgraded"),
    "teaser": ("t", "{n} teaser-only"),
    "stub": ("s", "{n} title-only"),
    "skipped": (".", "{n} skipped"),
    "uncertain": ("?", "{n} uncertain (retry later)"),
    "dead": ("d", "{n} never archived"),
}


class Stats:
    """Counts outcomes, prints one marker per outcome as it happens, and
    renders the summary line at the end.

    `source` is used to label the summary and to look up the row total; pass
    None for a run that spans several sources and prints its own totals.
    """

    def __init__(self, source: str = None):
        self.source = source
        self.counts = dict.fromkeys(_OUTCOMES, 0)

    def _bump(self, outcome: str) -> None:
        self.counts[outcome] += 1
        print(_OUTCOMES[outcome][0], end="", flush=True)

    def added(self) -> None:
        self._bump("added")

    def upgraded(self) -> None:
        self._bump("upgraded")

    def teaser(self) -> None:
        self._bump("teaser")

    def stub(self) -> None:
        self._bump("stub")

    def skipped(self) -> None:
        self._bump("skipped")

    def uncertain(self) -> None:
        self._bump("uncertain")

    def dead(self) -> None:
        self._bump("dead")

    def summary(self, conn=None) -> None:
        """Print the summary. Leading newline closes off the marker stream.
        Only non-zero outcomes are listed, so a scraper that never produces
        stubs never mentions them. `conn` adds the source's row total."""
        parts = [label.format(n=n) for (_, label), n
                 in ((_OUTCOMES[k], self.counts[k]) for k in _OUTCOMES) if n]
        body = ", ".join(parts) if parts else "nothing to do"

        prefix = f"[{self.source}] " if self.source else ""
        total = ""
        if conn is not None and self.source:
            total = f". Total in DB: {db.source_total(conn, self.source)}"

        print(f"\n{prefix}{body}{total}")
