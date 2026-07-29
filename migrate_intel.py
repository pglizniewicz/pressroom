#!/usr/bin/env python3
"""One-time migration: copy intel-pressroom/pressroom.db → unified pressroom.db.

Run after scrape_intel.py has been used at least once (so the schema exists),
or standalone (it calls init_db itself).

Safe to run multiple times — uses INSERT OR IGNORE.
"""

import contextlib
import sqlite3
from pathlib import Path

import db

SRC = Path(__file__).parent.parent / "intel-pressroom" / "pressroom.db"


def main() -> None:
    if not SRC.exists():
        print(f"Source DB not found: {SRC}")
        return

    with contextlib.closing(sqlite3.connect(SRC)) as src, contextlib.closing(db.connect()) as dst:
        rows = src.execute("SELECT detail_id, title, date, url, body FROM releases").fetchall()
        print(f"Migrating {len(rows)} Intel releases…")

        inserted = 0
        for detail_id, title, date, url, body in rows:
            if db.store_release(dst, "intel", url, title=title, date=date, body=body,
                                detail_id=detail_id, commit=False):
                inserted += 1

        dst.commit()

    print(f"Done. Inserted {inserted} new rows (skipped {len(rows) - inserted} duplicates).")


if __name__ == "__main__":
    main()
