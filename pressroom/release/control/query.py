"""Reading the corpus: the query shapes both readers share.

The CLI and the browser own no SQL of their own - the browser owns HTTP and
nothing else, the CLI owns argument parsing and printing - so every statement
they need lives here, once. Read-only by construction: none of these changes a
row, and connect_ro() hands out a connection SQLite itself refuses to write
through.

snippet()'s ordinal 1 is `body`, positional per the fts5(title, body)
declaration in entity/schema.py. That ordinal used to appear here *and* in the
CLI, which is two places for one invariant that fails silently.
"""

import sqlite3
from typing import Any

from pressroom.release.entity.schema import FLAG_SQL, MOJIBAKE_SQL

# Cursors are opaque to the caller: FTS pages by offset (bm25 cannot be
# keyset-paginated), browsing pages by (date, id) keyset. Both come back as one
# string so the browser and its frontend never have to know which mode they're in.
_MAX_OFFSET = 1000

# `c.origin_url` rides along on every list row for the same reason get_release
# joins it: a row's detail_id timestamp does not always name a capture of that
# row's own url, so a reader cannot build the capture link from the timestamp
# alone. NULL is the common case and means it can.
#
# Every computed column carries an AS. The rows arrive as sqlite3.Row and
# _row_dict reads them by name, and an unaliased expression gets whatever label
# SQLite invents for it - not a name to depend on.
_ROW_COLS = (
    "r.id, r.source, r.date, r.title, r.url, r.detail_id, r.grade, "
    "length(COALESCE(r.body, '')) AS body_len, "
    + MOJIBAKE_SQL
    + " AS damaged, c.origin_url"
)

_ROW_JOIN = " LEFT JOIN body_origin c ON c.url = r.url"


def _row_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source"],
        "date": row["date"] or "",
        "title": row["title"] or "",
        "url": row["url"] or "",
        "detail_id": row["detail_id"],
        "grade": row["grade"],
        "body_len": row["body_len"],
        # Judged over the whole body in SQL, not client-side over the excerpt:
        # most damage sits past the 240 characters a listing row ever shows.
        "damaged": bool(row["damaged"]),
        "origin_url": row["origin_url"],
        "excerpt": row["excerpt"] or "",
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
        if flag in FLAG_SQL:
            clauses.append(FLAG_SQL[flag])
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


def search_releases(
    conn: sqlite3.Connection,
    q: str = "",
    *,
    sources=None,
    date_from=None,
    date_to=None,
    order: str = "rank",
    flags=None,
    limit: int = 50,
    after=None,
) -> dict[str, Any]:
    """One page of releases, with or without a full-text query.

    An empty `q` is not a degenerate search but the browsing case: it skips
    releases_fts entirely and pages through `releases` by (date, id), which is
    also the only way to reach the 85 rows whose date is ''. A non-empty `q`
    joins releases_fts exactly as the CLI reader does - snippet()'s ordinal 1 is
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
            SELECT {_ROW_COLS},
                   snippet(releases_fts, 1, '>>>', '<<<', '…', 24) AS excerpt
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
            "results": [_row_dict(r) for r in rows[:limit]],
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
        SELECT {_ROW_COLS}, substr(COALESCE(r.body, ''), 1, 240) AS excerpt
          FROM releases r{_ROW_JOIN}
         WHERE 1=1{where}{keyset}
         ORDER BY r.date DESC, r.id DESC
         LIMIT ?
    """
    rows = conn.execute(sql, params + keyset_params + [limit + 1]).fetchall()
    page = rows[:limit]
    nxt = f"d:{page[-1]['date'] or ''}:{page[-1]['id']}" if len(rows) > limit else None
    return {
        "results": [_row_dict(r) for r in page],
        "next": nxt,
        "truncated": False,
        "query_mode": None,
    }


