"""Keeping releases_fts in agreement with `releases`.

**'integrity-check' passing is not evidence of a healthy index.** It only
checks that the index is internally consistent, not that it agrees with the
content table, so a stale index reports clean. Verify with orphan/missing counts
and token probes instead.
"""

from pressroom.release.entity import schema


def rebuild_fts(conn) -> None:
    """Reindex releases_fts from scratch.

    The recovery for a bulk change to `releases` made outside the triggers - an
    UPDATE over many rows, or an out-of-band DELETE from a GUI DB tool. Run it
    immediately, before anything updates a row: releases_au's 'delete'
    subtracts postings for old.body's tokens, and on a stale row those are not
    the tokens actually indexed, so the next UPDATE corrupts the index rather
    than merely leaving it wrong. FTS5 verifies none of this.

    No caller here on purpose - it is typed by hand after a bulk change, and
    the sqlite3 CLI is not installed on this machine. The statement lives in one
    place all the same.
    """
    conn.execute("INSERT INTO releases_fts(releases_fts) VALUES('rebuild')")
    conn.commit()


def install_fts_triggers(conn) -> None:
    """Create the three triggers that keep releases_fts in sync, if absent.

    Called last by database/control/creation.py, after every table exists: all
    three reference `releases`.
    """
    conn.executescript(schema.TRIGGERS_SQL)
