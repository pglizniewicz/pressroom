"""`body_origin`: which capture each row's body was actually read out of.

A scraper that reads a release out of a *listing* capture stores that listing's
timestamp in releases.detail_id, and the timestamp alone cannot say which page
it belongs to - so the browser used to build web/<ts>/<row url>, a capture that
never existed. #4414 was the report that turned this up: CDX has no capture of
that article, ever, while the listing capture holds its full text.

Its own table rather than a column on `releases`, because absence has to keep
meaning "no archive link for this row" - which is the right answer for the live
sources too.

**Two columns and nothing else**, and it took two removals to get there.
`page_url` agreed with `origin_url` in 158 of 158 rows, one being a prefix of
the other; origin_url is the one worth keeping, because the page is a pure
string split out of it while rebuilding it the other way would need
releases.detail_id, which a recovery can rewrite underneath. And `matched` -
the fraction of body probes found when an address had to be searched for - went
once its three classes turned out to be derivable from the address itself. The
number was never the useful thing: 88 of the 149 inferred rows scored below 1.0
and are right, four attachment rows scored a perfect 1.0 and were wrong. What a
reader wants is whether the body can be *produced* from those bytes, which is a
different question and has its own script.
"""

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS body_origin (
        url        TEXT PRIMARY KEY,  -- releases.url
        origin_url TEXT NOT NULL      -- where the body was read from: an
                                      -- archive.org capture address, which is
                                      -- also the page_cache key
    );
"""


def rename_before_create(conn) -> None:
    """body_capture -> body_origin, capture_url -> origin_url.

    The old name glued a column of `releases` to a concept from `page_cache`
    and read like a table storing captures *of* bodies; what it stores is where
    each body came from. Must run before the CREATE TABLE for the same reason
    page_cache's rename does: the script would otherwise create an empty
    body_origin beside the populated body_capture, and a rename guarded on "the
    target does not exist" would then never fire.
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


def migrate(conn) -> None:
    """Drop the two columns this table shipped with and no longer needs, per
    the docstring above. Idempotent: each is guarded on being present."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(body_origin)")}
    if "page_url" in cols:
        conn.execute("ALTER TABLE body_origin DROP COLUMN page_url")
    if "matched" in cols:
        conn.execute("ALTER TABLE body_origin DROP COLUMN matched")
    conn.commit()


def record(conn, url: str, origin_url: str, commit: bool = True) -> None:
    """Record which capture a row's body came from. One statement, one place,
    like every other write here."""
    conn.execute(
        "INSERT INTO body_origin (url, origin_url) VALUES (?,?) "
        "ON CONFLICT(url) DO UPDATE SET origin_url = excluded.origin_url",
        (url, origin_url))
    if commit:
        conn.commit()


def clear(conn, url: str, commit: bool = True) -> None:
    """Forget the recorded capture for `url`.

    Called by any pass that rewrites a body from somewhere other than the
    recorded capture: the entry then no longer describes where the text came
    from, and a stale one would keep the browser linking a listing for text
    that no longer came from one. Cheap and unconditional - most urls have no
    entry to begin with.
    """
    conn.execute("DELETE FROM body_origin WHERE url = ?", (url,))
    if commit:
        conn.commit()


def page_of(origin_url: str):
    """The page an `origin_url` is a capture of, or None.

    One split on the `id_/` marker every page_cache key carries - which is why
    the column stores the whole address and the page is derived. Lives here
    rather than with the archive client because the browser needs it and must
    not import requests.
    """
    if not origin_url or "id_/" not in origin_url:
        return None
    return origin_url.split("id_/", 1)[1]
