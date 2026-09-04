"""Which bytes a row's body came out of, and whether they are its own page's.

`page_cache` is keyed by capture address, and turning a row into that address
takes two routes depending on whether anybody recorded it: the recorded one is
authoritative, the derived one is a guess that is only right when the capture is
of the row's own url.

The derivation itself is pure string work and lives with the other addresses
(fetcher/control/address.py); what is here is everything that needs the database
to answer. No parsing and no verdicts: this module answers "which bytes" and
nothing about what they contain.
"""

from pressroom.fetcher.control import address
from pressroom.provenance.entity import origin


def origin_key(conn, url: str, detail_id) -> str:
    """The page_cache key for this row's body, recorded if we know it.

    A recorded entry is the only thing that can be right for a row whose text
    came off a *different* page - a listing, a print view - or for an attachment
    row, whose real capture timestamp differs from the listing timestamp its
    detail_id holds. Falls back to the derivation for a row nothing has recorded.
    """
    row = conn.execute(
        "SELECT origin_url FROM body_origin WHERE url = ?", (url,)
    ).fetchone()
    return row[0] if row else address.capture_key(detail_id, url)


def own_page(conn, url: str, detail_id) -> bool:
    """Whether the bytes we hold for this row are a capture of its *own* url.

    This is the question that picks the gate: a capture of another page - a
    listing holding twenty releases, a print view - may only be written through
    the strict gate, and a caller with a url-keyed listing collector should use
    that instead. Handing such a capture to a whole-page parser cost rows.
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
    """The page a capture key is a capture of, falling back to the key itself so
    a report stays readable when an address carries no `id_/` marker."""
    return origin.page_of(key) or key


def capture_page(origin_url, row_url):
    """The page a `body_origin.origin_url` is a capture of - but only when that
    is *not* the row's own url, since that is the only case a reader has
    anything to say about.

    The split itself is origin.page_of. What belongs here is the comparison:
    body_origin covers every Wayback row rather than only the discrepant ones,
    so this is what keeps the badge off a row whose capture is of its own page.
    """
    page = origin.page_of(origin_url)
    return page if page and page != row_url else None


def capture_kind(page, row_url):
    """How the capture's page relates to the row's own url: 'mirror' | 'other'.

    A copy of the *same file* on a sibling domain is not a listing, and a badge
    saying "z listingu" for `midiman.net/.../BX5_PR.pdf` read off
    `m-audio.com/.../BX5_PR.pdf` is simply false. Both addresses are start urls
    of one scraper, so the label is "a copy from another domain".

    The file name decides, and only here: this is a *label*, never an identity
    test. Identity is established by the bytes and by the extracted text, because
    matching on the name alone once paired a row with a different release.
    """
    if not page or not row_url:
        return None
    return (
        "mirror"
        if page.rsplit("/", 1)[-1].lower() == row_url.rsplit("/", 1)[-1].lower()
        else "other"
    )
