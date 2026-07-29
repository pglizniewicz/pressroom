#!/usr/bin/env python3
"""Backfill full press-release text for midiman_com_pressdb/midiman_net_pressdb
rows whose `url` is a PDF - scrape_midiman_pressdb.py only stores the short
listing-page teaser for these, since the full release lives in an external
PDF it doesn't fetch. Investigation confirmed these PDFs are genuine,
embedded-font, Word-to-Distiller exports (not scans) containing the full
release - headline, dateline, multiple paragraphs, quotes, sometimes a spec
sheet, an "About" boilerplate, and a contact footer - 6x-96x longer than the
stored teaser. Worth recovering.

Unlike every other backfill_*.py in this repo (which INSERT additional rows
under a new/different URL), this one UPDATEs existing rows' `body` in place:
the fuller content lives at the exact same URL already stored as `url`, not
a differently-addressed sibling page, so there's nothing new to key an
INSERT on. A `len(new) <= len(old)` guard prevents ever clobbering a good
teaser with a failed/short extraction.

Text extraction shells out to `pdftotext -layout` (poppler-utils, already
installed in this environment) rather than adding a pdfminer.six import -
one already-present system binary beats a new Python dependency in a repo
that declares none, and default-mode pdftotext was seen to silently drop
real hyphens at line-wrap boundaries ("rock-solid" -> "rocksolid"), which
-layout avoids.

Some PDFs are archived under only one of the two mirror domains (e.g. a
midiman.com URL 404s but the identical midiman.net copy of the same release
is archived, or vice versa) - domain_variants() tries the sibling domain
before giving up.

Known rough edge, not addressed here: 2-page releases repeat a running
header/footer (nav boilerplate, "Press Release", international contact
block) that ends up interleaved mid-body in naive whole-document text
extraction. Left as-is rather than guessing a general stripping rule from
only a handful of samples.

Out of scope: `promni.pdf`/`MORE5.pdf` are from the unrelated, earlier
scrape_midiman.py (2001 static pages, different source tags) and were
re-confirmed to have zero Wayback captures anywhere - nothing to backfill.

Usage:
  python backfill_midiman_pressdb_pdfs.py             # both domains
  python backfill_midiman_pressdb_pdfs.py --limit 5   # cap rows per domain (testing)
"""

import argparse
import re
import subprocess

import requests

import db
from progress import Stats
import wayback

SOURCES = ["midiman_com_pressdb", "midiman_net_pressdb"]


def domain_variants(url: str) -> list:
    variants = [url]
    if "midiman.com" in url:
        variants.append(url.replace("midiman.com", "midiman.net"))
    elif "midiman.net" in url:
        variants.append(url.replace("midiman.net", "midiman.com"))
    return variants


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-", "-"],
            input=pdf_bytes,
            capture_output=True,
            timeout=30,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    text = result.stdout.decode("utf-8", errors="replace")
    return re.sub(r"\s+", " ", text).strip()


def backfill_source(source: str, limit: int = None) -> None:
    conn = db.connect()
    session = requests.Session()

    rows = conn.execute(
        "SELECT url, body FROM releases WHERE source = ? AND url LIKE '%.pdf'", (source,)
    ).fetchall()
    if limit:
        rows = rows[:limit]
    print(f"[{source}] {len(rows)} PDF-linked rows to attempt", flush=True)

    stats = Stats(source)

    for url, old_body in rows:
        text = ""
        # A network error is not evidence that the PDF was never archived, so
        # track it separately - otherwise a run with no connectivity would
        # report every row as a confirmed dead end.
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
            snapshot_url, timestamp = found
            try:
                content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=30)
                text = extract_pdf_text(content)
            except Exception as e:
                print(f"\n  ERROR fetching {snapshot_url}: {e}")
                uncertain = True
                text = ""
            if text:
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


def backfill(limit: int = None) -> None:
    for source in SOURCES:
        backfill_source(source, limit=limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill full PDF text for Midiman/M-Audio pressdb.php releases")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N PDF-linked rows per domain")
    args = parser.parse_args()
    backfill(limit=args.limit)
