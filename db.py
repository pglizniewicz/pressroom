#!/usr/bin/env python3
"""Schema, connection and read/write helpers for pressroom.db.

Owns the database concern end to end: both tables (`releases` + its FTS5
index, and `wayback_cache`), the migrations, and the release helpers every
scraper writes through. Deliberately imports stdlib only - no requests, no
bs4 - so search.py (which has no third-party dependencies) can import it too.

Not an ORM: plain sqlite3, plain SQL strings, one function per statement
shape. The point is that each statement exists exactly once.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "pressroom.db"

# Bumped when a migration in init_db needs to run once per database.
# 1 = rebuild releases_fts (see _sync_fts_triggers).
SCHEMA_VERSION = 1

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS releases (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        source    TEXT NOT NULL,
        detail_id TEXT,
        title     TEXT,
        date      TEXT,
        url       TEXT UNIQUE,
        body      TEXT
    );

    -- External-content FTS5 index: stores no text of its own, reads it live
    -- from `releases` at query time, and only learns about changes through
    -- the triggers below. Column ORDER IS LOAD-BEARING - search.py calls
    -- snippet(releases_fts, 1, ...) where 1 is the positional ordinal of
    -- `body`; swapping these would silently start snippeting titles.
    CREATE VIRTUAL TABLE IF NOT EXISTS releases_fts USING fts5(
        title, body,
        content='releases',
        content_rowid='id',
        tokenize='unicode61'
    );

    CREATE TABLE IF NOT EXISTS wayback_cache (
        url                TEXT PRIMARY KEY,
        content            BLOB NOT NULL,
        id_content_type    TEXT,
        fw_guessed_charset TEXT,
        bs4_encoding       TEXT
    );
"""

# All three are required to keep releases_fts in sync. Until SCHEMA_VERSION 1
# only releases_ai existed, so every "UPDATE releases SET body = ..." left the
# recovered text unsearchable, and every out-of-band DELETE (e.g. from a GUI
# DB tool) left an orphaned index entry - which also skews bm25() corpus
# statistics for *every* query, not just the affected rows. FTS5 reports none
# of this: 'integrity-check' passes, because the index is internally
# consistent, it simply doesn't know the content table moved underneath it.
#
# Deletes and updates must use the special 'delete' command with the OLD
# values; a plain "DELETE FROM releases_fts" is not supported on an
# external-content table.
_TRIGGERS_SQL = """
    CREATE TRIGGER IF NOT EXISTS releases_ai
    AFTER INSERT ON releases BEGIN
        INSERT INTO releases_fts(rowid, title, body)
        VALUES (new.id, new.title, new.body);
    END;

    CREATE TRIGGER IF NOT EXISTS releases_au
    AFTER UPDATE ON releases BEGIN
        INSERT INTO releases_fts(releases_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
        INSERT INTO releases_fts(rowid, title, body)
        VALUES (new.id, new.title, new.body);
    END;

    CREATE TRIGGER IF NOT EXISTS releases_ad
    AFTER DELETE ON releases BEGIN
        INSERT INTO releases_fts(releases_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
    END;
"""

_INSERT_SQL = (
    "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) "
    "VALUES (?,?,?,?,?,?)"
)

