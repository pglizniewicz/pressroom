#!/usr/bin/env python3
"""One-time repair: record which archive.org capture a row's body really came
from, for the rows where that is not a capture of the row's own url.

The report that started this was #4414 (`terratec_new_de`). Its capture link
led nowhere, and the reason was not a lost capture: `detail_id`
20111011173713 names
`web/20111011173713/http://www.terratec.net/de/unternehmen/presse/presse.html`
- the *listing* capture whose 22 entries include that release, holding its full
text, character for character what the row stores. CDX has no capture of the
article's own url, ever. So `serve.wayback_url()`, which builds
`web/<detail_id>/<row url>`, was pointing at a page that never existed, while
the page that does exist was one join away.

That is the general shape: a scraper that reads a release out of a listing
capture stores the listing's timestamp on the row, because that is the honest
answer to "which capture is this text from" - but a timestamp alone cannot say
*which page* it belongs to, and every reader since has assumed "this row's own
url". 541 rows corpus-wide carry a timestamp whose own capture is not in
page_cache; 433 of them share their timestamp with some capture that is.

How the match is made, and why it is not a guess:

- candidates are only the cached captures carrying the *same timestamp*, so
  the row's own `detail_id` still does the identifying. This never searches the
  archive for a plausible-looking page.
- the body is then looked for in the candidate's raw bytes: up to 8 ASCII-only
  30-character slices, whitespace-normalised, decoded latin-1 so every byte
  round-trips regardless of the page's real charset. ASCII-only is what makes
  it encoding-independent, and it is why the threshold is 0.6 rather than 1.0:
  a slice that happens to straddle an entity (`&nbsp;`, `&#8211;`) or a tag
  will miss even on the right page. #4414 scores 0.88 on its listing capture
  and 0 on everything else.
- a tie is refused. Measured on 2026-08-22 there were none: of 541 rows, 158
  resolved (weakest match 0.625), 143 had no cached capture under that
  timestamp to test against, and for 240 the body is in none of the captures
  that do share it - those keep the timestamp and no recorded page. All 14
  sources' resolved links were spot-checked against archive.org: HTTP 200
  every time.

Writes to `body_origin` only - never to `releases`. It runs in two steps:

1. **every row whose detail_id is a capture timestamp** gets an entry for the
   capture of its *own* url, with `matched` NULL - derived, nothing to prove.
   This is what makes the table the single answer to "is there an archive link
   for this row", so `serve.py` never has to inspect a detail_id again.
2. **the rows whose own capture is not in page_cache** are then measured as
   below, and a resolved one overwrites its step-1 entry with the capture that
   really holds the text, plus the evidence.

A row that cannot be resolved in step 2 keeps its step-1 entry, i.e. exactly
the link the old timestamp-shaped derivation produced - honest, because the row
does claim its text came from that capture of its own page and we could not
show otherwise.

**Re-run this after a crawl.** Nothing else fills the table, so new Wayback
rows have no archive link in the browser until it does; step 1 is pure SQL over
`releases` and costs milliseconds.

Two markers carry a narrower meaning than usual here, because this run makes no
requests: `.` is "no cached capture under that timestamp, or too little ASCII
text to test with" - open again once the listing capture is fetched - and `d`
is "tested against every cached capture with that timestamp and the body is in
none of them". Neither is a statement about what archive.org holds.

Usage:
  python repair_capture_provenance.py --dry-run
  python repair_capture_provenance.py
  python repair_capture_provenance.py --source terratec_new_de
"""

import argparse
import collections
import re

import attachments
import db
import wayback
from progress import Stats
from scrape_midiman_pressdb import is_html_detail
from backfill_midiman_attachments import domain_variants

TS_RE = re.compile(r"^\d{14}$")
CAPTURE_TS_RE = re.compile(r"/web/(\d{14})id_/")

# A candidate must hold this fraction of the body's probes. See the docstring:
# not 1.0 because an ASCII slice can straddle an entity or a tag on the very
# page the text came from.
THRESHOLD = 0.6
PROBE_LEN = 30
PROBE_COUNT = 8


