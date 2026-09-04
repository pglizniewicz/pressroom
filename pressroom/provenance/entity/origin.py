"""`body_origin`: which capture each row's body was actually read out of.

A scraper that reads a release out of a *listing* capture stores that listing's
timestamp in releases.detail_id, and a timestamp alone cannot say which page it
belongs to - so an address built from the row's own url is one that never
existed.

Its own table rather than a column on `releases`, because absence has to keep
meaning "no archive link for this row", which is the right answer for the live
sources too.

**Two columns and nothing else.** The whole address is stored and the page it
captures is derived from it by a pure string split (page_of); going the other
way would need releases.detail_id, which a recovery can rewrite underneath.
Whether the body can still be *produced* from those bytes is a different
question, and it has its own pass (pressroom-verify-body-origin).
"""

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS body_origin (
        url        TEXT PRIMARY KEY,  -- releases.url
        origin_url TEXT NOT NULL      -- where the body was read from: an
                                      -- archive.org capture address, which is
                                      -- also the page_cache key
    );
"""


def record(conn, url: str, origin_url: str, commit: bool = True) -> None:
    """Record which capture a row's body came from."""
    conn.execute(
        "INSERT INTO body_origin (url, origin_url) VALUES (?,?) "
        "ON CONFLICT(url) DO UPDATE SET origin_url = excluded.origin_url",
        (url, origin_url),
    )
    if commit:
        conn.commit()


def clear(conn, url: str, commit: bool = True) -> None:
    """Delete the recorded capture for `url`.

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
    the column stores the whole address and the page is derived. It is here
    rather than with the archive client because the browser needs it and must
    not import requests.
    """
    if not origin_url or "id_/" not in origin_url:
        return None
    return origin_url.split("id_/", 1)[1]
