#!/usr/bin/env python3
"""Phase 2 for the rows whose `url` points at a .pdf or .doc file, not a page.

The second contract beside catch_up.py, and it is a different one: there is no
HTML parser here (the extractor *is* the parser), the bytes are addressed through
body_origin or a mirror domain rather than through the row's own detail_id, and
the gates are strict_same_text for a re-extraction and same_words for the
text-to-markup conversion.

A library: `catch_up()` is what maudio/control/pressdb.py and
maudio/control/media_pr.py call, and everything free happens by default. The
**network crawl is opt-in** (`--attachments`), and that is the one honest
exception to "a plain rerun gets everything": nothing in the database records
"CDX has no capture of this url, ever", so a full pass costs ~3 hours to
rediscover 30 confirmed-dead rows and 211 whose own url was never archived. 219
logged attempts bought nothing the last two times.

Two CMS generations link out this way, and neither scraper follows the link, so
both store only the short listing-page teaser:
  - pressdb.php    (midiman_com_pressdb, midiman_net_pressdb) - .pdf only
  - media.media_pr (midiman_com/net/maudio_com_media_pr)      - .doc and .pdf
This was two would-be scripts with the same body; the only real differences
were the source list, the extension filter and the set of mirror domains, so it
is one script - same reasoning as merging the two TerraTec portal scrapers.

Investigation confirmed the PDFs are genuine embedded-font Word-to-Distiller
exports (not scans) carrying the whole release - headline, dateline, quotes,
sometimes a spec sheet, an "About" boilerplate and a contact footer - 6x-96x
longer than the stored teaser. The media_pr teasers are shorter still, averaging
~150 characters, so there is more to gain there than in pressdb.

This one UPDATEs existing rows' `body` in place rather than inserting: the
fuller content lives at the exact same URL already stored as `url`, not a
differently-addressed sibling page, so there is nothing new to key an INSERT on.
A `len(new) <= len(old)` guard prevents ever clobbering a good teaser with a
failed or truncated extraction.

Extraction shells out to system binaries rather than adding Python
dependencies - `pdftotext -layout` (poppler-utils) and
`antiword -m UTF-8.txt`, both already installed here:
  - -layout because default-mode pdftotext was seen to silently drop real
    hyphens at line-wrap boundaries ("rock-solid" -> "rocksolid").
  - -m UTF-8.txt because antiword otherwise maps its output through the
    locale's charset, which would reintroduce exactly the mojibake
    text/control/decoding.py exists to prevent.
  - antiword rather than catdoc because antiword validates its input and exits
    non-zero on anything that isn't a Word document, while catdoc echoes
    unparseable input straight back - which would store garbage as a body.

Dispatch is on the file's magic bytes, NOT its extension: CMS-era attachments
are routinely mislabeled, and a .doc that is really a PDF (or an HTML error
page saved under a .doc name) must not reach the wrong extractor. Anything
whose type isn't recognised is reported and counted `uncertain`, never `dead` -
we are holding the bytes, so it is a missing parser rather than a missing
capture, and a later run should retry it.

CDX's statuscode:200 filter is necessary but not sufficient: it proves
archive.org got an answer, not that the answer was the attachment. Two
confirmed shapes of that gap, both served as HTTP 200 and both recurring:
the origin server's own soft-404 (a genuine 380-byte Apache "509 Bandwidth
Limit Exceeded" page, for one 2003-era PDF path), and, for a path whose real
file is long gone, a modern redesigned m-audio.com answering 200 for it in
2024. Fetching is therefore done through
archive.fetch_first_matching_snapshot, which walks captures of each domain
candidate newest-to-oldest and keeps going until is_attachment() confirms one
is a real pdf/doc, up to WALKBACK_ATTEMPTS - not
archive.get_latest_working_snapshot, which would stop at the first (newest)
capture regardless of what it actually was.

In practice, among the URLs still unrecovered when this was added, essentially
every one had zero or exactly one HTTP-200 capture ever - so the walk-back's
concrete win here is turning those single-poisoned-capture URLs from
perpetually `uncertain` (indistinguishable from a network hiccup, retried
every run) into a correctly `dead` verdict once every capture has genuinely
been tried. The mechanism is general, not tuned to that outcome: a URL that
does have an older, unpoisoned capture is exactly what it is for.

Attachments are often archived under only one of the three mirror domains (a
midiman.com URL 404s while the identical midiman.net or m-audio.com copy of the
same release is archived), so domain_variants() tries the siblings before
giving up.

Known rough edge, not addressed here: 2-page releases repeat a running
header/footer (nav boilerplate, "Press Release", international contact block)
that ends up interleaved mid-body in naive whole-document text extraction.
Left as-is rather than guessing a general stripping rule from a handful of
samples.

Out of scope: `promni.pdf`/`MORE5.pdf` belong to the earlier
maudio/control/golive.py era (2001 static pages, different source tags) and
were re-confirmed to have
zero Wayback captures anywhere - nothing to backfill.

Called by the two scrapers that own these tags, never run on its own.
"""

