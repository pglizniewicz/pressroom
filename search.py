#!/usr/bin/env python3
"""Search unified press release database.

Usage:
  python search.py "Ryzen 5000 launch"
  python search.py "PCIe 4.0" --source amd
  python search.py "desktop processor 2020" --source intel,amd --limit 10
  python search.py "Z97 H97" --full
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "pressroom.db"


def search(query: str, sources: list = None, limit: int = 8, full: bool = False) -> None:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run scrape_intel.py or scrape_amd.py first.")
        return

    conn = sqlite3.connect(DB_PATH)

    if sources:
        placeholders = ",".join("?" * len(sources))
        sql = f"""
            SELECT r.source, r.date, r.title, r.url,
                   snippet(releases_fts, 1, '>>>', '<<<', '…', 24) AS excerpt
            FROM releases_fts
            JOIN releases r ON releases_fts.rowid = r.id
            WHERE releases_fts MATCH ?
              AND r.source IN ({placeholders})
            ORDER BY bm25(releases_fts)
            LIMIT ?
        """
        params = [query] + sources + [limit]
    else:
        sql = """
            SELECT r.source, r.date, r.title, r.url,
                   snippet(releases_fts, 1, '>>>', '<<<', '…', 24) AS excerpt
            FROM releases_fts
            JOIN releases r ON releases_fts.rowid = r.id
            WHERE releases_fts MATCH ?
            ORDER BY bm25(releases_fts)
            LIMIT ?
        """
        params = [query, limit]

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Query error: {e}")
        print("Tip: use quoted phrases, AND/OR/NOT, or prefix* for wildcards")
        conn.close()
        return

    if not rows:
        print(f"No results for: {query}")
        conn.close()
        return

    source_label = f"  source={','.join(sources)}" if sources else ""
    print(f"Results for: {query!r}{source_label}  ({len(rows)} shown)\n")
    for src, date, title, url, excerpt in rows:
        print(f"[{src}][{date}] {title}")
        print(f"  {url}")
        print(f"  …{excerpt}…")
        print()

        if full:
            body = conn.execute("SELECT body FROM releases WHERE url = ?", (url,)).fetchone()
            if body:
                print(body[0][:1000])
                print("---")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Search query (FTS5 syntax: AND/OR/NOT, \"phrases\", prefix*)")
    parser.add_argument("--source", help="Filter by source(s), comma-separated: intel,amd")
    parser.add_argument("--limit", type=int, default=8, help="Max results (default: 8)")
    parser.add_argument("--full", action="store_true", help="Print full body of each result")
    args = parser.parse_args()

    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    search(args.query, sources, args.limit, args.full)


if __name__ == "__main__":
    main()
