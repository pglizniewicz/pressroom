"""Which capture the archive walk did *not* take, and why.

`archive.fetch_best_matching_snapshot` takes the earliest capture of a url that
parses to something, and the default is meant to hold: a press release is not
edited after publication, so the earliest copy is normally the article itself.
Two probes exist for when it does not hold, and when one of them wins, the row
was recovered from something other than the obvious answer. That is worth a
line at the end of the run.

Its own module rather than a counter on `Stats`, for the same reason
`decoding.REPAIRS` is: the fact is produced deep inside the fetch, and threading
it back out through every scraper would be sixteen chances to forget. Collected
here, drained by `Stats.summary()`, silent when there is nothing to say.

Stdlib only - a progress trailer has no business pulling a library in.
"""

from typing import NamedTuple

# Why the earliest capture was not the answer. Constants rather than free text
# at the append site, so the vocabulary lives in one place - the same rule
# `outcome._OUTCOMES` follows for its markers.
LATER_IS_BETTER = "rok pozniej bogatsza"
NEWEST_IS_BETTER = "ostatnia bogatsza"
PROBE_UNREACHABLE = "probki nie pobrano"

# How many rows the trailer lists before it stops naming them. The count in the
# header is always the whole truth; this only caps the reading.
CAP = 10


class Override(NamedTuple):
    """One decision that went against the earliest capture.

    `url` is the row's own url, which is the key to SELECT on - not the source,
    which `archive.py` does not know. `passed_score` is None when the capture we
    passed over could not be fetched at all, which is a different statement from
    "we looked and it was worse".
    """

    url: str
    taken: str
    taken_score: int
    passed: str
    passed_score: int | None
    why: str


OVERRIDES: list[Override] = []


def note(
    url: str,
    *,
    taken: str,
    taken_score: int,
    passed: str,
    passed_score: int | None,
    why: str,
) -> None:
    """Record one such decision. Called from exactly one place, at the point the
    walk settles on a capture, so a caller cannot forget to."""
    OVERRIDES.append(Override(url, taken, taken_score, passed, passed_score, why))


def _line(o: Override) -> str:
    # "blad" rather than 0 for a probe that could not be fetched: printing a
    # score would say we looked and found nothing, which is a different claim.
    score = o.passed_score
    lost = "blad" if score is None else str(score)
    taken = f"{o.taken} ({o.taken_score})"
    passed = f"{o.passed} ({lost})"
    return f"  {taken:21} zamiast {passed:21}  {o.why:20} {o.url}"


def drain(prefix: str = "") -> list[str]:
    """The trailer's lines, and clear the collector.

    Clearing matters: `catch_up()` builds a fresh `Stats` per strategy
    and calls `summary()` several times per source, so a collector that is not
    drained reprints the first strategy's rows under the third. That is the bug
    `decoding.REPAIRS.clear()` already exists to avoid.
    """
    if not OVERRIDES:
        return []
    lines = [f"{prefix}inna kopia niz domyslna: {len(OVERRIDES)}"]
    lines.extend(_line(o) for o in OVERRIDES[:CAP])
    if len(OVERRIDES) > CAP:
        lines.append(f"  ... i {len(OVERRIDES) - CAP} wiecej")
    OVERRIDES.clear()
    return lines