def get_release(conn: sqlite3.Connection, rid: int):
    """One full row by id, body included, or None.

    `origin_url` is the capture the body was read out of, and it is present
    only when that capture is *not* one of the row's own url - i.e. only for
    the rows whose text came off a listing. the browser turns it into the link and
    names the page; without it a reader can only guess from the timestamp, and
    for these rows that guess is a page that never existed."""
    row = conn.execute(
        f"""SELECT r.id, r.source, r.detail_id, r.grade, r.title, r.date, r.url,
                  r.body, {MOJIBAKE_SQL} AS damaged, r.body_html, c.origin_url
             FROM releases r
             LEFT JOIN body_origin c ON c.url = r.url
            WHERE r.id = ?""",
        (rid,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "source": row["source"],
        "detail_id": row["detail_id"],
        "grade": row["grade"],
        "title": row["title"] or "",
        "date": row["date"] or "",
        "url": row["url"] or "",
        "body": row["body"] or "",
        "damaged": bool(row["damaged"]),
        "body_html": row["body_html"],
        "origin_url": row["origin_url"],
    }


def neighbours(conn: sqlite3.Connection, rid: int) -> dict[str, dict[str, Any] | None]:
    """The chronologically adjacent rows within the same source, for reading a
    source straight through. Ordered by (date, id) so the 85 dateless rows
    still have a stable position instead of dropping out of the sequence."""
    row = conn.execute(
        "SELECT source, date, id FROM releases WHERE id = ?", (rid,)
    ).fetchone()
    if row is None:
        return {"prev": None, "next": None}
    source, date = row["source"], row["date"] or ""
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
        out[key] = (
            {"id": hit["id"], "title": hit["title"] or "", "date": hit["date"] or ""}
            if hit
            else None
        )
    return out


def quality_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Corpus-wide gap counters for the audit view: how much of the corpus is
    teaser-grade, dateless, suspiciously short or encoding-damaged. Short body
    matters independently of detail_id because pressdb and media_pr rows carry
    the *listing* capture's timestamp even when the body is just its blurb.

    'plain' counts rows whose body was never re-extracted through richtext,
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
        SELECT count(*)                                        AS total,
               sum(r.grade = 'teaser')                         AS teaser,
               sum(r.grade = 'stub')                           AS stub,
               sum(c.url IS NOT NULL)                          AS wayback,
               sum(c.url IS NULL AND r.detail_id IS NOT NULL)  AS platform_id,
               sum(length(COALESCE(r.body, '')) < 300)         AS short,
               sum(COALESCE(r.body, '') = '')                  AS empty,
               sum(r.date IS NULL OR r.date = '')              AS nodate,
               sum({MOJIBAKE_SQL})                             AS mojibake,
               sum(r.body_html IS NULL AND lower(r.url) NOT LIKE '%.pdf'
                                       AND lower(r.url) NOT LIKE '%.doc') AS plain
          FROM releases r
          LEFT JOIN body_origin c ON c.url = r.url
    """).fetchone()
    # The key names are the column names now. They used to be a ten-name tuple
    # twenty lines below the ten expressions it labelled, matched by position.
    return {k: (row[k] or 0) for k in row.keys()}


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every source with its size, date span and gap counts - one GROUP BY for
    both the browse sidebar and the audit table's per-source breakdown."""
    rows = conn.execute(f"""
        SELECT r.source, count(*),
               min(NULLIF(r.date, '')), max(NULLIF(r.date, '')),
               sum(r.grade IN ('teaser', 'stub')),
               sum(length(COALESCE(r.body, '')) < 300),
               sum(r.date IS NULL OR r.date = ''),
               sum({MOJIBAKE_SQL}),
               sum(r.body_html IS NULL AND lower(r.url) NOT LIKE '%.pdf'
                                       AND lower(r.url) NOT LIKE '%.doc')
          FROM releases r
         GROUP BY r.source
         ORDER BY count(*) DESC
    """).fetchall()
    return [
        {
            "source": s,
            "count": n,
            "first": first or "",
            "last": last or "",
            "teaser": teaser or 0,
            "short": short or 0,
            "nodate": nodate or 0,
            "mojibake": moji or 0,
            "plain": plain or 0,
        }
        for s, n, first, last, teaser, short, nodate, moji, plain in rows
    ]


# --- The CLI reader's one query --------------------------------------------

_CLI_SQL = """
    SELECT r.source, r.date, r.title, r.url, r.body,
           snippet(releases_fts, 1, '>>>', '<<<', '…', 24) AS excerpt
    FROM releases_fts
    JOIN releases r ON releases_fts.rowid = r.id
    WHERE releases_fts MATCH ?
      {source_clause}
    ORDER BY bm25(releases_fts)
    LIMIT ?
"""


def cli_search(conn, query: str, sources=None, limit: int = 8) -> list[tuple]:
    """Ranked rows for the terminal reader: (source, date, title, url, body,
    excerpt). Its own shape rather than search_releases(): the CLI wants five
    named columns and a snippet, not the browser's row dict with a cursor.

    Raises sqlite3.OperationalError through, so the caller can tell "no index
    yet" from "unparseable FTS query" and say which.
    """
    if sources:
        sql = _CLI_SQL.format(
            source_clause=f"AND r.source IN ({','.join('?' * len(sources))})"
        )
        params = [query] + list(sources) + [limit]
    else:
        sql = _CLI_SQL.format(source_clause="")
        params = [query, limit]
    return conn.execute(sql, params).fetchall()
