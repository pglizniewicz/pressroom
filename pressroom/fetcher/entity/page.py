"""`page_cache`: the raw bytes of every page ever fetched.

Bytes, not text. The decoding decision belongs to whoever knows which
generation of markup it is reading, and three diagnostic signals recorded per
capture say what the page *claimed* - so a wrong choice is diagnosable later
without a refetch.

In the database, not on disk: auxiliary data belongs in the database, and this
is what makes a parser fix free. A scraper that fetches a page any other way is
a bug.
"""

import hashlib

SCHEMA_SQL = """
    -- Column order is the order the file on disk has; see release/entity.
    CREATE TABLE IF NOT EXISTS page_cache (
        url                TEXT PRIMARY KEY,
        content            BLOB NOT NULL,
        id_content_type    TEXT,
        fw_guessed_charset TEXT,
        bs4_encoding       TEXT,
        -- sha256 of `content`. Not a storage trick - the blobs stay, and
        -- deduplicating them would be the thing that makes sharding this table
        -- awkward later. It is an *identity* fact: the same attachment was
        -- served from midiman.com, midiman.net and m-audio.com, and until this
        -- column existed the only way to ask "are these the same bytes" was to
        -- match filenames, which quietly paired a row with a different release
        -- that happened to share a file name (#5343).
        content_sha256     TEXT,
        -- When these bytes were fetched (time.time()). NULL wherever the table
        -- never recorded it: the question "when did
        -- this file arrive" had no answer at all there - not even "before the
        -- call log started", since a cache hit is not logged as an attempt.
        fetched_at         REAL
    );

    CREATE INDEX IF NOT EXISTS page_cache_sha ON page_cache(content_sha256);
"""


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
    row = conn.execute(
        "SELECT content_sha256 FROM page_cache WHERE url = ?", (url,)
    ).fetchone()
    if not row or not row[0]:
        return []
    return [
        u
        for (u,) in conn.execute(
            "SELECT url FROM page_cache WHERE content_sha256 = ? AND url <> ?",
            (row[0], url),
        )
    ]
