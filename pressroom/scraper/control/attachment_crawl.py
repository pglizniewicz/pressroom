#!/usr/bin/env python3
"""Phase 2 for the rows whose `url` points at a .pdf or .doc file, not a page.

A second contract beside catch_up.py: no HTML parser (the extractor *is* the
parser), bytes addressed through body_origin or a mirror domain rather than the
row's own detail_id, and gates of strict_same_text for a re-extraction and
same_words for the text-to-markup conversion. Rows are UPDATEd in place, since
the fuller content is at the url already stored.

Two CMS generations link out this way and neither scraper follows the link, so
both store only the listing-page teaser:
  - pressdb.php    (midiman_com_pressdb, midiman_net_pressdb) - .pdf only
  - media.media_pr (midiman_com/net/maudio_com_media_pr)      - .doc and .pdf

Called by those two scrapers, never run on its own. Everything that costs no
request happens by default; the network crawl is opt-in (`--attachments`), the
one exception to "a plain rerun gets everything" - nothing records "CDX has no
capture of this url, ever", so a full pass re-requests every confirmed absence.

Fetching goes through archive.fetch_best_matching_snapshot, never
get_latest_working_snapshot: `statuscode:200` proves archive.org answered, not
that the answer was the attachment, and both known shapes of that gap - an
origin server's own soft-404, a modern redesign answering 200 for a long-gone
path - come back as HTTP 200. An attachment is often archived under only one of
the three mirror domains, so domain_variants() tries the siblings first.

A type kind_of() does not recognise is `uncertain`, never `dead`: we hold the
bytes, so it is a missing parser rather than a missing capture.
"""

import contextlib

import requests

from pressroom.fetcher.control import address
from pressroom.release.control import gate
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.reporting.control.outcome import Stats

# What an attachment's bytes mean is conversion.py's concern; this file owns the
# crawl.
from pressroom.converter.control import conversion
from pressroom.fetcher.control import archive

SOURCES = [
    "midiman_com_pressdb",
    "midiman_net_pressdb",
    "midiman_com_media_pr",
    "midiman_net_media_pr",
    "maudio_com_media_pr",
]

# The three domains that mirrored the same press releases through the
# Midiman -> M-Audio transition.
MIRROR_DOMAINS = ["www.midiman.com", "www.midiman.net", "www.m-audio.com"]

ATTACHMENT_SQL = """
    SELECT url, body FROM releases
     WHERE source = ?
       AND (lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc')
"""

# The longest listing teaser seen across these sources is 864 characters, so a
# row longer than this has certainly had its attachment extracted already.
# --only-short uses it because nothing records which rows are done, and a full
# pass re-probes every one of them over the network.
RECOVERED_LENGTH = 900


def attachment_score(content: bytes) -> int:
    """How much text this capture's file yields, or 0 when it is not an
    attachment at all.

    The same measure the write below decides on (`len(text) <= len(old_body)` is
    a `skipped`), so the walk cannot pick a copy its own gate will then refuse.
    A bare `is_attachment` bool would not do: with a binary score every capture
    ties, the earliest is always picked, and the two probes gain nothing - which
    is how an earlier, thinner revision of a PDF would start scoring above the
    fuller later one this crawl exists to find.
    """
    if not conversion.is_attachment(content):
        return 0
    text, _kind = conversion.plain_text(content)
    return len(text)


def domain_variants(url: str) -> list[str]:
    """`url` first, then the same path on each of the other mirror domains."""
    for domain in MIRROR_DOMAINS:
        if domain in url:
            return [url] + [
                url.replace(domain, other)
                for other in MIRROR_DOMAINS
                if other != domain
            ]
    return [url]


# The bytes page_cache already holds for an attachment row, addressed through
# the capture body_origin records for it - which for these rows is the real
# capture of the .pdf/.doc itself, not the listing timestamp their detail_id
# carries.
CACHED_ATTACHMENT_SQL = """
    SELECT r.source, r.url, r.body, p.content, c.origin_url
      FROM releases r
      JOIN body_origin c ON c.url = r.url
      JOIN page_cache p ON p.url = c.origin_url
     WHERE (lower(r.url) LIKE '%.pdf' OR lower(r.url) LIKE '%.doc')
       -- Rows already converted to markup are on the other route: re-extracting
       -- their text would try to replace real paragraphs with a pre-wrap blob,
       -- and strict_same_text refuses it - but as a policy, not as a near miss.
       AND r.body_html IS NULL
       {where}
     ORDER BY r.source, r.id
"""


