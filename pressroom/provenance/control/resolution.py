"""Which bytes a row's body came out of, and whether they are its own page's.

`page_cache` is keyed by capture address, and turning a row into that address
takes two routes depending on whether anybody recorded it: the recorded one is
authoritative, the derived one is a guess that is only right when the capture is
of the row's own url. Both used to be spelled out at seven call sites, along
with the SELECT that follows them.

The derivation itself is pure string work and lives with the other addresses
(capture/control/address.py); what is here is everything that needs the
database to answer.

No parsing, and no verdicts: this module answers "which bytes" and nothing
about what they contain.
"""

from pressroom.capture.control import address
from pressroom.provenance.entity import origin


def origin_key(conn, url: str, detail_id) -> str:
    """The page_cache key for this row's body, recorded if we know it.

    body_origin answers for 1727 rows, including the 158 whose text came off a
    *different* page - a listing, a print view, another release's page - which a
    derivation from (detail_id, url) can only get wrong. It also covers the
    attachment rows, whose real capture timestamp differs from the listing
    timestamp their detail_id holds. Falls back to the derivation so a row
    nothing has recorded yet still works.
    """
    row = conn.execute(
        "SELECT origin_url FROM body_origin WHERE url = ?", (url,)
    ).fetchone()
    return row[0] if row else address.capture_key(detail_id, url)


def own_page(conn, url: str, detail_id) -> bool:
    """Whether the bytes we hold for this row are a capture of its *own* url.

    This is the question that picks the gate, which is why it is a function and
    not an inline comparison: a capture of another page (a listing holding 22
    releases, a print view) may only be written through the strict gate,
    and if the caller has a url-keyed listing collector it should use that
    instead. Handing such a capture to a whole-page parser cost 64 rows.
    """
    key = origin_key(conn, url, detail_id)
    return bool(key) and key == address.capture_key(detail_id, url)


def cached(conn, key: str):
    """The bytes page_cache holds under this key, or None."""
    if not key:
        return None
    row = conn.execute(
        "SELECT content FROM page_cache WHERE url = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def page_of(key: str) -> str:
    """The page a capture key is a capture of - for reports. origin.page_of
    does the split; this only supplies the fallback that keeps a report readable
    when an address carries no `id_/` marker."""
    return origin.page_of(key) or key
