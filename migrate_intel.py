#!/usr/bin/env python3
"""One-time migration: copy intel-pressroom/pressroom.db → unified pressroom.db.

Run after scrape_intel.py has been used at least once (so the schema exists),
or standalone (it calls init_db itself).

Safe to run multiple times — uses INSERT OR IGNORE.
"""

import sqlite3
from pathlib import Path

from common import init_db

SRC = Path(__file__).parent.parent / "intel-pressroom" / "pressroom.db"
DST = Path(__file__).parent / "pressroom.db"


def main() -> None:
    if not SRC.exists():
        print(f"Source DB not found: {SRC}")
        return

    src = sqlite3.connect(SRC)
    dst = sqlite3.connect(DST)
    init_db(dst)

    rows = src.execute("SELECT detail_id, title, date, url, body FROM releases").fetchall()
    print(f"Migrating {len(rows)} Intel releases…")

    inserted = 0
    for detail_id, title, date, url, body in rows:
        cur = dst.execute(
            "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
            ("intel", detail_id, title, date, url, body),
        )
        inserted += cur.rowcount

    dst.commit()
    src.close()
    dst.close()
    print(f"Done. Inserted {inserted} new rows (skipped {len(rows) - inserted} duplicates).")


if __name__ == "__main__":
    main()
