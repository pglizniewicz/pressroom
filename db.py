#!/usr/bin/env python3
"""Schema, connection and read/write helpers for pressroom.db.

Owns the database concern end to end: both tables (`releases` + its FTS5
index, and `page_cache`), the migrations, and the release helpers every
scraper writes through. Deliberately imports stdlib only - no requests, no
bs4 - so search.py (which has no third-party dependencies) can import it too.

Not an ORM: plain sqlite3, plain SQL strings, one function per statement
shape. The point is that each statement exists exactly once.
"""

import hashlib
import sqlite3
import time
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
        body      TEXT,
        -- How good this row is: 'full' | 'teaser' | 'stub'. Its own column
        -- since 2026-08-22, because it spent a long time inside `detail_id`,
        -- where a *verdict about the body* sat in a field meant for a
        -- *reference to where the body came from*. Seven places had to
        -- re-derive which of the two a given value was, by counting digits.
        -- 'full' is only ever as good as what the scraper knew: the media_pr
        -- and pressdb sources store a capture timestamp on a row whose body is
        -- just the listing blurb, so a 'full' grade there means "not marked
        -- otherwise", and length(body) remains the honest check.
        grade     TEXT NOT NULL DEFAULT 'full',
        -- The same content as `body`, but as the small HTML subset richtext.py
        -- emits: paragraphs, lists, headings, links, images, tables. NULL means
        -- the row predates that change and still renders as preformatted text.
        -- Deliberately not indexed: FTS reads `body`, which is derived from
        -- this column and therefore can never disagree with it.
        body_html TEXT
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

    CREATE TABLE IF NOT EXISTS page_cache (
        url                TEXT PRIMARY KEY,
        content            BLOB NOT NULL,
        id_content_type    TEXT,
        fw_guessed_charset TEXT,
        bs4_encoding       TEXT,
        -- When these bytes were fetched (time.time()). NULL on the 6345 entries
        -- written before this column existed, which is the honest answer: the
        -- table never recorded it, and the question "when did this file arrive"
        -- had no answer at all - not even "before the wayback_calls log
        -- started", since a cache hit is not logged as an attempt.
        fetched_at         REAL,
        -- sha256 of `content`. Not a storage trick - the blobs stay, and
        -- deduplicating them would be the thing that makes sharding this table
        -- awkward later. It is an *identity* fact: the same attachment was
        -- served from midiman.com, midiman.net and m-audio.com, and until this
        -- column existed the only way to ask "are these the same bytes" was to
        -- match filenames, which quietly paired a row with a different release
        -- that happened to share a file name (#5343).
        content_sha256     TEXT
    );

    -- Which archive.org capture a row's `body` was actually read out of, for
    -- the rows where that is NOT a capture of the row's own url. A scraper
    -- that reads a release out of a *listing* capture stores that listing's
    -- timestamp in releases.detail_id, and the timestamp alone cannot say
    -- which page it belongs to - so serve.py used to build
    -- web/<ts>/<row url>, a capture that never existed (#4414 was the report
    -- that turned this up; CDX has no capture of that article, ever, while
    -- web/20111011173713/.../presse.html holds its full text).
    --
    -- Its own table rather than a column on `releases`: this is provenance,
    -- one row per release that has a capture behind its text, and absence has
    -- to keep meaning "no archive link for this row".
    --
    -- Two columns and nothing else. It briefly carried `matched`, the fraction
    -- of body probes found when an address had to be *inferred* - dropped once
    -- the classes turned out to be derivable from what is already here:
    -- `origin_url` equal to `web/<detail_id>id_/<url>` is an address computed
    -- from the row, a .pdf/.doc url is an attachment located by path, and
    -- anything else was inferred (149 rows, exactly the ones `matched` marked).
    -- What a reader wants from those is not a score but whether the body can be
    -- produced from those bytes, which verify_body_origin.py answers.
    --
    -- `origin_url` is the ONLY address stored, and it is the whole one: the
    -- page_cache key, `…/web/<ts>id_/<page>`. It first shipped alongside a
    -- `page_url` column and that was one column too many - the two agreed in
    -- 158 of 158 rows, since one is a prefix of the other. Of the two,
    -- origin_url is the one worth keeping: the page is a pure string split
    -- out of it (serve.py), while rebuilding it from a page would need
    -- releases.detail_id, which a --wayback recovery can rewrite underneath.
    CREATE TABLE IF NOT EXISTS body_origin (
        url        TEXT PRIMARY KEY,  -- releases.url
        origin_url TEXT NOT NULL      -- where the body was read from: an
                                      -- archive.org capture address, which is
                                      -- also the page_cache key
    );

    -- One row per HTTP attempt against archive.org - a CDX query or a content
    -- fetch - purely for later analysis (latency distributions, how often and
    -- when 503s cluster, whether a timeout constant is well-tuned). Never
    -- read by any scraper; see wayback.record_wayback_call's docstring for
    -- why this exists instead of judging archive.org's behavior from a
    -- handful of manual curl calls in one session.
    CREATE TABLE IF NOT EXISTS wayback_calls (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ts             REAL NOT NULL,  -- time.time() when the attempt started
        kind           TEXT NOT NULL,  -- 'cdx_probe' | 'cdx_bulk' | 'content'
        url            TEXT,
        attempt        INTEGER NOT NULL,  -- 0-based, within one call's retry loop
        timeout_budget REAL NOT NULL,
        outcome        TEXT NOT NULL,  -- 'ok' | '503' | '429' | 'timeout' | 'connection_error' | 'other'
        duration       REAL NOT NULL   -- seconds this one attempt took
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
    "INSERT OR IGNORE INTO releases "
    "(source, detail_id, title, date, url, body, body_html, grade) "
    "VALUES (?,?,?,?,?,?,?,?)"
)

_UPGRADE_SQL = """
    UPDATE releases
       SET detail_id = COALESCE(?, detail_id),
           title     = COALESCE(?, title),
           date      = COALESCE(?, date),
           body      = COALESCE(?, body),
           body_html = COALESCE(?, body_html),
           grade     = COALESCE(?, grade)
     WHERE url = ?
"""


def content_hash(content: bytes) -> str:
    """sha256 of a cached page's bytes, hex. One implementation for the two
    write sites (fetch.fetch_cached, wayback.fetch_snapshot) and for the pass
    that fills it in for older rows."""
    return hashlib.sha256(content).hexdigest()


def same_bytes(conn: sqlite3.Connection, url: str) -> list:
    """Other addresses in page_cache holding byte-identical content to `url`'s.

    Answers "we already have these bytes, under another name" without guessing
    from file names or paths. Empty when the entry is unique, or when its hash
    has not been filled in yet.
    """
    row = conn.execute("SELECT content_sha256 FROM page_cache WHERE url = ?",
                       (url,)).fetchone()
    if not row or not row[0]:
        return []
    return [u for (u,) in conn.execute(
        "SELECT url FROM page_cache WHERE content_sha256 = ? AND url <> ?",
        (row[0], url))]


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


def _rename_wayback_cache(conn: sqlite3.Connection) -> None:
    """wayback_cache -> page_cache. The table stopped being archive.org-only
    when the live scrapers (Q4, Creative, GlobeNewswire) started caching their
    fetches through it, and a table whose name lies about its contents is the
    kind of thing this repo pays for later. Runs before the CREATE TABLE in
    _SCHEMA_SQL, which would otherwise make an empty page_cache alongside the
    full wayback_cache."""
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "wayback_cache" in names and "page_cache" not in names:
        conn.execute("ALTER TABLE wayback_cache RENAME TO page_cache")
        conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if absent, apply migrations, install FTS triggers.
    Idempotent - every scraper calls it once at startup."""
    _rename_wayback_cache(conn)
    # Before the CREATE TABLEs, for the same reason: the script would otherwise
    # create an empty body_origin beside the populated body_origin, and a
    # rename guarded on "the target does not exist" would then never fire.
    _rename_to_body_origin(conn)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()

    # page_cache may predate these diagnostic columns (SQLite has no
    # "ADD COLUMN IF NOT EXISTS") - add whichever are missing.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(page_cache)").fetchall()}
    for col, coltype in (("id_content_type", "TEXT"), ("fw_guessed_charset", "TEXT"),
                         ("bs4_encoding", "TEXT"), ("content_sha256", "TEXT"),
                         ("fetched_at", "REAL")):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE page_cache ADD COLUMN {col} {coltype}")
    # After the ALTER, not in _SCHEMA_SQL: on an existing database the script
    # runs before the column is added, and CREATE INDEX on a column that is not
    # there yet fails the whole init.
    conn.execute("CREATE INDEX IF NOT EXISTS page_cache_sha "
                 "ON page_cache(content_sha256)")

    if "body_html" not in {row[1] for row in conn.execute("PRAGMA table_info(releases)")}:
        conn.execute("ALTER TABLE releases ADD COLUMN body_html TEXT")

    # body_origin shipped with a redundant page_url (a prefix of origin_url).
    if "page_url" in {row[1] for row in conn.execute("PRAGMA table_info(body_origin)")}:
        conn.execute("ALTER TABLE body_origin DROP COLUMN page_url")
    conn.commit()

    _migrate_grade(conn)
    _drop_matched(conn)

    _sync_fts_triggers(conn)


def _migrate_grade(conn: sqlite3.Connection) -> None:
    """Move the two grade sentinels out of `detail_id` into `grade`.

    Idempotent by construction rather than by a version flag: the ALTER is
    guarded on the column being absent, and the UPDATE matches nothing once it
    has run. `detail_id` is set to NULL for those rows because 'teaser'/'stub'
    never *were* references - the scraper had no capture to name, which is
    exactly what the row was recording. Callers asking "is this row worth
    retrying" must read `grade` (stored_grade), not `detail_id`; the one that
    only wanted "does a row exist" is already served by already_stored().
    """
    if "grade" not in {row[1] for row in conn.execute("PRAGMA table_info(releases)")}:
        # A DEFAULT on ALTER TABLE fills every existing row without firing the
        # FTS update trigger, which is right: title and body are untouched.
        conn.execute("ALTER TABLE releases ADD COLUMN grade TEXT NOT NULL DEFAULT 'full'")
    moved = conn.execute(
        "UPDATE releases SET grade = detail_id, detail_id = NULL "
        "WHERE detail_id IN ('teaser', 'stub')").rowcount
    if moved:
        print(f"Migrated {moved} rows: detail_id -> grade", flush=True)
    conn.commit()


def _rename_to_body_origin(conn: sqlite3.Connection) -> None:
    """body_capture -> body_origin, capture_url -> origin_url.

    The old name glued a column of `releases` to a concept from `page_cache` and
    read like a table storing captures *of* bodies; what it stores is where each
    body came from. Runs after the column migrations above, which still address
    the old name - on a renamed database their PRAGMA finds nothing and they are
    no-ops.
    """
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "body_capture" in names and "body_origin" not in names:
        conn.execute("ALTER TABLE body_capture RENAME TO body_origin")
        conn.commit()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(body_origin)")}
    if "capture_url" in cols and "origin_url" not in cols:
        conn.execute("ALTER TABLE body_origin RENAME COLUMN capture_url TO origin_url")
        conn.commit()


def _drop_matched(conn: sqlite3.Connection) -> None:
    """Remove body_origin.matched, whose three classes are derivable without it
    (see the table's comment). Idempotent: guarded on the column being present.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(body_origin)")}
    if "matched" in cols:
        conn.execute("ALTER TABLE body_origin DROP COLUMN matched")
        conn.commit()


def connect(db_path=None) -> sqlite3.Connection:
    """Open pressroom.db with the schema ensured."""
    conn = sqlite3.connect(db_path or DB_PATH)
    init_db(conn)
    return conn


def store_release(conn: sqlite3.Connection, source: str, url: str, *,
                  title: str = "", date: str = "", body: str = "",
                  body_html=None, detail_id=None, grade: str = "full",
                  commit: bool = True) -> bool:
    """INSERT OR IGNORE one release, keyed on `url` (UNIQUE).

    Returns True only if a row was actually inserted; False means the url was
    already present and nothing was written. Callers keeping a "new" counter
    should gate it on this rather than incrementing unconditionally.

    Content fields are keyword-only on purpose: the six positional columns
    were easy to transpose silently, and this way a mistake is a TypeError.
    Pass commit=False when the caller commits once after a loop.

    `grade` defaults to 'full' because most callers store a real article; a
    caller that could only get the listing blurb passes grade="teaser" (or
    "stub" for title/date only) and leaves detail_id alone. Those two strings
    used to be written *into* detail_id, which is the union this split undid.
    """
    cur = conn.execute(_INSERT_SQL,
                       (source, detail_id, title, date, url, body, body_html, grade))
    if commit:
        conn.commit()
    return cur.rowcount > 0


def upgrade_release(conn: sqlite3.Connection, url: str, *,
                    body=None, body_html=None, title=None, date=None,
                    detail_id=None, grade=None, commit: bool = True) -> bool:
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

    A pass that replaces a teaser body with the real article must say so with
    grade="full" - otherwise the row keeps a verdict that stopped being true,
    and `stored_grade()` will hand it to the next run as still-upgradable.

    Returns True if a row matched `url`.
    """
    cur = conn.execute(_UPGRADE_SQL,
                       (detail_id, title, date, body, body_html, grade, url))
    if commit:
        conn.commit()
    return cur.rowcount > 0


def capture_page_of(origin_url: str):
    """The page a `body_origin.origin_url` is a capture of, or None.

    One split on the `id_/` marker wayback.py puts in every page_cache key -
    which is why the column stores the whole address and the page is derived.
    Lives here rather than in wayback.py because serve.py needs it and must not
    import requests, and here rather than twice because it was twice: this
    function and repair_capture_provenance.page_of, the same line in two files.
    """
    if not origin_url or "id_/" not in origin_url:
        return None
    return origin_url.split("id_/", 1)[1]


def clear_body_origin(conn: sqlite3.Connection, url: str, commit: bool = True) -> None:
    """Forget the recorded capture for `url`.

    Called by any pass that rewrites a body from the row's *own* capture: the
    recorded one then no longer describes where the text came from, and a stale
    entry would keep the browser linking a listing for text that no longer came
    from it. Cheap and unconditional - most urls have no entry to begin with.
    """
    conn.execute("DELETE FROM body_origin WHERE url = ?", (url,))
    if commit:
        conn.commit()


def already_stored(conn: sqlite3.Connection, url: str) -> bool:
    """Whether any row exists for `url`. The right question for a loop that
    only skips what it has already seen; a loop that wants to know whether the
    row is worth upgrading asks stored_grade()."""
    return conn.execute("SELECT 1 FROM releases WHERE url = ?", (url,)).fetchone() is not None


def record_wayback_call(conn: sqlite3.Connection, *, kind: str, url: str, attempt: int,
                        timeout_budget: float, outcome: str, duration: float) -> None:
    """Append one row to wayback_calls - a single HTTP attempt against
    archive.org, whatever it resulted in.

    Best-effort and silent on failure by design, same as the encoding
    diagnostics in wayback.fetch_snapshot: this is a side channel for later
    analysis, and must never be able to break an actual scrape (e.g. if the
    schema migration hasn't run yet against an older db.connect() call held
    open across a code update).
    """
    try:
        conn.execute(
            "INSERT INTO wayback_calls (ts, kind, url, attempt, timeout_budget, outcome, duration) "
            "VALUES (?,?,?,?,?,?,?)",
            (time.time(), kind, url, attempt, timeout_budget, outcome, duration),
        )
        conn.commit()
    except Exception:
        pass


def stored_grade(conn: sqlite3.Connection, url: str):
    """None if no row exists for `url`, else 'full' | 'teaser' | 'stub' - i.e.
    whether a future run should try to upgrade this row. The scrapers' loop
    condition: `grade is not None and grade != "teaser"` means "already stored
    and already as good as this source can get"."""
    row = conn.execute("SELECT grade FROM releases WHERE url = ?", (url,)).fetchone()
    return row[0] if row else None


def record_body_origin(conn: sqlite3.Connection, url: str, origin_url: str,
                       commit: bool = True) -> None:
    """Record which capture a row's body came from. One statement, one place,
    like every other write here."""
    conn.execute(
        "INSERT INTO body_origin (url, origin_url) VALUES (?,?) "
        "ON CONFLICT(url) DO UPDATE SET origin_url = excluded.origin_url",
        (url, origin_url))
    if commit:
        conn.commit()


def stored_body_length(conn: sqlite3.Connection, url: str):
    """None if no row exists for `url`, else the length of its body.

    The way to tell a teaser-grade row from a fully recovered one when `grade`
    cannot: the pressdb and media_pr scrapers put the
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


# --- Read helpers for the browser (serve.py) -------------------------------
#
# serve.py owns HTTP and nothing else, so every statement it needs lives here,
# same as the scrapers' writes. These are read-only by construction: none of
# them takes a connection they could migrate, and connect_ro below hands out a
# connection SQLite itself refuses to write through.

# The two damage shapes described in CLAUDE.md's encoding convention: 'â€' is
# UTF-8 read as something 8-bit, a raw C1 control character is cp1252 read as
# ISO-8859-1 (0x99 ™, 0x92 ', 0x93 ", 0x84 „ are the ones that occur most,
# plus 0x81 which cp1252 does not define at all and repair_encoding.py
# therefore refuses to touch - it still has to show up in the audit view).
#
# This is the same rule as encoding.C1_RE / encoding.MOJIBAKE_RE, written in
# SQL because SQLite has no regex; the audit view needs it as a WHERE clause.
# Two languages, one rule: change one and change the other.
#
# The C1 half used to name five codepoints by hand - the ones that happened to
# occur when it was written - while encoding.C1_RE has always matched the whole
# 0x80-0x9F range. So the audit view under-reported: after the 2026-08-21
# refetch it showed 12 damaged rows where repair_encoding.py found 35. The
# range is spelled out here instead, 32 instr() calls generated from the same
# bounds, so the two cannot drift again.
_C1_SQL = " OR ".join(f"instr(r.body, char({c})) > 0" for c in range(0x80, 0xA0))

_MOJIBAKE_SQL = (
    "(instr(r.body, 'â€') > 0 OR instr(r.body, 'Ã') > 0"
    f" OR {_C1_SQL})"
)

_FLAG_SQL = {
    "teaser": "r.grade IN ('teaser', 'stub')",
    "short": "length(COALESCE(r.body, '')) < 300",
    "nodate": "(r.date IS NULL OR r.date = '')",
    "mojibake": _MOJIBAKE_SQL,
    # Rows still stored as one flat blob, i.e. not yet re-extracted through
    # richtext.py. This is a progress bar for a re-scrape that spans many
    # sessions, not a defect in the source material.
    #
    # Attachment rows are excluded: a .pdf/.doc release was extracted with
    # pdftotext/antiword and has no HTML behind it, so body_html is NULL there
    # permanently and by design. Counting them made the audit overstate the
    # remaining work by 358 rows out of 1706 - and the whole point of this view
    # is that its numbers are not a lie.
    #
    # Third instance of the mirror-rule pattern (see _MOJIBAKE_SQL): this is
    # scrape_midiman_pressdb.is_html_detail / ATTACHMENT_EXTS written in SQL,
    # because db.py may not import a scraper. Change one and change the other.
    "plain": ("r.body_html IS NULL"
              " AND lower(r.url) NOT LIKE '%.pdf'"
              " AND lower(r.url) NOT LIKE '%.doc'"),
}

# Cursors are opaque to the caller: FTS pages by offset (bm25 cannot be
# keyset-paginated), browsing pages by (date, id) keyset. Both come back as one
# string so serve.py and the frontend never have to know which mode they're in.
_MAX_OFFSET = 1000

# `c.origin_url` rides along on every list row for the same reason get_release
# joins it: a row's detail_id timestamp does not always name a capture of that
# row's own url, so a reader cannot build the capture link from the timestamp
# alone. NULL is the common case and means it can.
_ROW_COLS = ("r.id, r.source, r.date, r.title, r.url, r.detail_id, r.grade, "
             "length(COALESCE(r.body, '')), " + _MOJIBAKE_SQL + ", c.origin_url")

_ROW_JOIN = " LEFT JOIN body_origin c ON c.url = r.url"


def connect_ro(db_path=None) -> sqlite3.Connection:
    """Open pressroom.db read-only, for a reader that must not be able to
    change it.

    Deliberately NOT connect(): that calls init_db(), which migrates and
    installs FTS triggers. A browser has no business doing either, and
    ?mode=ro makes an accidental write an OperationalError from SQLite rather
    than a corrupted index nobody notices. check_same_thread=False because
    serve.py hands each request thread its own connection.
    """
    path = Path(db_path) if db_path else DB_PATH
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)


def _row_dict(row, excerpt_key: str) -> dict:
    (rid, source, date, title, url, detail_id, grade, body_len, damaged,
     origin_url) = row[:10]
    return {
        "id": rid,
        "source": source,
        "date": date or "",
        "title": title or "",
        "url": url or "",
        "detail_id": detail_id,
        "grade": grade,
        "body_len": body_len,
        # Judged over the whole body in SQL, not client-side over the excerpt:
        # most damage sits past the 240 characters a listing row ever shows.
        "damaged": bool(damaged),
        "origin_url": origin_url,
        excerpt_key: row[10] or "",
    }


def _filters(sources, date_from, date_to, flags):
    """Build the WHERE fragments shared by both query shapes."""
    clauses, params = [], []
    if sources:
        clauses.append(f"r.source IN ({','.join('?' * len(sources))})")
        params.extend(sources)
    if date_from:
        clauses.append("r.date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("r.date <= ?")
        params.append(date_to)
    for flag in flags or ():
        if flag in _FLAG_SQL:
            clauses.append(_FLAG_SQL[flag])
    return "".join(f" AND {c}" for c in clauses), params


def _fts_match(conn, sql, params):
    """Run an FTS query, retrying once with the whole query as a literal phrase.

    FTS5 barewords are alphanumeric, so 'M-Audio' - the single most likely
    thing to type at this corpus - is a syntax error, as is a stray quote or
    a trailing AND. Trying the raw string first keeps AND/OR/NOT, "phrases"
    and prefix* working for anyone who means them; the retry means everyone
    else gets results instead of a 500.
    """
    try:
        return conn.execute(sql, params).fetchall(), "raw"
    except sqlite3.OperationalError:
        quoted = '"' + str(params[0]).replace('"', '""') + '"'
        return conn.execute(sql, [quoted] + list(params[1:])).fetchall(), "literal"


def search_releases(conn: sqlite3.Connection, q: str = "", *, sources=None,
                    date_from=None, date_to=None, order: str = "rank",
                    flags=None, limit: int = 50, after=None) -> dict:
    """One page of releases, with or without a full-text query.

    An empty `q` is not a degenerate search but the browsing case: it skips
    releases_fts entirely and pages through `releases` by (date, id), which is
    also the only way to reach the 85 rows whose date is ''. A non-empty `q`
    joins releases_fts exactly as search.py does - snippet()'s ordinal 1 is
    `body`, positional per the fts5(title, body) declaration.

    Returns {"results": [...], "next": cursor|None, "truncated": bool,
    "query_mode": "raw"|"literal"|None}. `truncated` is True when more pages
    exist but bm25 paging has hit _MAX_OFFSET - the cap is reported rather
    than silently applied.
    """
    limit = max(1, min(int(limit), 200))
    where, params = _filters(sources, date_from, date_to, flags)

    if q:
        offset = 0
        if after and after.startswith("o:"):
            offset = min(int(after[2:]), _MAX_OFFSET)
        ordering = "r.date DESC, r.id DESC" if order == "date" else "bm25(releases_fts)"
        sql = f"""
            SELECT {_ROW_COLS}, snippet(releases_fts, 1, '>>>', '<<<', '…', 24)
              FROM releases_fts
              JOIN releases r ON releases_fts.rowid = r.id{_ROW_JOIN}
             WHERE releases_fts MATCH ?{where}
             ORDER BY {ordering}
             LIMIT ? OFFSET ?
        """
        rows, mode = _fts_match(conn, sql, [q] + params + [limit + 1, offset])
        more = len(rows) > limit
        nxt = f"o:{offset + limit}" if more and offset + limit < _MAX_OFFSET else None
        return {
            "results": [_row_dict(r, "excerpt") for r in rows[:limit]],
            "next": nxt,
            "truncated": more and nxt is None,
            "query_mode": mode,
        }

    keyset, keyset_params = "", []
    if after and after.startswith("d:"):
        _, date, rid = after.split(":", 2)
        keyset = " AND (r.date < ? OR (r.date = ? AND r.id < ?))"
        keyset_params = [date, date, int(rid)]
    sql = f"""
        SELECT {_ROW_COLS}, substr(COALESCE(r.body, ''), 1, 240)
          FROM releases r{_ROW_JOIN}
         WHERE 1=1{where}{keyset}
         ORDER BY r.date DESC, r.id DESC
         LIMIT ?
    """
    rows = conn.execute(sql, params + keyset_params + [limit + 1]).fetchall()
    page = rows[:limit]
    nxt = f"d:{page[-1][2] or ''}:{page[-1][0]}" if len(rows) > limit else None
    return {
        "results": [_row_dict(r, "excerpt") for r in page],
        "next": nxt,
        "truncated": False,
        "query_mode": None,
    }


def get_release(conn: sqlite3.Connection, rid: int):
    """One full row by id, body included, or None.

    `origin_url` is the capture the body was read out of, and it is present
    only when that capture is *not* one of the row's own url - i.e. only for
    the rows whose text came off a listing. serve.py turns it into the link and
    names the page; without it a reader can only guess from the timestamp, and
    for these rows that guess is a page that never existed."""
    row = conn.execute(
        f"""SELECT r.id, r.source, r.detail_id, r.grade, r.title, r.date, r.url,
                  r.body, {_MOJIBAKE_SQL}, r.body_html, c.origin_url
             FROM releases r
             LEFT JOIN body_origin c ON c.url = r.url
            WHERE r.id = ?""",
        (rid,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "source": row[1], "detail_id": row[2], "grade": row[3],
        "title": row[4] or "", "date": row[5] or "", "url": row[6] or "",
        "body": row[7] or "", "damaged": bool(row[8]),
        "body_html": row[9], "origin_url": row[10],
    }


def neighbours(conn: sqlite3.Connection, rid: int) -> dict:
    """The chronologically adjacent rows within the same source, for reading a
    source straight through. Ordered by (date, id) so the 85 dateless rows
    still have a stable position instead of dropping out of the sequence."""
    row = conn.execute("SELECT source, date, id FROM releases WHERE id = ?", (rid,)).fetchone()
    if row is None:
        return {"prev": None, "next": None}
    source, date, _ = row
    date = date or ""
    out = {}
    for key, cmp, direction in (("prev", "<", "DESC"), ("next", ">", "ASC")):
        hit = conn.execute(
            f"""SELECT id, title, date FROM releases
                 WHERE source = ?
                   AND (COALESCE(date, '') {cmp} ?
                        OR (COALESCE(date, '') = ? AND id {cmp} ?))
                 ORDER BY COALESCE(date, '') {direction}, id {direction}
                 LIMIT 1""",
            (source, date, date, rid),
        ).fetchone()
        out[key] = {"id": hit[0], "title": hit[1] or "", "date": hit[2] or ""} if hit else None
    return out


def quality_counts(conn: sqlite3.Connection) -> dict:
    """Corpus-wide gap counters for the audit view: how much of the corpus is
    teaser-grade, dateless, suspiciously short or encoding-damaged. Short body
    matters independently of detail_id because pressdb and media_pr rows carry
    the *listing* capture's timestamp even when the body is just its blurb.

    'plain' counts rows whose body was never re-extracted through richtext.py,
    so they still render as one preformatted blob - excluding .pdf/.doc
    attachment rows, which have no HTML behind them and never will.

    'wayback' counts rows with a recorded archive capture (body_origin), and
    'platform_id' the rows that carry a reference but no capture: mostly the
    live sources storing their platform's own numeric id, plus 241 attachment
    rows whose .pdf/.doc bytes were never cached, so nothing can say which
    capture their text came out of. The key name is older than that second
    group - the browser labels it "bez capture", which is what it measures. Neither is derived from
    the *shape* of detail_id any more: counting digits was the same rule
    re-implemented in seven places, and it silently decided what a new source
    was allowed to store (soundonsound's docstring says so outright)."""
    row = conn.execute(f"""
        SELECT count(*),
               sum(r.grade = 'teaser'),
               sum(r.grade = 'stub'),
               sum(c.url IS NOT NULL),
               sum(c.url IS NULL AND r.detail_id IS NOT NULL),
               sum(length(COALESCE(r.body, '')) < 300),
               sum(COALESCE(r.body, '') = ''),
               sum(r.date IS NULL OR r.date = ''),
               sum({_MOJIBAKE_SQL}),
               sum(r.body_html IS NULL AND lower(r.url) NOT LIKE '%.pdf'
                                       AND lower(r.url) NOT LIKE '%.doc')
          FROM releases r
          LEFT JOIN body_origin c ON c.url = r.url
    """).fetchone()
    keys = ("total", "teaser", "stub", "wayback", "platform_id",
            "short", "empty", "nodate", "mojibake", "plain")
    return {k: (v or 0) for k, v in zip(keys, row)}


def list_sources(conn: sqlite3.Connection) -> list:
    """Every source with its size, date span and gap counts - one GROUP BY for
    both the browse sidebar and the audit table's per-source breakdown."""
    rows = conn.execute(f"""
        SELECT r.source, count(*),
               min(NULLIF(r.date, '')), max(NULLIF(r.date, '')),
               sum(r.grade IN ('teaser', 'stub')),
               sum(length(COALESCE(r.body, '')) < 300),
               sum(r.date IS NULL OR r.date = ''),
               sum({_MOJIBAKE_SQL}),
               sum(r.body_html IS NULL AND lower(r.url) NOT LIKE '%.pdf'
                                       AND lower(r.url) NOT LIKE '%.doc')
          FROM releases r
         GROUP BY r.source
         ORDER BY count(*) DESC
    """).fetchall()
    return [
        {"source": s, "count": n, "first": first or "", "last": last or "",
         "teaser": teaser or 0, "short": short or 0, "nodate": nodate or 0,
         "mojibake": moji or 0, "plain": plain or 0}
        for s, n, first, last, teaser, short, nodate, moji, plain in rows
    ]
