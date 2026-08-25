"""Where a row's bytes are: the page_cache key for the capture its body came
from, and the bytes themselves.

One concern, and it is a narrow one - `page_cache` is keyed by capture address,
and turning a row into that address takes two different routes depending on
whether anybody recorded it. Both used to be spelled out at seven call sites,
along with the `SELECT content FROM page_cache WHERE url = ?` that follows them.

No parsing here, and no verdicts: this module answers "which bytes" and nothing
about what they contain.
"""

import db
import wayback


def capture_key(detail_id, url: str) -> str:
    """The page_cache key for a capture of this row's own url, or "" when the
    detail_id is not a capture timestamp at all (the live sources' platform ids,
    or no reference).

    Both halves belong to wayback.py; this is the two of them in the order every
    caller needs them. It is a *derivation* though, and only right when the
    capture is of the row's own url - prefer origin_key(), which asks the
    database first.
    """
    return wayback.snapshot_url(detail_id, url) if wayback.is_timestamp(detail_id) else ""


def origin_key(conn, url: str, detail_id) -> str:
    """The page_cache key for this row's body, recorded if we know it.

    body_origin answers for 1727 rows, including the 158 whose text came off a
    *different* page - a listing, a print view, another release's page - which a
    derivation from (detail_id, url) can only get wrong. It also covers the
    attachment rows, whose real capture timestamp differs from the listing
    timestamp their detail_id holds. Falls back to the derivation so a row
    nothing has recorded yet still works.
    """
    row = conn.execute("SELECT origin_url FROM body_origin WHERE url = ?",
                       (url,)).fetchone()
    return row[0] if row else capture_key(detail_id, url)


def own_page(conn, url: str, detail_id) -> bool:
    """Whether the bytes we hold for this row are a capture of its *own* url.

    This is the question that picks the gate, which is why it is a function and
    not an inline comparison: a capture of another page (a listing holding 22
    releases, a print view) may only be written through bodygate.strict_same_text,
    and if the caller has a url-keyed listing collector it should use that
    instead. Handing such a capture to a whole-page parser cost 64 rows.
    """
    key = origin_key(conn, url, detail_id)
    return bool(key) and key == capture_key(detail_id, url)


def cached(conn, key: str):
    """The bytes page_cache holds under this key, or None."""
    if not key:
        return None
    row = conn.execute("SELECT content FROM page_cache WHERE url = ?",
                       (key,)).fetchone()
    return row[0] if row else None


def page_of(key: str) -> str:
    """The page a capture key is a capture of - for reports. db.capture_page_of
    does the split; this only supplies the fallback that keeps a report readable
    when an address carries no `id_/` marker."""
    return db.capture_page_of(key) or key