def cursor_rows(conn, sql: str, sources: list | None, limit: int | None) -> list:
    """Rows of a `{where}` cursor query, narrowed to `sources` and capped at `limit`.

    The only thing the two offline passes share. Their columns differ, their
    gates differ, and the comment above each SQL constant is the whole reason
    that constant reads the way it does - so the query text stays with its pass
    and only the assembly is here.

    `limit` is a slice rather than a SQL LIMIT because that is what it has
    always been: both queries are ordered, so the two agree today, and swapping
    one for the other in passing would be a behaviour change inside what looks
    like a deduplication.
    """
    where, params = "", ()
    if sources:
        where = "AND r.source IN (%s)" % ",".join("?" * len(sources))
        params = tuple(sources)
    rows = conn.execute(sql.format(where=where), params).fetchall()
    return rows[:limit] if limit else rows


def reextract_from_cache(limit: int | None = None, sources: list | None = None) -> None:
    """Re-extract attachment text from bytes page_cache already holds. No network.

    The gate is strict_same_text rather than the length comparison the network
    path uses: the same extractor on the same bytes must produce the same
    characters, so whitespace is the only admissible change - which is exactly
    what a row stored before normalize() kept its line breaks needs.

    It is also the one write here that passes no `grade`. The gate admits
    nothing but a body that already *is* this extraction, so there is no grade
    for this pass to establish, and claiming one would be the same false
    statement in the other direction. The two writes that can replace a teaser
    say `grade="full"`.
    """
    with contextlib.closing(connection.connect()) as conn:
        rows = cursor_rows(conn, CACHED_ATTACHMENT_SQL, sources, limit)
        print(
            f"[from-cache] {len(rows)} attachment rows whose bytes are cached",
            flush=True,
        )

        stats = Stats(total=len(rows))
        held, gained_layout = [], 0
        for source, url, old_body, content, origin_url in rows:
            text, kind = conversion.plain_text(content)
            if not text:
                print(f"\n  {kind} extractor produced no text for {url}")
                stats.dead()
                continue
            ok, why = gate.strict_same_text(old_body or "", text)
            if not ok:
                held.append((url, why))
                stats.skipped()
                continue
            if text == (old_body or ""):
                stats.skipped()
                continue
            gained_layout += 1 if "\n" in text and "\n" not in (old_body or "") else 0
            # origin_url through the write, not beside it: `upgrade_release`
            # records it inside its own `with conn:`, so the text and the entry
            # are one transaction.
            storage.upgrade_release(conn, url, body=text, origin_url=origin_url)
            stats.upgraded()

        stats.summary()
        print(
            f"  layout recovered: {gained_layout} rows now have line breaks where they had none"
        )
        if held:
            print(f"WSTRZYMANE przez bramke: {len(held)} - nic nie zapisano")
            for url, why in held[:10]:
                print(f"  {why:26} {url}")


# Same rows as CACHED_ATTACHMENT_SQL, on the other side of `body_html`.
RICHTEXT_SQL = """
    SELECT r.id, r.source, r.url, r.body, p.content, c.origin_url
      FROM releases r
      JOIN body_origin c ON c.url = r.url
      JOIN page_cache p ON p.url = c.origin_url
     WHERE (lower(r.url) LIKE '%.pdf' OR lower(r.url) LIKE '%.doc')
       -- The same cursor reextract_from_cache walks, for the other half of the
       -- reason: converting a row that already carries markup runs the same
       -- extractor over the same bytes and writes them back identical, so every
       -- rerun would report `upgraded` for work it did not do. The filter is the
       -- only place that can be told apart - the UPDATE does match its row, so
       -- upgrade_release's return value says True either way.
       AND r.body_html IS NULL
       {where}
     ORDER BY r.source, r.id
"""


