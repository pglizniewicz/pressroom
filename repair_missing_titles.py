#!/usr/bin/env python3
"""Fill in the titles that were never extracted, from page_cache. No network.

54 rows across five TerraTec sources carried an empty `title`. All of them had
their capture in page_cache already, so this costs no requests - it is the same
"a parser fix costs no refetch" the rest of the re-extraction passes rely on.

Why they were empty, which is four separate causes and not one:

  - terratec_early (21, i.e. every row it has) never extracted a title at all.
    Those two pages have no headline markup to key on; scrape_terratec_early
    .headline() reads it off the dateline instead.
  - terratec / terratec_de (8) hit one of the three headline shapes the
    bold-tag rule does not see - see scrape_terratec.find_headline.
  - terratec_pressen (5) parse correctly today and were simply stored before
    TITLE_TAG_MONTH_RE existed. Re-running the parser is the whole fix.
  - terratec_pressde (3) use two templates the <title> regexes miss: the
    printer-friendly print.php view, and one article whose <title> carries no
    date prefix.

What it will not do:

  - **It only ever writes a title, and only over an empty one.** Not the date,
    not the body, not detail_id. Several of these captures now parse to a
    *better* body than the row holds (3864 goes from 0 to 4547 characters), and
    two of them to a different date - but a body rewrite has to go through
    backfill_body_html.py, which owns safe_to_write. Widening this script to
    "everything the parser now returns" would be a bulk body update with no
    gate on it.
  - It does not invent a title. A capture that carries no headline leaves the
    row as it is and is reported `dead`: 15 of the 54 are placeholder pages -
    ten PHP-Nuke skeletons rendered with no article in them, five 290-byte
    terratec.de stubs - and one French release that opens straight into prose
    with no headline of any kind.

Usage:
  python repair_missing_titles.py --dry-run    # report, write nothing
  python repair_missing_titles.py
  python repair_missing_titles.py --source terratec_early
"""

import argparse

import db
from progress import Stats

import backfill_body_html as B


def missing(conn, sources=None):
    sql = "SELECT id, source, detail_id, url FROM releases WHERE COALESCE(title, '') = ''"
    params = []
    if sources:
        sql += f" AND source IN ({','.join('?' * len(sources))})"
        params = list(sources)
    return conn.execute(sql + " ORDER BY source, id", params).fetchall()


def from_capture(conn, source: str, detail_id: str, url: str) -> str:
    """The title this row's own cached capture yields, or "" if there is no
    capture, no parser for the source, or no headline on the page."""
    parser = B.CACHED_PARSERS.get(source)
    if parser is None:
        return ""
    key = B.capture_url(detail_id, url)
    if not key:
        return ""
    row = conn.execute("SELECT content FROM page_cache WHERE url = ?", (key,)).fetchone()
    # looks_like_html before parsing: a .pdf handed to BeautifulSoup comes back
    # as a document whose text is the decoded PDF stream, with no exception to
    # catch - that is what put "%PDF-1.3 %âãÏÓ" into 23 bodies once.
    if row is None or not B.looks_like_html(row[0]):
        return ""
    return (parser(row[0]).get("title") or "").strip()


def run(sources=None, dry_run: bool = False) -> None:
    conn = db.connect()
    rows = missing(conn, sources)
    print(f"[titles] {len(rows)} rows with no title"
          f"{' (dry run)' if dry_run else ''}", flush=True)

    # The listing-derived sources have no capture per row - the release only
    # ever existed inside one listing page - so their titles come out of that
    # page in one pass, keyed by the URL the scraper minted.
    listings = {}
    for source in {source for _, source, _, _ in rows} & set(B.LISTING_SOURCES):
        listings[source] = B.LISTING_SOURCES[source](conn)

    stats = Stats(total=len(rows))
    unresolved = []

    for rid, source, detail_id, url in rows:
        if source in listings:
            entry = listings[source].get(url) or {}
            title = (entry.get("title") or "").strip()
        else:
            title = from_capture(conn, source, detail_id, url)

        if not title:
            unresolved.append((rid, source, url))
            stats.dead()
            continue
        if dry_run:
            print(f"\n  {rid} {source}: {title!r}", flush=True)
            stats.upgraded()
            continue
        # title only - see the module docstring on why the body is left alone
        # even where the same parse would now produce a better one.
        if db.upgrade_release(conn, url, title=title):
            stats.upgraded()
        else:
            stats.skipped()

    stats.summary(conn)

    if unresolved:
        print(f"\n{len(unresolved)} rows have no headline in their capture "
              f"and keep an empty title:")
        for rid, source, url in unresolved:
            print(f"  {rid:5d} {source:18s} {url}")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill empty release titles from page_cache. No network.")
    parser.add_argument("--source", action="append", dest="sources",
                        help="limit to this source (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written, write nothing")
    args = parser.parse_args()
    run(sources=args.sources, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
