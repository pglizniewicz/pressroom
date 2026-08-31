"""`page_cache`: the raw bytes of every page ever fetched.

Bytes, not text. The decoding decision belongs to whoever knows which
generation of markup it is reading, and three diagnostic signals recorded per
capture say what the page *claimed* - so a wrong choice is diagnosable later
without a refetch.

In the database, not on disk: auxiliary data belongs in the database, and this
is what makes a parser fix free. The table was called `wayback_cache` while
only the archive path used it; the four live sources went straight through
`session.get`, and when the extraction turned out to be wrong all four had to
be crawled again from scratch. It is named for what it actually holds now, and
a scraper that fetches a page any other way is a bug.
"""

import hashlib

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS page_cache (
        url                TEXT PRIMARY KEY,
        content            BLOB NOT NULL,
        id_content_type    TEXT,
        fw_guessed_charset TEXT,
        bs4_encoding       TEXT,
        -- When these bytes were fetched (time.time()). NULL on the 6345 entries
        -- written before this column existed, which is the honest answer: the
        -- table never recorded it, and the question "when did this file arrive"
        -- had no answer at all - not even "before the call log started", since
        -- a cache hit is not logged as an attempt.
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
"""

_ADDED_COLUMNS = (("id_content_type", "TEXT"), ("fw_guessed_charset", "TEXT"),
                  ("bs4_encoding", "TEXT"), ("content_sha256", "TEXT"),
                  ("fetched_at", "REAL"))


def rename_before_create(conn) -> None:
    """wayback_cache -> page_cache.

    The table stopped being archive.org-only when the live scrapers started
    caching their fetches through it, and a table whose name lies about its
    contents is the kind of thing this repo pays for later. Must run before the
    CREATE TABLE, which would otherwise make an empty page_cache alongside the
    full wayback_cache and leave this rename permanently unable to fire.
    """
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "wayback_cache" in names and "page_cache" not in names:
        conn.execute("ALTER TABLE wayback_cache RENAME TO page_cache")
        conn.commit()


def migrate(conn) -> None:
    """Add whichever diagnostic columns are missing, then the hash index.

    SQLite has no "ADD COLUMN IF NOT EXISTS". The index comes after the ALTER
    rather than living in SCHEMA_SQL: on an existing database the script runs
    before the column is added, and CREATE INDEX on a column that is not there
    yet fails the whole init.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(page_cache)")}
    for col, coltype in _ADDED_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE page_cache ADD COLUMN {col} {coltype}")
    conn.execute("CREATE INDEX IF NOT EXISTS page_cache_sha "
                 "ON page_cache(content_sha256)")
    conn.commit()


def content_hash(content: bytes) -> str:
    """sha256 of a cached page's bytes, hex. One implementation for both write
    sites - the live fetch path and the archive fetch path."""
    return hashlib.sha256(content).hexdigest()


def same_bytes(conn, url: str) -> list[str]:
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
