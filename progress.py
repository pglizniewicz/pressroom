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

import time

import db

# Not an outcome: wayback.sample_all_captures prints this once per historical
# capture it fetches, which happens before any row is stored. Deliberately not
# '+' so the two phases stay tellable apart in one run's output.
CAPTURE = ","

# How often the marker stream is interrupted by a timestamped progress line.
# These runs last hours - a wall of undated marker characters gives no way to
# tell "alive but rate-limited" from "hung", and no way to judge the rate or
# what is left. 15 minutes is often enough to answer that, rare enough not to
# bury the markers.
HEARTBEAT_SECONDS = 900

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

    `total` is the number of items this run will attempt, if the caller knows
    it up front. It only enriches the heartbeat with a percentage and an ETA;
    everything works without it.
    """

    def __init__(self, source: str = None, total: int = None):
        self.source = source
        self.total = total
        self.counts = dict.fromkeys(_OUTCOMES, 0)
        self.started = time.monotonic()
        self._last_beat = self.started

    def _bump(self, outcome: str) -> None:
        self.counts[outcome] += 1
        print(_OUTCOMES[outcome][0], end="", flush=True)
        self._maybe_heartbeat()

    def done(self) -> int:
        return sum(self.counts.values())

    def _maybe_heartbeat(self) -> None:
        """Interrupt the marker stream with a dated progress line every
        HEARTBEAT_SECONDS.

        Driven by item completions rather than a timer thread: these loops
        finish an item every couple of seconds even when archive.org is
        throttling hard (its retry backoff caps out around a minute), so a
        clock check here is enough and keeps the module thread-free. The one
        case it cannot cover is a single item wedged for longer than the
        interval - then the next beat is simply late.
        """
        now = time.monotonic()
        if now - self._last_beat < HEARTBEAT_SECONDS:
            return
        self._last_beat = now

        done = self.done()
        elapsed = now - self.started
        rate = done / (elapsed / 60) if elapsed else 0

        progress = f"{done}"
        if self.total:
            progress += f"/{self.total} ({done * 100 // self.total}%)"
        eta = ""
        if self.total and rate:
            left = max(0, self.total - done) / rate
            eta = f", ~{int(left // 60)}h{int(left % 60):02d}m left"

        prefix = f"[{self.source}] " if self.source else ""
        print(f"\n{time.strftime('%H:%M:%S')} {prefix}{progress}"
              f" · {self._counts_text()} · {rate:.1f}/min{eta}", flush=True)

    def _counts_text(self) -> str:
        parts = [label.format(n=self.counts[k]) for k, (_, label) in _OUTCOMES.items()
                 if self.counts[k]]
        return ", ".join(parts) if parts else "nothing yet"

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
        body = self._counts_text() if self.done() else "nothing to do"

        prefix = f"[{self.source}] " if self.source else ""
        total = ""
        if conn is not None and self.source:
            total = f". Total in DB: {db.source_total(conn, self.source)}"

        print(f"\n{prefix}{body}{total}")
        # What the write path had to undo on the way past. Printed here rather
        # than reported by a separate pass, because the alternative was a rule
        # ("re-run the encoding repair after anything that refetches") that a
        # human had to remember, and one forgotten run put the damage back into
        # 34 rows. Silence means there was nothing to fix.
        if db.REPAIRS:
            fixed = ", ".join(f"{n}x {m}" for m, n in db.REPAIRS.most_common())
            print(f"{prefix}naprawione kodowanie: {fixed}")
            db.REPAIRS.clear()