def probes(body: str) -> list:
    """Up to PROBE_COUNT ASCII-only slices of the body, spread through it."""
    text = " ".join((body or "").split())
    runs = re.findall(r"[ -~]{%d,}" % PROBE_LEN, text)
    return [r[:PROBE_LEN] for r in runs][:PROBE_COUNT]


def capture_index(conn) -> dict:
    """timestamp -> [capture url], over page_cache. Keys only: this reads no
    blobs, so it stays cheap on a 500 MB cache."""
    index = collections.defaultdict(list)
    for (url,) in conn.execute("SELECT url FROM page_cache"):
        m = CAPTURE_TS_RE.search(url)
        if m:
            index[m.group(1)].append(url)
    return index


def normalised(conn, capture_url: str) -> str:
    row = conn.execute("SELECT content FROM page_cache WHERE url = ?",
                       (capture_url,)).fetchone()
    if row is None:
        return ""
    return " ".join(row[0].decode("latin-1", "replace").split())


def page_of(capture_url: str) -> str:
    """The page a capture url is a capture of - for this run's report only.
    db.capture_page_of does the split; this only supplies the fallback that
    keeps the report readable when an address carries no `id_/` marker."""
    return db.capture_page_of(capture_url) or capture_url


def attachment_captures(conn) -> dict:
    """url -> the page_cache key holding that .pdf/.doc attachment's bytes,
    including the copy served from a sibling domain of the same scraper.

    An attachment row needs this instead of a derived address, and the first
    cut of this script got it wrong twice over. Its `body` came out of
    pdftotext/antiword, not out of any HTML page, and its `detail_id` is the
    *listing* capture's timestamp - so deriving `web/<that ts>id_/<the .pdf>`
    names a capture nobody has seen: measured, 117 of 381 attachment rows
    pointed at a timestamp that demonstrably differs from the capture we hold
    (#4990: 20030212170800 recorded against 20030701151621 actually fetched).
    The capture we downloaded is in page_cache under its real key, which is
    evidence rather than derivation, so that is what gets recorded - and where
    those bytes are absent the row gets no entry at all.
    """
    by_page = {}
    for key, head in conn.execute(
            "SELECT url, substr(content, 1, 8) FROM page_cache "
            "WHERE lower(url) LIKE '%.pdf' OR lower(url) LIKE '%.doc'"):
        # Magic bytes, not the extension: 5 of these ".pdf" captures are the
        # original server's error page (`509 Bandwidth Limit Exceeded`, 380
        # bytes). Recording one as a row's origin is a false statement - the
        # row's teaser did not come from there - and it would put a link to an
        # error page in the browser under the row's own name. Caught by the
        # content check on 4 rows before this guard existed.
        if "id_/" in key and attachments.is_attachment(bytes(head)):
            by_page.setdefault(key.split("id_/", 1)[1].lower(), key)

    out = {}
    for (url,) in conn.execute(
            "SELECT url FROM releases WHERE lower(url) LIKE '%.pdf' "
            "OR lower(url) LIKE '%.doc'"):
        # The row's own capture first, then the same path on a sibling domain.
        # `domain_variants` and never the file name: matching on the basename
        # paired 12 rows with a *different release* that happened to share one
        # (#5343 holds "M-Audio Ships ProSessions Sound and Loop Libraries",
        # the same-named file in another directory is "M-Audio Announces
        # ProSessions Sound Library"). With the path, 119 of 119 matched the
        # stored text character for character.
        for candidate in domain_variants(url):
            key = by_page.get(candidate.lower())
            if key:
                out[url] = key
                break
    return out