import contextlib

import requests

from pressroom.capture.control import address
from pressroom.release.control import gate
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.reporting.entity.outcome import Stats

# _wordchars is the repo's single implementation of "compare two extractions of
# the same text": it drops indentation, line wrapping, bullets and the ordinals
# to_text() prepends. Imported rather than copied, private name and all.
# One implementation of "only whitespace may differ", shared with
# what is now catch_up.from_cache's strict route, rather than copied.
# What an attachment's bytes mean is conversion.py's concern; this file owns
# the crawl. extract_text/normalize/PDF_MAGIC/OLE2_MAGIC moved there when
# the calibration pass became a second caller for them.
from pressroom.attachment.control import conversion
from pressroom.capture.control import archive

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
# row longer than this has certainly already had its attachment extracted.
# Used by --only-short to keep a rerun cheap: nothing here records which rows
# were already done, and a full pass re-probes all ~380 over the network even
# though the bytes are cached, which costs hours to reach the handful of rows
# that a previous run left `uncertain`.
RECOVERED_LENGTH = 900

# How many historical captures archive.fetch_first_matching_snapshot will walk
# back through per domain candidate before giving up on a URL. Caps the cost of
# a URL that was never archived as a real file - most of them have 0 or 1
# HTTP-200 capture ever, so 6 comfortably covers the rare one with more without
# turning a single dead URL into dozens of requests.
WALKBACK_ATTEMPTS = 6

# Magic bytes, the extractors and the two levels of extraction live in
# conversion.py: the calibration pass needs the same code, and a second
# copy of "what these bytes are" is exactly the mirror-rule trap this repo
# keeps paying for. This file owns the crawl.


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


def reextract_from_cache(limit: int | None = None, sources: list | None = None) -> None:
    r"""Re-extract attachment text from bytes page_cache already holds. No network.

    This exists because normalize()'s layout fix never reached the corpus.
    Measured 2026-08-23: **380 of 381 attachment rows held text with not one
    newline in it** - the output of the old `re.sub(r"\s+", " ", text)`, which is
    precisely what that fix was written to stop doing. The rows predate it and
    nothing re-ran the extraction, so every spec sheet in this corpus was still
    a single run-on line, rendered through white-space: pre-wrap that had no
    layout left to show.

    140 rows had their attachment's bytes in page_cache, so their layout cost
    nothing to recover. Of the 241 that did not, **205 have the same file cached
    under a mirror domain** (midiman.com / midiman.net / m-audio.com served the
    same attachment) - reachable by widening this query, not by crawling.

    The gate is strict_same_text, not the length comparison the network path
    uses: here the *only* admissible change is whitespace, because the same
    extractor on the same bytes must produce the same characters. Measured over
    all 140 before writing anything: 140/140 identical modulo whitespace.

    That is also why this is the one write here that passes no `grade`. The
    gate admits nothing but a body that already *is* this extraction, character
    for character - a teaser fails it - and a row whose text does not change is
    `skipped` before the write. There is no verdict for this pass to change,
    and claiming one it did not establish is the same lie in the other
    direction. The two writes that can replace a teaser say `grade="full"`.
    """
    with contextlib.closing(connection.connect()) as conn:
        where = ""
        params = ()
        if sources:
            where = "AND r.source IN (%s)" % ",".join("?" * len(sources))
            params = tuple(sources)
        rows = conn.execute(
            CACHED_ATTACHMENT_SQL.format(where=where), params
        ).fetchall()
        if limit:
            rows = rows[:limit]
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
            # origin_url through the write rather than beside it: `upgrade_release`
            # records it inside its own `with conn:`, so the text and the entry are
            # one transaction. Here it restates the value the JOIN above just read
            # - which is exactly why this pair was the last one still split.
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


