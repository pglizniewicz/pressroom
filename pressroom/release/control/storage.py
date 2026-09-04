"""Writing a release, and the four checks a scraper makes before it does.

Every change to a `releases` row goes through here, and exactly once; the
statements themselves are `entity/schema.py`'s, run by `schema.insert` and
`schema.upgrade`. What this module decides is what goes into them - the text
repair, the grade, the provenance written in the same transaction.
Not an ORM: plain sqlite3, plain SQL, one function per statement shape.

Commit per row by default, because these are hour-long crawls against a flaky
archive; a caller that batches passes commit=False and commits after its loop.
"""

import contextlib

from pressroom.fetcher.control import address
from pressroom.provenance.entity import origin
from pressroom.release.entity import schema
from pressroom.release.entity.grade import Grade
from pressroom.text.control.decoding import repaired


def _transaction(conn, commit: bool):
    """The transaction boundary for one write, or nothing when the caller owns it.

    `with conn:` rather than a bare conn.commit() because it **rolls back on an
    exception**, which makes a row and its body_origin entry one write:
    a raising origin write once left the INSERT in an open transaction for the
    next commit on that connection to include.
    """
    return conn if commit else contextlib.nullcontext()


def _record_origin(conn, url: str, origin_url) -> None:
    if origin_url is None:
        return
    if not address.is_capture_address(origin_url):
        raise ValueError(f"origin_url is not a capture address: {origin_url!r}")
    origin.record(conn, url, origin_url, commit=False)


def store_release(
    conn,
    source: str,
    url: str,
    *,
    title: str = "",
    date: str = "",
    body: str = "",
    body_html=None,
    detail_id=None,
    grade: str = Grade.FULL,
    origin_url=None,
    commit: bool = True,
) -> bool:
    """INSERT OR IGNORE one release, keyed on `url` (UNIQUE).

    Returns True only if a row was actually inserted; False means the url was
    already present and nothing was written. Callers keeping a "new" counter
    should gate it on this rather than incrementing unconditionally.

    Content fields are keyword-only: the six positional columns
    were easy to transpose silently, and this way a mistake is a TypeError.

    `grade` defaults to 'full' because most callers store a real article; a
    caller that could only get the listing blurb passes grade="teaser" (or
    "stub" for title/date only) and leaves detail_id alone.
    """
    with _transaction(conn, commit):
        inserted = schema.insert(
            conn,
            source=source,
            detail_id=detail_id,
            title=repaired(title),
            date=date,
            url=url,
            body=repaired(body) if body_html is None else body,
            body_html=body_html,
            grade=str(grade),
        )
        # Provenance only when the row was actually inserted. A url the
        # UNIQUE constraint made this a no-op for holds a body some other pass
        # wrote, and claiming our capture as its origin would be a false
        # statement about text we did not store.
        if inserted:
            _record_origin(conn, url, origin_url)
    return inserted


def upgrade_release(
    conn,
    url: str,
    *,
    body=None,
    body_html=None,
    title=None,
    date=None,
    detail_id=None,
    grade=None,
    origin_url=None,
    commit: bool = True,
) -> bool:
    """Upgrade an existing row in place - a teaser/stub replaced by recovered
    full text. None means "leave that column alone", so the call site states
    which columns the upgrade is allowed to touch:

        upgrade_release(conn, url, body=text)
            body only, e.g. a PDF's extracted text under the same url.
        upgrade_release(conn, url, detail_id=ts, body=text)
            keeps the title/date harvested from the listing page, which are
            better than anything the detail page carries.
        upgrade_release(conn, url, detail_id=ts, title=t, date=d, body=text)
            detail page is authoritative for all of them.

    A pass that replaces a teaser body with the real article must say so with
    grade="full" - otherwise the row keeps a grade that stopped being true,
    and `stored_grade()` will report it to the next run as still-upgradable.

    Returns True if a row matched `url`.
    """
    with _transaction(conn, commit):
        matched = schema.upgrade(
            conn,
            url,
            detail_id=detail_id,
            title=repaired(title),
            date=date,
            body=repaired(body) if body_html is None else body,
            body_html=body_html,
            grade=None if grade is None else str(grade),
        )
        # Same rule as store_release, one step further: the entry describes
        # where a *body* came from, so a call that only moves a title or a date
        # must not touch it.
        if matched and (body is not None or body_html is not None):
            _record_origin(conn, url, origin_url)
    return matched


def already_stored(conn, url: str) -> bool:
    """Whether any row exists for `url`. The right question for a loop that
    only skips what it has already seen; a loop that needs to know whether the
    row is worth upgrading uses stored_grade()."""
    return (
        conn.execute("SELECT 1 FROM releases WHERE url = ?", (url,)).fetchone()
        is not None
    )


def stored_grade(conn, url: str):
    """None if no row exists for `url`, else 'full' | 'teaser' | 'stub' - i.e.
    whether a future run should try to upgrade this row. The scrapers' loop
    condition: `grade is not None and grade != "teaser"` means "already stored
    and already as good as this source can get"."""
    row = conn.execute("SELECT grade FROM releases WHERE url = ?", (url,)).fetchone()
    return row[0] if row else None


def stored_body_length(conn, url: str):
    """None if no row exists for `url`, else the length of its body.

    The way to tell a teaser-grade row from a fully recovered one when `grade`
    cannot: the pressdb and media_pr scrapers put the *listing* capture's
    timestamp in detail_id even when the body they stored is only that
    listing's blurb, so a timestamp there says nothing about whether the real
    text was ever fetched. Length does.
    """
    row = conn.execute(
        "SELECT length(COALESCE(body, '')) FROM releases WHERE url = ?", (url,)
    ).fetchone()
    return row[0] if row else None


def source_total(conn, source: str) -> int:
    """Row count for one source - the figure every scraper's summary prints."""
    return conn.execute(
        "SELECT count(*) FROM releases WHERE source = ?", (source,)
    ).fetchone()[0]


def source_urls(conn, source: str):
    """Yield every stored url for `source`; callers derive their own dedup key
    (a sid, a filename) from it."""
    for (url,) in conn.execute("SELECT url FROM releases WHERE source = ?", (source,)):
        yield url