def record_own_captures(conn, source: str = None, dry_run: bool = False) -> tuple:
    """Step 1: an entry per row whose body came from a capture of its own url.

    Only fills gaps - an entry recovered by measurement is never overwritten
    with a derived one, which is what keeps this safe to re-run. Attachment rows
    take their real page_cache key (see attachment_captures) and are otherwise
    left without an entry; every other row's address is derived from its own
    detail_id, where there is nothing to prove.
    """
    sql = "SELECT url, detail_id FROM releases WHERE detail_id IS NOT NULL"
    params = ()
    if source:
        sql += " AND source = ?"
        params = (source,)
    have = {u for (u,) in conn.execute("SELECT url FROM body_origin")}
    attachments = attachment_captures(conn)
    derived, from_cache, no_evidence = [], [], 0
    for url, ts in conn.execute(sql, params):
        if not wayback.is_timestamp(ts) or url in have:
            continue
        if is_html_detail(url):
            derived.append((url, wayback.snapshot_url(ts, url)))
        elif url in attachments:
            from_cache.append((url, attachments[url]))
        else:
            no_evidence += 1
    if not dry_run:
        for url, cap in derived + from_cache:
            db.record_body_origin(conn, url, cap, commit=False)
        conn.commit()
    return len(derived), len(from_cache), no_evidence


def run(source: str = None, dry_run: bool = False, limit: int = None) -> None:
    conn = db.connect()
    derived, from_cache, no_evidence = record_own_captures(conn, source, dry_run)
    print(f"[capture-provenance] own captures: {derived} derived from detail_id, "
          f"{from_cache} attachments read off page_cache, {no_evidence} attachments "
          f"left without one{' (dry run)' if dry_run else ''}", flush=True)

    index = capture_index(conn)
    cached = {u for urls in index.values() for u in urls}

    sql = ("SELECT id, source, url, detail_id, COALESCE(body, '') FROM releases "
           "WHERE detail_id IS NOT NULL")
    params = ()
    if source:
        sql += " AND source = ?"
        params = (source,)
    # is_html_detail: a .pdf/.doc row's text came from pdftotext/antiword, so
    # matching it against an HTML page can only ever attribute it to the wrong
    # thing - and did, for 9 rows, one of them at a perfect 1.0 because the
    # listing happened to carry the same release text the PDF does.
    rows = [r for r in conn.execute(sql + " ORDER BY id", params)
            if TS_RE.match(str(r[3])) and is_html_detail(r[2])
            and f"https://web.archive.org/web/{r[3]}id_/{r[2]}" not in cached]
    if limit:
        rows = rows[:limit]

    print(f"[capture-provenance] {len(rows)} rows whose own capture is not in "
          f"page_cache{' (dry run)' if dry_run else ''}", flush=True)

    stats = Stats(total=len(rows))
    texts = {}
    per_page = collections.Counter()
    ties = []
    for rid, src, url, ts, body in rows:
        candidates = index.get(str(ts), [])
        marks = probes(body)
        if not candidates or len(marks) < 2:
            # Nothing to test against, or too little ASCII text to test with.
            # Both are open to a later run once the listing capture is fetched.
            stats.skipped()
            continue
        scored = []
        for cap in candidates:
            if cap not in texts:
                texts[cap] = normalised(conn, cap)
            hit = sum(1 for m in marks if m in texts[cap])
            scored.append((hit / len(marks), cap))
        scored.sort(reverse=True)
        if scored[0][0] < THRESHOLD:
            stats.dead()
            continue
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            ties.append((rid, [c for s, c in scored if s == scored[0][0]]))
            stats.uncertain()
            continue
        _score, cap = scored[0]
        per_page[page_of(cap)] += 1
        if dry_run:
            stats.upgraded()
            continue
        db.record_body_origin(conn, url, cap)
        stats.upgraded()

    stats.summary()
    print("\nthe page each resolved timestamp really names:")
    for page, n in per_page.most_common():
        print(f"  {n:4}  {page}")
    if ties:
        print(f"\nties refused: {len(ties)}")
        for rid, caps in ties[:10]:
            print(f"  #{rid}: {', '.join(page_of(c) for c in caps)}")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", help="limit to one source tag")
    p.add_argument("--limit", type=int, help="stop after this many rows")
    p.add_argument("--dry-run", action="store_true", help="measure, write nothing")
    args = p.parse_args()
    run(args.source, args.dry_run, args.limit)
