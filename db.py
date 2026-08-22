#!/usr/bin/env python3
"""Schema, connection and read/write helpers for pressroom.db.

Owns the database concern end to end: both tables (`releases` + its FTS5
index, and `page_cache`), the migrations, and the release helpers every
scraper writes through. Deliberately imports stdlib only - no requests, no
bs4 - so search.py (which has no third-party dependencies) can import it too.

Not an ORM: plain sqlite3, plain SQL strings, one function per statement
shape. The point is that each statement exists exactly once.
"""

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
        bs4_encoding       TEXT
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
    "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body, body_html) "
    "VALUES (?,?,?,?,?,?,?)"
)

_UPGRADE_SQL = """
    UPDATE releases
       SET detail_id = COALESCE(?, detail_id),
           title     = COALESCE(?, title),
           date      = COALESCE(?, date),
           body      = COALESCE(?, body),
           body_html = COALESCE(?, body_html)
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
    conn.executescript(_SCHEMA_SQL)
    conn.commit()

    # page_cache may predate these diagnostic columns (SQLite has no
    # "ADD COLUMN IF NOT EXISTS") - add whichever are missing.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(page_cache)").fetchall()}
    for col in ("id_content_type", "fw_guessed_charset", "bs4_encoding"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE page_cache ADD COLUMN {col} TEXT")

    if "body_html" not in {row[1] for row in conn.execute("PRAGMA table_info(releases)")}:
        conn.execute("ALTER TABLE releases ADD COLUMN body_html TEXT")
    conn.commit()

    _sync_fts_triggers(conn)


def connect(db_path=None) -> sqlite3.Connection:
    """Open pressroom.db with the schema ensured."""
    conn = sqlite3.connect(db_path or DB_PATH)
    init_db(conn)
    return conn


def store_release(conn: sqlite3.Connection, source: str, url: str, *,
                  title: str = "", date: str = "", body: str = "",
                  body_html=None, detail_id=None, commit: bool = True) -> bool:
    """INSERT OR IGNORE one release, keyed on `url` (UNIQUE).

    Returns True only if a row was actually inserted; False means the url was
    already present and nothing was written. Callers keeping a "new" counter
    should gate it on this rather than incrementing unconditionally.

    Content fields are keyword-only on purpose: the six positional columns
    were easy to transpose silently, and this way a mistake is a TypeError.
    Pass commit=False when the caller commits once after a loop.
    """
    cur = conn.execute(_INSERT_SQL, (source, detail_id, title, date, url, body, body_html))
    if commit:
        conn.commit()
    return cur.rowcount > 0


def upgrade_release(conn: sqlite3.Connection, url: str, *,
                    body=None, body_html=None, title=None, date=None,
                    detail_id=None, commit: bool = True) -> bool:
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
    cur = conn.execute(_UPGRADE_SQL, (detail_id, title, date, body, body_html, url))
    if commit:
        conn.commit()
    return cur.rowcount > 0


def already_stored(conn: sqlite3.Connection, url: str) -> bool:
    """Whether any row exists for `url`. Note this is not the same as
    stored_detail_id(...) is not None - a row whose detail_id is NULL exists
    but yields None there."""
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


# --- Read helpers for the browser (serve.py) -------------------------------
#
# serve.py owns HTTP and nothing else, so every statement it needs lives here,
# same as the scrapers' writes. These are read-only by construction: none of
# them takes a connection they could migrate, and connect_ro below hands out a
# connection SQLite itself refuses to write through.

# 14 digits: a Wayback timestamp in detail_id means full text was recovered
# from that capture. 'teaser'/'stub'/NULL mean it wasn't.
_TS_GLOB = "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]"

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
    "teaser": "r.detail_id IN ('teaser', 'stub')",
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

_ROW_COLS = ("r.id, r.source, r.date, r.title, r.url, r.detail_id, "
             "length(COALESCE(r.body, '')), " + _MOJIBAKE_SQL)


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
    rid, source, date, title, url, detail_id, body_len, damaged = row[:8]
    return {
        "id": rid,
        "source": source,
        "date": date or "",
        "title": title or "",
        "url": url or "",
        "detail_id": detail_id,
        "body_len": body_len,
        # Judged over the whole body in SQL, not client-side over the excerpt:
        # most damage sits past the 240 characters a listing row ever shows.
        "damaged": bool(damaged),
        excerpt_key: row[8] or "",
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
              JOIN releases r ON releases_fts.rowid = r.id
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
          FROM releases r
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
    """One full row by id, body included, or None."""
    row = conn.execute(
        f"""SELECT r.id, r.source, r.detail_id, r.title, r.date, r.url, r.body,
                  {_MOJIBAKE_SQL}, r.body_html
             FROM releases r WHERE r.id = ?""",
        (rid,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "source": row[1], "detail_id": row[2],
        "title": row[3] or "", "date": row[4] or "", "url": row[5] or "",
        "body": row[6] or "", "damaged": bool(row[7]),
        "body_html": row[8],
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

    'wayback' counts rows whose detail_id is a 14-digit capture timestamp;
    'platform_id' the rest of the non-fallback ids, which are the Q4 sources
    (intel, amd) storing that platform's own numeric detail id instead - so a
    short detail_id there is not a defect."""
    row = conn.execute(f"""
        SELECT count(*),
               sum(r.detail_id = 'teaser'),
               sum(r.detail_id = 'stub'),
               sum(r.detail_id GLOB '{_TS_GLOB}'),
               sum(r.detail_id IS NOT NULL AND r.detail_id NOT IN ('teaser', 'stub')
                   AND NOT r.detail_id GLOB '{_TS_GLOB}'),
               sum(length(COALESCE(r.body, '')) < 300),
               sum(COALESCE(r.body, '') = ''),
               sum(r.date IS NULL OR r.date = ''),
               sum({_MOJIBAKE_SQL}),
               sum(r.body_html IS NULL AND lower(r.url) NOT LIKE '%.pdf'
                                       AND lower(r.url) NOT LIKE '%.doc')
          FROM releases r
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
               sum(r.detail_id IN ('teaser', 'stub')),
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
