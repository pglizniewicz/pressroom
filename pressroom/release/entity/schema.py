"""The `releases` table, its full-text index, and the SQL shapes over both.

Three invariants live here and every one of them fails *silently* when broken;
see index.py for the third.

1. **fts5(title, body) column order is load-bearing.** The readers call
   snippet(releases_fts, 1, ...) and that `1` is a positional ordinal. Swap the
   columns and it starts snippeting titles with no error at all.
2. **All three triggers must exist**, and updates and deletes must use the
   external-content 'delete' command form with the OLD values.
3. A bulk change made outside the triggers leaves the index stale, and the next
   UPDATE then corrupts it - see index.rebuild_fts.

`releases.url` is the dedup key (UNIQUE) and inserts are INSERT OR IGNORE, so a
rerun of any crawl is free. Gate any "new" counter on store_release()'s bool
return - an unconditional increment after it reports phantom inserts on every
rerun.
"""

SCHEMA_SQL = """
    -- Column order is the order the file on disk has, which is what a
    -- positional read would see. Nothing here reads positionally today
    -- (row_factory is sqlite3.Row and there is no SELECT * in the tree), but
    -- this declaration is the only description of that file, so it matches it.
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
        -- renders as preformatted text. Deliberately not indexed: FTS reads
        -- `body`, which is derived from this column and therefore can never
        -- disagree with it.
        body_html TEXT,
        -- How good this row is: 'full' | 'teaser' | 'stub' (entity/grade.py).
        -- Its own column, and not a sentinel inside `detail_id`: a *verdict
        -- about the body* does not belong in a field meant for a *reference to
        -- where the body came from*, and every reader of the packed form had
        -- to re-derive which of the two a value was. Nothing parses detail_id.
        -- 'full' is only ever as good as what the scraper knew: the media_pr
        -- and pressdb sources store a capture timestamp on a row whose body is
        -- just the listing blurb, so a 'full' grade there means "not marked
        -- otherwise", and length(body) remains the honest check.
        grade     TEXT NOT NULL DEFAULT 'full'
    );

    -- External-content FTS5 index: stores no text of its own, reads it live
    -- from `releases` at query time, and only learns about changes through
    -- the triggers below. Column ORDER IS LOAD-BEARING - see invariant 1.
    CREATE VIRTUAL TABLE IF NOT EXISTS releases_fts USING fts5(
        title, body,
        content='releases',
        content_rowid='id',
        tokenize='unicode61'
    );
"""

# All three are required to keep releases_fts in sync. Only releases_ai existed
# once, so every "UPDATE releases SET body = ..." left the recovered text
# unsearchable, and every out-of-band DELETE (e.g. from a GUI DB tool) left an
# orphaned index entry - which also skews bm25() corpus
# statistics for *every* query, not just the affected rows. FTS5 reports none
# of this: 'integrity-check' passes, because the index is internally
# consistent, it simply doesn't know the content table moved underneath it.
#
# Deletes and updates must use the special 'delete' command with the OLD
# values; a plain "DELETE FROM releases_fts" is not supported on an
# external-content table.
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


# --- The query shapes both readers share -----------------------------------
#
# Neither reader owns SQL of its own: the CLI and the browser go through
# control/query.py, and every statement lives once. These are read-only by
# construction - none of them changes a row.

# The two damage shapes: 'â€' is UTF-8 read as something 8-bit, a raw C1
# control character is cp1252 read as ISO-8859-1 (0x99 tm, 0x92 apostrophe,
# 0x93 quote, 0x84 low quote are the ones that occur most, plus 0x81 which
# cp1252 does not define at all and the repair therefore refuses to touch - it
# still has to show up in the audit view).
#
# This is the same rule as text/control/decoding.py's C1_RE / MOJIBAKE_RE,
# written in SQL because SQLite has no regex; the audit view needs it as a
# WHERE clause. Two languages, one rule: change one and change the other.
#
# The C1 half used to name five codepoints by hand - the ones that happened to
# occur when it was written - while C1_RE has always matched the whole
# 0x80-0x9F range. So the audit view under-reported: after the 2026-08-21
# refetch it showed 12 damaged rows where the repair found 35. The range is
# spelled out here instead, 32 instr() calls generated from the same bounds, so
# the two cannot drift again.
C1_SQL = " OR ".join(f"instr(r.body, char({c})) > 0" for c in range(0x80, 0xA0))

MOJIBAKE_SQL = f"(instr(r.body, 'â€') > 0 OR instr(r.body, 'Ã') > 0 OR {C1_SQL})"

FLAG_SQL = {
    "teaser": "r.grade IN ('teaser', 'stub')",
    "short": "length(COALESCE(r.body, '')) < 300",
    "nodate": "(r.date IS NULL OR r.date = '')",
    "mojibake": MOJIBAKE_SQL,
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
    # Third instance of the mirror-rule pattern (see MOJIBAKE_SQL): this is
    # attachment/control/conversion.py's ATTACHMENT_EXTS written in SQL,
    # because this module may not import a parser. Change one and change the
    # other.
    "plain": (
        "r.body_html IS NULL"
        " AND lower(r.url) NOT LIKE '%.pdf'"
        " AND lower(r.url) NOT LIKE '%.doc'"
    ),
}
