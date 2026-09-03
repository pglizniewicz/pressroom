#!/usr/bin/env python3
"""Fill a teaser body from its twin row in the same source. No network at all.

The M-Audio CMS published the same release under two url schemes on the same
domain, and both got crawled, so the corpus holds pairs like:

  #6627 len= 142 detail=teaser          .../news/en_us-596.html
  #7055 len=1417 detail=20051212114750  .../index.php?do=media.new&ID=596

Same source, same title, same date - one a listing blurb, the other the full
article. Wherever the twin already holds the real text, the teaser can be filled
from the database itself, with no request for a capture a neighbouring row has.

Deliberately non-destructive. Nothing is deleted and nothing is merged:
`releases.url` stays the dedup key and both urls genuinely existed, so both rows
stay. Only the short row's `body` is replaced, and its `detail_id` becomes the
twin's capture timestamp - which is where that text actually came from.

Pairing is within one source only. Across sources the same release lives on
several mirrors under unrelated url schemes by design, and their bodies are
genuinely different documents, so merging those would invent a fact.

`fill(conn, source)` is the last step of phase 2 for the scrapers whose CMS did
this. It is here rather than in catch_up.py because the text comes from another
row rather than from a capture: different source of truth, different gate, and
the one place with a reason to call origin.clear().
"""

import collections

from pressroom.fetcher.control import address
from pressroom.provenance.entity import origin
from pressroom.release.control import storage

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
    rows = conn.execute(
        _ROWS_SQL.format(where=where), (source,) if source else ()
    ).fetchall()

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
            {
                "id": rid,
                "source": src,
                "url": url,
                "detail_id": detail_id,
                "title": title,
                "date": date,
                "len": length,
            }
        )

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


def fill(conn, source: str | None = None, *, dry_run: bool = False) -> int:
    """Fill every teaser in `source` that has a better twin. Returns rows written.

    Called at the end of a scraper's run, so a fresh crawl that lands one url
    scheme before the other needs no follow-up pass. Idempotent: once the short
    row holds the twin's text there is no gap left to find, and a rerun reports
    nothing to do.
    """
    pairs, rejected, undated = find_pairs(conn, source)

    per_source = collections.Counter()
    if pairs:
        print(f"[{source or 'twins'}] bliźniaki do uzupełnienia:", flush=True)
    for short, best in pairs:
        per_source[short["source"]] += 1
        print(
            f"  #{short['id']} {short['source']} {short['len']:5} -> "
            f"{best['len']:5} znaków (od #{best['id']}, detail={best['detail_id']})"
        )
        print(f"      {short['title'][:88]}")

    if not pairs and not rejected:
        return 0
    print(f"\ndo uzupełnienia: {len(pairs)} wierszy")
    for src, n in per_source.most_common():
        print(f"  {src:24} {n}")
    print(f"odrzucone (bliźniak nie jest istotnie lepszy): {len(rejected)}")
    print(f"pominięte bez daty (tytuł sam nie wystarcza): {undated}")

    if dry_run:
        print("\n[dry-run] nic nie zapisano")
        return 0

    written = 0
    for short, best in pairs:
        body = conn.execute(
            "SELECT body FROM releases WHERE id = ?", (best["id"],)
        ).fetchone()[0]
        # detail_id and grade follow the body: the text came from the twin's
        # capture, and the row no longer holds a teaser.
        detail = best["detail_id"] if address.is_timestamp(best["detail_id"]) else None
        # One transaction for the pair, which is why both take commit=False. The
        # recorded origin stops being true the instant the body changes, so
        # committing the upgrade first would leave a window in which anything
        # raising leaves exactly the false statement the clear exists to prevent.
        with conn:
            if storage.upgrade_release(
                conn,
                short["url"],
                body=body,
                detail_id=detail,
                grade="full",
                commit=False,
            ):
                written += 1
                # This text came out of another row, so whatever address was
                # recorded has stopped describing the body.
                origin.clear(conn, short["url"], commit=False)
    if written:
        print(f"\nuzupełniono {written} wierszy z bliźniaków")
    return written