# Rows whose own attachment bytes are in page_cache, addressed through the
# capture body_origin recorded for them.
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

    **A PDF that converts to nothing is reported and left alone.** Falling back
    to its text would put a pre-wrap blob in the corpus and lose the fact that
    the converter failed on that document; the choice between "use the text for
    this one" and "fix the converter" belongs to a person reading it. Same for a
    row the gate refuses. Both are listed at the end of the run.

    The cursor is `body_html IS NULL`, and there is no flag that widens it -
    neither offline pass has one, because a converter change is a one-off and
    stays one: widen the query by hand for that run, the way the schema is
    edited by hand. What the cursor buys every other run is an honest `Stats`.
    """
    with contextlib.closing(connection.connect()) as conn:
        where = ""
        params = ()
        if sources:
            where = "AND r.source IN (%s)" % ",".join("?" * len(sources))
            params = tuple(sources)
        rows = conn.execute(RICHTEXT_SQL.format(where=where), params).fetchall()
        if limit:
            rows = rows[:limit]
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
                # conversions of these bytes to each other rather than to what
                # is stored. A row still holding its listing blurb is replaced
                # here, and a verdict left behind would offer it up again.
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
    domain's url, which is true and unrepresentable - `releases.url` is one
    address per row. `domain_variants` puts the row's own url first, so this pass
    stores the right capture wherever archive.org has one, and where it has
    none the mirror stays the only honest answer.

    Rows with no cached bytes anywhere are excluded: those 30 were probed twice
    and confirmed never archived (see CLAUDE.md), so re-crawling them buys the
    same nothing again.
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
        anywhere.add(page.rsplit("/", 1)[1].lower())
    return [
        (url, body)
        for url, body in conn.execute(
            "SELECT url, COALESCE(body, '') FROM releases "
            "WHERE lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc' ORDER BY id"
        )
        if url.lower() not in own and url.rsplit("/", 1)[1].lower() in anywhere
    ]


def missing_bytes_rows(conn) -> list[tuple[str, str]]:
    """(url, body) for attachment rows with no cached bytes under any mirror name.

    Done in Python, not SQL: SQLite has no basename(), the rtrim/replace trick
    that emulates one is unreadable, and this comparison has to match the one
    attachment/boundary/calibration.py and the richtext pass use.
    """
    cached = set()
    for (key,) in conn.execute(
        "SELECT url FROM page_cache WHERE lower(url) LIKE '%.pdf' "
        "OR lower(url) LIKE '%.doc'"
    ):
        if "id_/" in key:
            cached.add(key.rsplit("/", 1)[1].lower())
    return [
        (url, body)
        for url, body in conn.execute(
            "SELECT url, COALESCE(body, '') FROM releases "
            "WHERE lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc' ORDER BY id"
        )
        if url.rsplit("/", 1)[1].lower() not in cached
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
            # Say what was dropped: a bare "12 rows to attempt" after a 90-row run
            # would otherwise read as most of the work having vanished.
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
            # A network error - or a search capped before trying every capture -
            # is not evidence that the attachment was never archived, so track it
            # separately. Otherwise a run with no connectivity, or a URL with more
            # captures than WALKBACK_ATTEMPTS, would report a confirmed dead end.
            uncertain = False

            for candidate in domain_variants(url):
                # Walks newest-to-oldest through every archived capture of
                # `candidate`, not just the newest: CDX's statuscode:200 filter
                # only proves archive.org got an HTTP 200, not that it was the
                # attachment - a real "509 Bandwidth Limit Exceeded" page and a
                # 2024 capture of the modern site both turned up served as 200 for
                # a 2003-era attachment path. is_attachment rejects those and the
                # walk-back tries the next-older capture instead of giving up.
                content, found_ts, confirmed = archive.fetch_first_matching_snapshot(
                    conn,
                    session,
                    candidate,
                    conversion.is_attachment,
                    max_attempts=WALKBACK_ATTEMPTS,
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
                    # is_attachment already confirmed this is a real pdf/doc, so a
                    # failure here is the extractor choking on it (encrypted,
                    # corrupt), not a wrong-typed capture - still a parser gap
                    # worth another try, not a verified absence.
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

            # The address this text came out of, recorded now rather than inferred
            # later, and in the same transaction as the body it describes. For a
            # row whose own url was never archived this is a sibling domain of the
            # same scraper - true, and unrepresentable in `releases.url`, which is
            # exactly why the table exists. None needs no guard: upgrade_release
            # writes no entry for one. And the verdict beside it: the
            # `len(text) > len(old_body)` above is the teaser-to-article
            # replacement itself, so this is where `grade` stops being whatever
            # the listing scraper guessed.
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
    """Phase 2 for attachment rows of `sources`. Free work first, always.

    Offline: re-extract the bytes page_cache already holds (layout included),
    then convert every cached PDF to our HTML subset. Both are gated, both are
    idempotent, and a rerun right after one reports all dots.

    `network=True` adds the crawl: for the rows still short, walk the captures of
    this url and its mirror-domain siblings. Opt-in because the yield is
    measured at zero - see the module docstring.
    """
    # These three open their own connection (they always have), so the
    # caller's has to be settled first: two writers on one SQLite file is fine
    # sequentially and a lock error otherwise.
    conn.commit()
    reextract_from_cache(limit=limit, sources=sources)
    write_richtext(limit=limit, sources=sources)
    if network:
        catch_up_network(limit=limit, sources=sources, only_short=True)
