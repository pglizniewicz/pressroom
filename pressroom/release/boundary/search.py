"""Search the corpus from a terminal.

Owns argument parsing and printing, and no SQL: the query lives in
control/query.py next to the fts5 declaration whose column order it depends on.

Read-only: goes through connect_ro(), so it creates nothing and cannot touch a
row or the index.

Usage:
  pressroom-search "Ryzen 5000 launch"
  pressroom-search "PCIe 4.0" --source amd
  pressroom-search "desktop processor 2020" --source intel,amd --limit 10
  pressroom-search "Z97 H97" --full
"""

import argparse
import contextlib
import sqlite3

from pressroom.database.control.connection import DB_PATH, connect_ro
from pressroom.release.control import query


def search(
    q: str, sources: list | None = None, limit: int = 8, full: bool = False
) -> None:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run any scraper first.")
        return

    with contextlib.closing(connect_ro()) as conn:
        try:
            rows = query.cli_search(conn, q, sources, limit)
        except sqlite3.OperationalError as e:
            print(f"Query error: {e}")
            if "no such table" in str(e):
                print(
                    "Tip: the database has no search index yet - run any scraper once to build it"
                )
            else:
                print("Tip: use quoted phrases, AND/OR/NOT, or prefix* for wildcards")
            return

        if not rows:
            print(f"No results for: {q}")
            return

        source_label = f"  source={','.join(sources)}" if sources else ""
        print(f"Results for: {q!r}{source_label}  ({len(rows)} shown)\n")
        for src, date, title, url, body, excerpt in rows:
            print(f"[{src}][{date}] {title}")
            print(f"  {url}")
            print(f"  …{excerpt}…")
            print()

            if full and body:
                print(body[:1000])
                print("---")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip().split("\n\n", 1)[0]
    )
    parser.add_argument(
        "query", help='Search query (FTS5 syntax: AND/OR/NOT, "phrases", prefix*)'
    )
    parser.add_argument(
        "--source", help="Filter by source(s), comma-separated: intel,amd"
    )
    parser.add_argument("--limit", type=int, default=8, help="Max results (default: 8)")
    parser.add_argument(
        "--full", action="store_true", help="Print full body of each result"
    )
    args = parser.parse_args()

    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    search(args.query, sources, args.limit, args.full)
