"""Keeping releases_fts in agreement with `releases`.

**'integrity-check' passing is not evidence of a healthy index.** It only
checks that the index is internally consistent, not that it agrees with the
content table, so a stale index reports clean. Verify with orphan/missing counts
and token probes instead.
"""

from pressroom.release.entity import schema


def rebuild_fts(conn) -> None:
    """Reindex releases_fts from scratch. Needed after any bulk change to
    `releases` made outside the triggers (and by the SCHEMA_VERSION 1
    migration, which repairs indexes built when only releases_ai existed)."""
    conn.execute("INSERT INTO releases_fts(releases_fts) VALUES('rebuild')")
    conn.commit()


def sync_fts_triggers(conn) -> None:
    """Repair the index if needed, then install the sync triggers.

    Order matters and is why these two steps live in one function: creating
    releases_au before the rebuild would let the next UPDATE actively corrupt
    the index, because its 'delete' subtracts postings for old.body's tokens
    and on a stale row those are not the tokens actually indexed. FTS5 does
    not verify them.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] < schema.SCHEMA_VERSION:
        print("Rebuilding releases_fts (one-off search-index repair)...", flush=True)
        conn.execute("INSERT INTO releases_fts(releases_fts) VALUES('rebuild')")
        conn.execute(f"PRAGMA user_version = {schema.SCHEMA_VERSION}")
        conn.commit()

    conn.executescript(schema.TRIGGERS_SQL)
    conn.commit()
