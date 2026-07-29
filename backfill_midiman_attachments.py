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

    if head.startswith(b"%PDF"):
        return _run(["pdftotext", "-layout", "-", "-"], content), "pdf"
    # OLE2 compound document - the real Word 97-2003 container.
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
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
        # A network error is not evidence that the attachment was never
        # archived, so track it separately - otherwise a run with no
        # connectivity would report every row as a confirmed dead end.
        uncertain = False

        for candidate in domain_variants(url):
            try:
                found = wayback.get_latest_working_snapshot(candidate)
            except Exception as e:
                print(f"\n  ERROR probing snapshots for {candidate}: {e}")
                uncertain = True
                continue
            if not found:
                continue
            snapshot_url, _timestamp = found
            try:
                content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=30)
            except Exception as e:
                print(f"\n  ERROR fetching {snapshot_url}: {e}")
                uncertain = True
                continue

            text, kind = extract_text(content)
            text = normalize(text)
            if not text:
                print(f"\n  no text from {kind}: {snapshot_url}")
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