_UPGRADE_SQL = """
    UPDATE releases
       SET detail_id = COALESCE(?, detail_id),
           title     = COALESCE(?, title),
           date      = COALESCE(?, date),
           body      = COALESCE(?, body)
     WHERE url = ?
"""


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Reindex releases_fts from scratch. Needed after any bulk change to
    `releases` made outside the triggers (and by the SCHEMA_VERSION 1
    migration, which repairs indexes built when only releases_ai existed)."""
    conn.execute("INSERT INTO releases_fts(releases_fts) VALUES('rebuild')")
    conn.commit()


def _sync_fts_triggers(conn: sqlite3.Connection) -> None:
    """Repair the FTS index if needed, then install the sync triggers.

    Order matters and is why these two steps live in one function: creating
    releases_au before the rebuild would let the next UPDATE actively corrupt
    the index, because its 'delete' subtracts postings for old.body's tokens
    and on a stale row those are not the tokens actually indexed. FTS5 does
    not verify them.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        print("Rebuilding releases_fts (one-off search-index repair)...", flush=True)
        conn.execute("INSERT INTO releases_fts(releases_fts) VALUES('rebuild')")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    conn.executescript(_TRIGGERS_SQL)
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if absent, apply migrations, install FTS triggers.
    Idempotent - every scraper calls it once at startup."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()

    # wayback_cache may predate these diagnostic columns (SQLite has no
    # "ADD COLUMN IF NOT EXISTS") - add whichever are missing.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(wayback_cache)").fetchall()}
    for col in ("id_content_type", "fw_guessed_charset", "bs4_encoding"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE wayback_cache ADD COLUMN {col} TEXT")
    conn.commit()

    _sync_fts_triggers(conn)


def connect(db_path=None) -> sqlite3.Connection:
    """Open pressroom.db with the schema ensured."""
    conn = sqlite3.connect(db_path or DB_PATH)
    init_db(conn)
    return conn


def store_release(conn: sqlite3.Connection, source: str, url: str, *,
                  title: str = "", date: str = "", body: str = "",
                  detail_id=None, commit: bool = True) -> bool:
    """INSERT OR IGNORE one release, keyed on `url` (UNIQUE).

    Returns True only if a row was actually inserted; False means the url was
    already present and nothing was written. Callers keeping a "new" counter
    should gate it on this rather than incrementing unconditionally.

    Content fields are keyword-only on purpose: the six positional columns
    were easy to transpose silently, and this way a mistake is a TypeError.
    Pass commit=False when the caller commits once after a loop.
    """
    cur = conn.execute(_INSERT_SQL, (source, detail_id, title, date, url, body))
    if commit:
        conn.commit()
    return cur.rowcount > 0


def upgrade_release(conn: sqlite3.Connection, url: str, *,
                    body=None, title=None, date=None, detail_id=None,
                    commit: bool = True) -> bool:
    """Upgrade an existing row in place - a teaser/stub replaced by recovered
    full text. None means "leave that column alone", so the call site states
    which columns the upgrade is allowed to touch:

        upgrade_release(conn, url, body=text)
            body only, e.g. a PDF's extracted text under the same url.
        upgrade_release(conn, url, detail_id=ts, body=text)
            keeps the title/date harvested from the listing page, which are
            better than anything the detail page carries.
        upgrade_release(conn, url, detail_id=ts, title=t, date=d, body=text)
            detail page is authoritative for all of them.

    Returns True if a row matched `url`.
    """
    cur = conn.execute(_UPGRADE_SQL, (detail_id, title, date, body, url))
    if commit:
        conn.commit()
    return cur.rowcount > 0


def already_stored(conn: sqlite3.Connection, url: str) -> bool:
    """Whether any row exists for `url`. Note this is not the same as
    stored_detail_id(...) is not None - a row whose detail_id is NULL exists
    but yields None there."""
    return conn.execute("SELECT 1 FROM releases WHERE url = ?", (url,)).fetchone() is not None


def stored_detail_id(conn: sqlite3.Connection, url: str):
    """None if no row exists for `url`, else its detail_id - lets a caller
    tell a fully-recovered row apart from a fallback (e.g. detail_id=="teaser"
    or "stub") that's still worth retrying to upgrade on a future run."""
    row = conn.execute("SELECT detail_id FROM releases WHERE url = ?", (url,)).fetchone()
    return row[0] if row else None


def stored_body_length(conn: sqlite3.Connection, url: str):
    """None if no row exists for `url`, else the length of its body.

    The way to tell a teaser-grade row from a fully recovered one when
    stored_detail_id() cannot: the pressdb and media_pr scrapers put the
    *listing* capture's timestamp in detail_id even when the body they stored is
    only that listing's blurb, so a timestamp there says nothing about whether
    the real text was ever fetched. Length does.
    """
    row = conn.execute("SELECT length(COALESCE(body, '')) FROM releases WHERE url = ?",
                       (url,)).fetchone()
    return row[0] if row else None


def source_total(conn: sqlite3.Connection, source: str) -> int:
    """Row count for one source - the figure every scraper's summary prints."""
    return conn.execute("SELECT count(*) FROM releases WHERE source = ?", (source,)).fetchone()[0]


def source_urls(conn: sqlite3.Connection, source: str):
    """Yield every stored url for `source`; callers derive their own dedup key
    (a sid, a filename) from it."""
    for (url,) in conn.execute("SELECT url FROM releases WHERE source = ?", (source,)):
        yield url