def write_richtext(
    limit: int | None = None, sources: list | None = None, dry_run: bool = False
) -> None:
    """Store the structured form of every PDF attachment whose bytes we hold.

    PDFs only. .doc keeps the text route by decision - see conversion.py - so
    a Word row here is `skipped`, not converted.

    **A PDF that converts to nothing is reported and left alone**, as is a row
    the gate refuses: falling back to its text would put a pre-wrap blob in the
    corpus and lose the fact that the converter failed on that document. Both
    are listed at the end of the run, for a person to decide.

    The cursor is `body_html IS NULL` and no flag widens it: a converter change
    is a one-off, so widen the query by hand for that run, the way the schema is
    edited by hand.
    """
    with contextlib.closing(connection.connect()) as conn:
        rows = cursor_rows(conn, RICHTEXT_SQL, sources, limit)
        print(
            f"[richtext] {len(rows)} attachment rows with cached bytes and no markup"
            f"{' (dry run)' if dry_run else ''}",
            flush=True,
        )

        stats = Stats(total=len(rows))
        decisions, gained = [], 0
        for rid, source, url, old_body, content, origin_url in rows:
            kind = conversion.kind_of(content)
            if kind != "pdf":
                stats.skipped()
                continue
            text, _ = conversion.plain_text(content)
            body, body_html, _ = conversion.to_richtext(content)
            if not body_html:
                decisions.append((rid, url, "converter returned nothing"))
                stats.dead()
                continue
            ok, why = gate.same_words(
                text, body, conversion.rotated_text(content), body_html.count("<li>")
            )
            if not ok:
                decisions.append((rid, url, why))
                stats.skipped()
                continue
            if not dry_run:
                # `grade="full"` because this write can be the teaser-to-article
                # replacement: the cursor is `body_html IS NULL`, which says
                # nothing about `body`, and the gate above compares the two
                # conversions of these bytes rather than what is stored.
                storage.upgrade_release(
                    conn,
                    url,
                    body=body,
                    body_html=body_html,
                    grade="full",
                    origin_url=origin_url,
                )
            gained += 1
            stats.upgraded()

        stats.summary()
        print(f"  {gained} rows now carry real paragraphs instead of preformatted text")
        if decisions:
            print(f"\nDO DECYZJI: {len(decisions)} wierszy - nic nie zapisano")
            for rid, url, why in decisions:
                print(f"  #{rid:5} {why:34} {url}")
        else:
            print(
                "  nothing needed a decision: every PDF converted and passed the gate"
            )


# Rows whose attachment bytes are in page_cache under NO name - not their own
# capture, and not a mirror's. Everything else can be worked offline, so these
# are the only attachment rows a crawl can still add anything to.
def no_own_bytes_rows(conn) -> list[tuple[str, str]]:
    """(url, body) for attachment rows we hold bytes for, but not under their own
    address - the copy in page_cache was fetched from a mirror domain.

    Worth a crawl of its own rather than reading the sibling's bytes at write
    time: the row would otherwise claim its text came from a capture of another
    domain's url, which is true and unrepresentable. `domain_variants` puts the
    row's own url first, so this pass stores the right capture wherever
    archive.org has one, and the mirror stays the answer where it has
    none. Rows with no cached bytes anywhere are excluded - confirmed never
    archived, `docs/adr/numbers.md`.
    """
    own, anywhere = set(), set()
    for (key,) in conn.execute(
        "SELECT url FROM page_cache WHERE lower(url) LIKE '%.pdf' "
        "OR lower(url) LIKE '%.doc'"
    ):
        if "id_/" not in key:
            continue
        page = key.split("id_/", 1)[1]
        own.add(page.lower())
        anywhere.add(conversion.attachment_name(page))
    return [
        (url, body)
        for url, body in conn.execute(
            "SELECT url, COALESCE(body, '') FROM releases "
            "WHERE lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc' ORDER BY id"
        )
        if url.lower() not in own and conversion.attachment_name(url) in anywhere
    ]


def missing_bytes_rows(conn) -> list[tuple[str, str]]:
    """(url, body) for attachment rows with no cached bytes under any mirror name.

    Done in Python, not SQL: SQLite has no basename(), and the name rule is
    `conversion.attachment_name`, the one every pass compares by.
    """
    cached = set()
    for (key,) in conn.execute(
        "SELECT url FROM page_cache WHERE lower(url) LIKE '%.pdf' "
        "OR lower(url) LIKE '%.doc'"
    ):
        if "id_/" in key:
            cached.add(conversion.attachment_name(key))
    return [
        (url, body)
        for url, body in conn.execute(
            "SELECT url, COALESCE(body, '') FROM releases "
            "WHERE lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc' ORDER BY id"
        )
        if conversion.attachment_name(url) not in cached
    ]


