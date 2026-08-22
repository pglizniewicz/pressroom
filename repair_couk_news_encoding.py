#!/usr/bin/env python3
"""One-off repair: re-decode midiman_couk_news rows that were stored as mojibake.

Before encoding.py existed, scrape_midiman_news.py handed raw bytes to
BeautifulSoup, and midiman.co.uk's listing pages are utf-8 with three stray
cp1252 bytes in a footer sentence - enough to make chardet decode the whole
file as windows-1250/1258, so 14 stored titles and 11 teaser bodies came out
as e.g. "Academyâ€™s" instead of "Academy's".

Runs entirely from page_cache - the original bytes were never lost, so this
needs no network and cannot be affected by archive.org being down. Rows are
matched by url (ASCII, so unaffected by the bug); only values that actually
differ are written.

A teaser row's body is listing-derived and gets repaired; a fully recovered
row's body came from its own detail page, so it is NOT touched here - that
would overwrite an article with a one-line blurb. Detail-page bodies are
re-decoded simply by rerunning scrape_midiman_news.py, which now fetches
through encoding.decode_html.

Usage:
  python repair_couk_news_encoding.py --dry-run   # report what would change
  python repair_couk_news_encoding.py
"""

import argparse

import db
from scrape_midiman_news import DOMAINS, ID_HREF_RE, detail_url, extract_entries, rank

# This repair is specific to the co.uk listing captures, so it names that one
# source rather than following the scraper across all four domains it now covers.
SOURCE = "midiman_couk_news"
BASE = DOMAINS[SOURCE]

# The cached listing captures this rebuilds from. Both the bare listing and its
# &show=all variant, matching LISTING_URLS in the scraper.
CACHED_LISTINGS_SQL = """
    SELECT url, content FROM page_cache
     WHERE url LIKE '%midiman.co.uk%do=media.news%'
"""


def correct_entries(conn) -> dict:
    """url -> {title, date, teaser}, re-parsed from the cached listing bytes."""
    by_url = {}
    pages = conn.execute(CACHED_LISTINGS_SQL).fetchall()
    print(f"[{SOURCE}] re-parsing {len(pages)} cached listing captures")

    for snap_url, content in pages:
        # "…/web/<timestamp>id_/<original>" - extract_entries needs the original
        # for urljoin, or every href would resolve against web.archive.org.
        original = snap_url.split("id_/", 1)[1]
        for e in extract_entries(content, original):
            m = ID_HREF_RE.search(e["href"])
            url = detail_url(BASE, m.group(1)) if m else e["href"]
            cur = by_url.get(url)
            if cur is None or rank(e) > rank(cur):
                by_url[url] = e
    return by_url


def repair(dry_run: bool = False) -> None:
    conn = db.connect()
    by_url = correct_entries(conn)

    stored = conn.execute(
        "SELECT url, detail_id, title, body FROM releases WHERE source = ?", (SOURCE,)
    ).fetchall()
    print(f"[{SOURCE}] {len(stored)} stored rows, {len(by_url)} listing entries re-parsed\n")

    titles = bodies = unmatched = 0
    for url, detail_id, title, body in stored:
        good = by_url.get(url)
        if good is None:
            unmatched += 1
            continue

        new_title = good["title"] if good["title"] != title else None
        # Only teaser rows carry a listing-derived body.
        new_body = (good["teaser"]
                    if detail_id == "teaser" and good["teaser"] != body else None)
        if new_title is None and new_body is None:
            continue

        if new_title is not None:
            titles += 1
            print(f"  title: {title!r}\n      -> {new_title!r}")
        if new_body is not None:
            bodies += 1
            print(f"  body:  {body[:60]!r}\n      -> {new_body[:60]!r}")

        if not dry_run:
            db.upgrade_release(conn, url, title=new_title, body=new_body)

    verb = "would repair" if dry_run else "repaired"
    print(f"\n[{SOURCE}] {verb} {titles} titles, {bodies} teaser bodies"
          f" ({unmatched} stored rows had no listing entry - prefix-crawl finds, expected)")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()
    repair(dry_run=args.dry_run)
