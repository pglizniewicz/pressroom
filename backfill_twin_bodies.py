#!/usr/bin/env python3
"""Backfill teaser-grade rows from a twin row of the same source - no network.

The M-Audio CMS published the same release under two URL schemes on the same
domain, and both got crawled, so the corpus holds pairs like:

  #6627 len= 142 detail=teaser          .../news/en_us-596.html
  #7055 len=1417 detail=20051212114750  .../index.php?do=media.new&ID=596

Same source, same title, same date - one a listing blurb, the other the full
article. 176 such groups exist, 121 of them in maudio_com_media_news. Wherever
the twin already holds the real text, the teaser can be filled in from the
database itself: no archive.org request, no waiting for a capture that is
already cached in a neighbouring row.

Deliberately non-destructive. Nothing is deleted and nothing is merged:
`releases.url` stays the dedup key, and both URLs genuinely existed, so both
rows stay. Only the short row's `body` is replaced, and its `detail_id` becomes
the twin's capture timestamp - which is where that text actually came from, and
what makes the row's provenance honest afterwards.

Pairing is within one source only. Across sources the same release lives on
several mirrors under unrelated URL schemes by design, and their bodies are
genuinely different documents - merging those would invent a fact.

Usage:
  python backfill_twin_bodies.py --dry-run
  python backfill_twin_bodies.py --dry-run --source maudio_com_media_news
  python backfill_twin_bodies.py
"""

import argparse
import collections

import db

# A row counts as needing help below this; the repo's audit view uses the same
# 300 characters to call a body teaser-grade.
SHORT = 300

# The twin has to be substantially better, not merely different: a 142-character
# blurb replaced by a 200-character blurb is churn, not recovery.
MIN_GAIN = 3

_ROWS_SQL = """
    SELECT id, source, url, detail_id, title, date, length(COALESCE(body, ''))
      FROM releases
     {where}
     ORDER BY source, id
"""


def _key(source, title, date):
    """Same release, same source. Title whitespace differs between the two URL
    schemes (one comes from a listing cell, the other from an article heading),
    so it is collapsed and casefolded; the date must match exactly."""
    return source, " ".join((title or "").split()).casefold(), date


def find_pairs(conn, source=None):
    """(short row, twin row) pairs worth applying, plus the ones rejected."""
    where = "WHERE source = ?" if source else ""
    rows = conn.execute(_ROWS_SQL.format(where=where),
                        (source,) if source else ()).fetchall()

    groups = collections.defaultdict(list)
    undated = 0
    for rid, src, url, detail_id, title, date, length in rows:
        if not title:
            continue
        if not date:
            # Without a date, "same title" is not enough to call two rows the
            # same release - the CMS reused headlines across years.
            undated += 1
            continue
        groups[_key(src, title, date)].append(
            {"id": rid, "source": src, "url": url, "detail_id": detail_id,
             "title": title, "date": date, "len": length})

    pairs, rejected = [], []
    for members in groups.values():
        if len(members) < 2:
            continue
        best = max(members, key=lambda m: m["len"])
        for row in members:
            if row is best or row["len"] >= SHORT:
                continue
            if best["len"] < SHORT or best["len"] < row["len"] * MIN_GAIN:
                rejected.append((row, best))
                continue
            pairs.append((row, best))
    return pairs, rejected, undated


def backfill(dry_run: bool, source: str = None) -> None:
    conn = db.connect()
    pairs, rejected, undated = find_pairs(conn, source)

    per_source = collections.Counter()
    for short, best in pairs:
        per_source[short["source"]] += 1
        print(f"  #{short['id']} {short['source']} {short['len']:5} -> "
              f"{best['len']:5} znaków (od #{best['id']}, detail={best['detail_id']})")
        print(f"      {short['title'][:88]}")

    print(f"\ndo uzupełnienia: {len(pairs)} wierszy")
    for src, n in per_source.most_common():
        print(f"  {src:24} {n}")
    print(f"odrzucone (bliźniak nie jest istotnie lepszy): {len(rejected)}")
    print(f"pominięte bez daty (tytuł sam nie wystarcza): {undated}")

    if dry_run:
        print("\n[dry-run] nic nie zapisano")
        conn.close()
        return

    written = 0
    for short, best in pairs:
        body = conn.execute("SELECT body FROM releases WHERE id = ?", (best["id"],)).fetchone()[0]
        # detail_id follows the body: the text came from the twin's capture, so
        # that timestamp is what this row's provenance now is. upgrade_release
        # goes through releases_au, so the FTS index follows the change.
        detail = best["detail_id"] if str(best["detail_id"] or "").isdigit() else None
        if db.upgrade_release(conn, short["url"], body=body, detail_id=detail):
            written += 1
    print(f"\nuzupełniono {written} wierszy z bliźniaków")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--source", help="Limit to one source tag")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run, source=args.source)