def catch_up_network_source(
    source: str,
    limit: int | None = None,
    only_short: bool = False,
    rows: list | None = None,
    in_db: bool = True,
) -> None:
    """One source's attachment rows, or an explicit `rows` list spanning several.

    `in_db=False` says the label is not a source tag - the --missing-bytes run
    passes "missing-bytes", and Stats would otherwise look that up with
    source_total and print "Total in DB: 0", which reads as the run having found
    an empty source rather than as the label not being one.
    """
    with (
        contextlib.closing(connection.connect()) as conn,
        requests.Session() as session,
    ):
        rows = (
            rows
            if rows is not None
            else conn.execute(ATTACHMENT_SQL, (source,)).fetchall()
        )
        if only_short:
            full = [r for r in rows if len(r[1] or "") >= RECOVERED_LENGTH]
            rows = [r for r in rows if len(r[1] or "") < RECOVERED_LENGTH]
            # Say what was dropped, or the count reads as if rows had been lost.
            print(
                f"[{source}] --only-short: skipping {len(full)} rows that already "
                f"hold >{RECOVERED_LENGTH} characters",
                flush=True,
            )
        if limit:
            rows = rows[:limit]
        print(f"[{source}] {len(rows)} attachment-linked rows to attempt", flush=True)

        stats = Stats(source, total=len(rows))

        for url, old_body in rows:
            text = ""
            origin_url = None
            # A network error - or a listing CDX truncated before every capture
            # was tried - is not evidence that the attachment was never
            # archived, so it is tracked separately rather than reported as a
            # dead end.
            uncertain = False

            for candidate in domain_variants(url):
                # Scores every capture the same way the write below does - see
                # attachment_score and this module's docstring.
                content, found_ts, confirmed = archive.fetch_best_matching_snapshot(
                    conn,
                    session,
                    candidate,
                    attachment_score,
                    timeout=30,
                )

                if content is None:
                    if not confirmed:
                        uncertain = True
                    continue

                origin_url = (
                    address.snapshot_url(found_ts, candidate) if found_ts else None
                )
                text, kind = conversion.plain_text(content)
                if not text:
                    # is_attachment already confirmed a real pdf/doc, so a failure
                    # here is the extractor failing on it - a parser gap worth
                    # another try, not a verified absence.
                    print(f"\n  {kind} extractor produced no text for {candidate}")
                    uncertain = True
                else:
                    break

            if not text:
                stats.uncertain() if uncertain else stats.dead()
                continue
            if len(text) <= len(old_body or ""):
                stats.skipped()
                continue

            # The address this text came out of, in the same transaction as the
            # body it describes - for a row whose own url was never archived that
            # is a sibling domain, which `releases.url` cannot say. None needs no
            # guard: upgrade_release writes no entry for one. `grade="full"`
            # because the length comparison above is the teaser-to-article
            # replacement itself.
            storage.upgrade_release(
                conn, url, body=text, grade="full", origin_url=origin_url
            )
            stats.upgraded()

        stats.summary(conn if in_db else None)


def catch_up_network(
    limit: int | None = None,
    sources: list | None = None,
    only_short: bool = False,
    missing_bytes: bool = False,
    no_own_bytes: bool = False,
) -> None:
    if no_own_bytes:
        conn = connection.connect_ro()
        rows = no_own_bytes_rows(conn)
        conn.close()
        if limit:
            rows = rows[:limit]
        print(
            f"[no-own-bytes] {len(rows)} attachment rows whose bytes we only hold "
            f"under a mirror domain",
            flush=True,
        )
        catch_up_network_source("no-own-bytes", rows=rows, in_db=False)
        return
    if missing_bytes:
        conn = connection.connect_ro()
        rows = missing_bytes_rows(conn)
        conn.close()
        if limit:
            rows = rows[:limit]
        print(
            f"[missing-bytes] {len(rows)} attachment rows have no cached bytes "
            f"under any mirror name - the only ones a crawl can still help",
            flush=True,
        )
        catch_up_network_source("missing-bytes", rows=rows, in_db=False)
        return
    for source in sources or SOURCES:
        catch_up_network_source(source, limit=limit, only_short=only_short)


def catch_up(
    conn, sources: list, *, network: bool = False, limit: int | None = None
) -> None:
    """Phase 2 for attachment rows of `sources`. Offline work first, always.

    Offline: re-extract the bytes page_cache already holds (layout included),
    then convert every cached PDF to our HTML subset. Both are gated, both are
    idempotent, and a rerun right after one reports all dots.

    `network=True` adds the crawl: for the rows still short, walk the captures of
    this url and its mirror-domain siblings. Opt-in - see the module docstring.
    """
    # These three open their own connection, so the caller's has to be settled
    # first: two writers on one SQLite file is fine sequentially, a lock error
    # otherwise.
    conn.commit()
    reextract_from_cache(limit=limit, sources=sources)
    write_richtext(limit=limit, sources=sources)
    if network:
        catch_up_network(limit=limit, sources=sources, only_short=True)
