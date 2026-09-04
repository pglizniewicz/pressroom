"""Find the DOM container each Wayback source's article lives in.

A parser needs a selector, and picking one from a single capture is how you get
a parser that works on the page you looked at and silently stores navigation
for the rest. This is the pass that redesigned the six text-surgery parsers,
and it stays because the next selector wants the same evidence.

So: walk every capture of a source that page_cache already holds, and for each
one find the smallest element whose text covers that row's stored body. Report
the distribution. A selector worth encoding is one that wins on most captures;
if no shape dominates, say so and leave that parser alone - flat text beats a
sidebar stored as a press release.

Read-only. Prints, writes nothing. The measure is `control/containers.py`'s;
this is the walk over the cache and the report.

Usage:
  pressroom-calibrate-containers                    # every source with a cache
  pressroom-calibrate-containers --source terratec_pressde
  pressroom-calibrate-containers --source terratec --show 2
"""

import argparse
import collections

from pressroom.database.control import connection
from pressroom.text.control import containers

# The row's own capture, keyed the way archive.fetch_snapshot stores it.
_ROWS_SQL = """
    SELECT r.source, r.url, r.detail_id, r.body, w.content
      FROM releases r
      JOIN page_cache w
        ON w.url = 'https://web.archive.org/web/' || r.detail_id || 'id_/' || r.url
     WHERE r.body_html IS NULL
       AND length(COALESCE(r.body, '')) > 400
       {where}
     ORDER BY r.source, r.id
"""


def calibrate(source_filter=None, show=0) -> None:
    conn = connection.connect_ro()
    where = "AND r.source = ?" if source_filter else ""
    rows = conn.execute(
        _ROWS_SQL.format(where=where), (source_filter,) if source_filter else ()
    ).fetchall()

    by_source = collections.defaultdict(list)
    for source, url, ts, body, content in rows:
        by_source[source].append((url, ts, body, content))

    if not by_source:
        print(
            "no cached captures for these rows yet - run "
            "`pressroom-<source> --seed-cache` first"
        )
        return

    for source, items in sorted(by_source.items()):
        shapes = collections.Counter()
        paths = collections.Counter()
        ratios = []
        misses = 0
        samples = []
        for url, ts, body, content in items:
            tag, n = containers.locate(content, body)
            if tag is None:
                misses += 1
                continue
            shapes[containers.describe(tag)] += 1
            paths[containers.ancestry(tag)] += 1
            ratios.append(n / max(len(containers.norm(body)), 1))
            if len(samples) < show:
                samples.append(
                    (ts, containers.describe(tag), n, len(containers.norm(body)))
                )

        found = len(ratios)
        print(
            f"\n=== {source}  ({len(items)} captures, {found} located, {misses} nie znaleziono)"
        )
        if not found:
            continue
        ratios.sort()
        med = ratios[len(ratios) // 2]
        top, top_n = shapes.most_common(1)[0]
        print(f"    dominanta: {top}   {top_n}/{found} = {top_n / found:.0%}")
        print(
            f"    pokrycie body: mediana {med:.2f}  (min {ratios[0]:.2f} max {ratios[-1]:.2f})"
        )
        for shape, n in shapes.most_common(4)[1:]:
            print(f"      inne: {shape:44} {n}")
        path, pn = paths.most_common(1)[0]
        print(f"    ścieżka: {path}   {pn}/{found}")
        for ts, shape, n, blen in samples:
            print(f"      próbka ts={ts} {shape} tekst={n} body={blen}")

    conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n", 1)[0])
    p.add_argument("--source", help="limit to one source tag")
    p.add_argument("--show", type=int, default=0, help="print N per-capture samples")
    args = p.parse_args()
    calibrate(args.source, args.show)
