#!/usr/bin/env python3
"""Backfill full press-release text for Midiman/M-Audio rows whose `url` points
at an external .pdf or .doc attachment rather than an HTML page.

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

Unlike every other backfill_*.py in this repo (which INSERT additional rows
under a new/different URL), this one UPDATEs existing rows' `body` in place:
the fuller content lives at the exact same URL already stored as `url`, not a
differently-addressed sibling page, so there's nothing new to key an INSERT on.
A `len(new) <= len(old)` guard prevents ever clobbering a good teaser with a
failed or truncated extraction.

Extraction shells out to system binaries rather than adding Python dependencies
to a repo that declares none - `pdftotext -layout` (poppler-utils) and
`antiword -m UTF-8.txt`, both already installed here:
  - -layout because default-mode pdftotext was seen to silently drop real
    hyphens at line-wrap boundaries ("rock-solid" -> "rocksolid").
  - -m UTF-8.txt because antiword otherwise maps its output through the
    locale's charset, which would reintroduce exactly the mojibake encoding.py
    exists to prevent.
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
wayback.fetch_first_matching_snapshot, which walks captures of each domain
candidate newest-to-oldest and keeps going until is_attachment() confirms one
is a real pdf/doc, up to WALKBACK_ATTEMPTS - not
wayback.get_latest_working_snapshot, which would stop at the first (newest)
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

Out of scope: `promni.pdf`/`MORE5.pdf` belong to the earlier scrape_midiman.py
era (2001 static pages, different source tags) and were re-confirmed to have
zero Wayback captures anywhere - nothing to backfill.

Usage:
  python backfill_midiman_attachments.py            # every source
  python backfill_midiman_attachments.py --limit 5   # cap rows per source (testing)
  python backfill_midiman_attachments.py --source midiman_net_media_pr
"""

import argparse
import re
import subprocess

import requests

from encoding import decode_html
import db
from progress import Stats
import wayback

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

# How many historical captures wayback.fetch_first_matching_snapshot will walk
# back through per domain candidate before giving up on a URL. Caps the cost of
# a URL that was never archived as a real file - most of them have 0 or 1
# HTTP-200 capture ever, so 6 comfortably covers the rare one with more without
# turning a single dead URL into dozens of requests.
WALKBACK_ATTEMPTS = 6

PDF_MAGIC = b"%PDF"
# OLE2 compound document header - the real Word 97-2003 container.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def is_attachment(content: bytes) -> bool:
    """True if `content`'s magic bytes are a type extract_text can pull real
    text from. Drives fetch_first_matching_snapshot's walk-back: a capture
    that is HTML (a soft-404) or anything else fails this, so the walk-back
    tries an older capture instead of settling for a wrong-typed page.

    Checked on the raw bytes, not by calling extract_text and looking at
    `kind` - that would run pdftotext/antiword just to classify, then run it
    again to actually extract.
    """
    head = content[:8]
    return head.startswith(PDF_MAGIC) or head.startswith(OLE2_MAGIC)


def domain_variants(url: str) -> list:
    """`url` first, then the same path on each of the other mirror domains."""
    for domain in MIRROR_DOMAINS:
        if domain in url:
            return [url] + [url.replace(domain, other)
                            for other in MIRROR_DOMAINS if other != domain]
    return [url]


def _run(cmd: list, data: bytes) -> str:
    """Feed `data` to an extractor on stdin; "" on any failure."""
    try:
        result = subprocess.run(cmd, input=data, capture_output=True, timeout=30)
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return decode_html(result.stdout)


def extract_text(content: bytes) -> tuple:
    """Extract an attachment's text as (text, kind).

    `kind` names what the bytes actually turned out to be, so a mislabeled or
    unsupported file can be reported instead of just yielding an empty body.
    """
    head = content[:8]

    if head.startswith(PDF_MAGIC):
        return _run(["pdftotext", "-layout", "-", "-"], content), "pdf"
    if head.startswith(OLE2_MAGIC):
        return _run(["antiword", "-m", "UTF-8.txt", "-"], content), "doc"
    if content.lstrip()[:5].lower() == b"{\\rtf":
        # antiword refuses RTF and there is no unrtf in this environment;
        # report it rather than hand-rolling a stripper on zero real samples.
        return "", "rtf (no extractor)"
    if content.lstrip()[:1] == b"<":
        # HTML under a .doc/.pdf URL is almost always the original server's
        # soft-404 (served as HTTP 200, so CDX's statuscode filter can't catch
        # it), not the release. Deliberately NOT returned as text: an error
        # page easily runs longer than the teaser, so the length guard below
        # would happily overwrite good data with junk. Reported instead, so a
        # real release hiding here would still be visible in the log.
        return "", "html (soft-404?)"

    return "", f"unrecognised ({bytes(head[:4])!r})"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def backfill_source(source: str, limit: int = None, only_short: bool = False) -> None:
    conn = db.connect()
    session = requests.Session()

    rows = conn.execute(ATTACHMENT_SQL, (source,)).fetchall()
    if only_short:
        full = [r for r in rows if len(r[1] or "") >= RECOVERED_LENGTH]
        rows = [r for r in rows if len(r[1] or "") < RECOVERED_LENGTH]
        # Say what was dropped: a bare "12 rows to attempt" after a 90-row run
        # would otherwise read as most of the work having vanished.
        print(f"[{source}] --only-short: skipping {len(full)} rows that already "
              f"hold >{RECOVERED_LENGTH} characters", flush=True)
    if limit:
        rows = rows[:limit]
    print(f"[{source}] {len(rows)} attachment-linked rows to attempt", flush=True)

    stats = Stats(source, total=len(rows))

    for url, old_body in rows:
        text = ""
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
            content, _ts, confirmed = wayback.fetch_first_matching_snapshot(
                conn, session, candidate, is_attachment,
                max_attempts=WALKBACK_ATTEMPTS, timeout=30)

            if content is None:
                if not confirmed:
                    uncertain = True
                continue

            text, kind = extract_text(content)
            text = normalize(text)
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

        db.upgrade_release(conn, url, body=text)
        stats.upgraded()

    stats.summary(conn)
    conn.close()


def backfill(limit: int = None, sources: list = None, only_short: bool = False) -> None:
    for source in sources or SOURCES:
        backfill_source(source, limit=limit, only_short=only_short)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill full .pdf/.doc attachment text for Midiman/M-Audio releases")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the first N attachment rows per source")
    parser.add_argument("--source", help="Only this source (default: all five)")
    parser.add_argument("--only-short", action="store_true",
                        help=f"Skip rows whose body already exceeds {RECOVERED_LENGTH} "
                             "characters - makes a retry pass minutes instead of hours")
    args = parser.parse_args()
    backfill(limit=args.limit, sources=[args.source] if args.source else None,
             only_short=args.only_short)
