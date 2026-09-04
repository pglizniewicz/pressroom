"""The `releases` table, its full-text index, the three statements that change
them, and the SQL shapes the readers share.

Three invariants hold over what is declared here, and each fails *silently* when
broken: the `fts5(title, body)` column order is a positional ordinal in every
`snippet()` call; all three triggers must exist and must use the
external-content 'delete' form with the OLD values; and a bulk change made
outside the triggers leaves the index stale, which the next UPDATE turns into
corruption - see index.rebuild_fts.

`releases.url` is the dedup key (UNIQUE) and inserts are INSERT OR IGNORE, so a
rerun of any crawl is free - which is why a "new" counter is gated on
store_release()'s bool return rather than incremented after it.
"""

SCHEMA_SQL = """
    -- Column order is the order the file on disk has. Nothing reads
    -- positionally today, but this declaration is that file's only description.
    CREATE TABLE IF NOT EXISTS releases (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        source    TEXT NOT NULL,
        detail_id TEXT,
        title     TEXT,
        date      TEXT,
        url       TEXT UNIQUE,
        body      TEXT,
        -- The same content as `body`, but as the small HTML subset
        -- text/control/richtext.py emits: paragraphs, lists, headings, links,
        -- images, tables. NULL means the row predates that change and still
        -- renders as preformatted text. Not indexed: FTS reads
        -- `body`, which is derived from this column and therefore can never
        -- disagree with it.
        body_html TEXT,
        -- How good this row is: 'full' | 'teaser' | 'stub' (entity/grade.py).
        -- Its own column, and not a sentinel inside `detail_id`: a *grade
        -- of the body* does not belong in a field meant for a *reference to
        -- where the body came from*, and every reader of the packed form had
        -- to re-derive which of the two a value was. Nothing parses detail_id.
        -- 'full' states only what the scraper could tell: the media_pr
        -- and pressdb sources store a capture timestamp on a row whose body is
        -- just the listing blurb, so a 'full' grade there means "not marked
        -- otherwise", and length(body) remains the check.
        grade     TEXT NOT NULL DEFAULT 'full'
    );

    -- External-content FTS5 index: stores no text of its own, reads it live
    -- from `releases` at query time, and only sees changes through the
    -- triggers below. Column ORDER MUST NOT CHANGE - see invariant 1.
    CREATE VIRTUAL TABLE IF NOT EXISTS releases_fts USING fts5(
        title, body,
        content='releases',
        content_rowid='id',
        tokenize='unicode61'
    );
"""

# All three are required to keep releases_fts in sync: with only releases_ai,
# an UPDATE leaves the recovered text unsearchable and an out-of-band DELETE
# leaves an orphan that skews bm25() for every query. FTS5 reports none of it -
# 'integrity-check' passes on an index that is internally consistent and simply
# does not see that the content table changed under it.
#
# Deletes and updates must use the 'delete' command with the OLD values; a plain
# "DELETE FROM releases_fts" is not supported on an external-content table.
TRIGGERS_SQL = """
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

INSERT_SQL = (
    "INSERT OR IGNORE INTO releases "
    "(source, detail_id, title, date, url, body, body_html, grade) "
    "VALUES (?,?,?,?,?,?,?,?)"
)

UPGRADE_SQL = """
    UPDATE releases
       SET detail_id = COALESCE(?, detail_id),
           title     = COALESCE(?, title),
           date      = COALESCE(?, date),
           body      = COALESCE(?, body),
           body_html = COALESCE(?, body_html),
           grade     = COALESCE(?, grade)
     WHERE url = ?
"""

REBUILD_FTS_SQL = "INSERT INTO releases_fts(releases_fts) VALUES('rebuild')"


def insert(
    conn, *, source, detail_id, title, date, url, body, body_html, grade
) -> bool:
    """INSERT OR IGNORE one row. True if it was inserted, False if `url` was
    already there and nothing was written. No commit: the caller owns the
    transaction, because a body and its provenance are one write."""
    cur = conn.execute(
        INSERT_SQL, (source, detail_id, title, date, url, body, body_html, grade)
    )
    return cur.rowcount > 0


def upgrade(
    conn,
    url,
    *,
    detail_id=None,
    title=None,
    date=None,
    body=None,
    body_html=None,
    grade=None,
) -> bool:
    """UPDATE the row for `url`; None leaves that column alone. True if a row
    matched. No commit, for the same reason as `insert`."""
    cur = conn.execute(
        UPGRADE_SQL, (detail_id, title, date, body, body_html, grade, url)
    )
    return cur.rowcount > 0


def rebuild_fts(conn) -> None:
    """Reindex releases_fts from `releases`, from scratch. `control/index.py`
    says when to run this and why it cannot wait."""
    conn.execute(REBUILD_FTS_SQL)
    conn.commit()


# --- The query shapes both readers share (control/query.py) ----------------

# The two damage shapes: 'â€' is UTF-8 read as something 8-bit, a raw C1
# control character is cp1252 read as ISO-8859-1 (0x99 tm, 0x92 apostrophe,
# 0x93 quote, 0x84 low quote are the ones that occur most, plus 0x81 which
# cp1252 does not define at all and the repair therefore refuses to touch - it
# still has to show up in the audit view).
#
# This is the same rule as text/control/decoding.py's C1_RE / MOJIBAKE_RE,
# written in SQL because SQLite has no regex; the audit view needs it as a
# WHERE clause. tests/test_mirrored_rules.py holds the two together.
#
# The whole range, as 32 instr() calls generated from the same bounds rather
# than a hand-written list of the codepoints that happened to occur - which is
# how this half drifted from C1_RE and under-reported.
C1_SQL = " OR ".join(f"instr(r.body, char({c})) > 0" for c in range(0x80, 0xA0))

MOJIBAKE_SQL = f"(instr(r.body, 'â€') > 0 OR instr(r.body, 'Ã') > 0 OR {C1_SQL})"

FLAG_SQL = {
    "teaser": "r.grade IN ('teaser', 'stub')",
    "short": "length(COALESCE(r.body, '')) < 300",
    "nodate": "(r.date IS NULL OR r.date = '')",
    "mojibake": MOJIBAKE_SQL,
    # Rows still stored as one flat blob, i.e. not yet re-extracted through
    # richtext.py. This measures the remaining work of a re-scrape that spans
    # many sessions; it is not a defect in the source material.
    #
    # Attachment rows are excluded: a .pdf/.doc release was extracted with
    # pdftotext/antiword and has no HTML behind it, so body_html is NULL there
    # permanently and by design. Counting them made the audit overstate the
    # remaining work by 358 rows out of 1706 - and the whole point of this view
    # is that its numbers are correct.
    #
    # Third instance of the mirror-rule pattern (see MOJIBAKE_SQL): this is
    # converter/control/conversion.py's ATTACHMENT_EXTS written in SQL,
    # because this module may not import a parser. Change one and change the
    # other.
    "plain": (
        "r.body_html IS NULL"
        " AND lower(r.url) NOT LIKE '%.pdf'"
        " AND lower(r.url) NOT LIKE '%.doc'"
    ),
}
