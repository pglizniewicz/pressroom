"""TerraTec's earliest press page (terratec.de, 1996-1997) -
a single hand-authored HTML page listing every release with anchor tags,
plus a short-lived English mirror covering just the earliest 5 entries.

Not a crawler like the other scrapers: these are two known-good Wayback URLs
given directly, so no timemap/sparkline lookup is involved.
"""

import re

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.dating import iso_date
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.text.control import richtext
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Entry

SOURCE = "terratec_early"

PAGES = [
    {
        "lang": "de",
        "base_url": "http://www.terratec.de/presse2.htm",
        "wayback_url": "https://web.archive.org/web/19980115084201id_/http://www.terratec.de/presse2.htm",
        "timestamp": "19980115084201",
        "date_marker": r"Presseinformation vom ([\d.]+):",
        "anchor_re": r'<A NAME="(p\d+)">',
        "anchor_fmt": "#{}",
    },
    {
        "lang": "en",
        "base_url": "http://www.terratec.de/e/presse.htm",
        "wayback_url": "https://web.archive.org/web/19961129051606id_/http://www.terratec.de:80/e/presse.htm",
        "timestamp": "19961129051606",
        "date_marker": r"Press-Release as of\s*([\d.]+):",
        "anchor_re": r'<A NAME="(\d+)">',
        "anchor_fmt": "#{}",
    },
]

# The longest a first paragraph may be and still be read as the headline. Every
# one of the 21 headlines on these two pages is 16-56 characters; the shortest
# opening paragraph of an actual release is 264. Nothing lands in between, so
# the cut is nowhere near either population.
MAX_TITLE = 200


def headline(body: str, date_marker: str) -> str:
    """The headline of one entry, out of the text extract() just produced.

    These two pages carry no headline markup at all - no heading tag, no bold,
    nothing to key on - so the entries were stored with no title for as long as
    this scraper has existed. What they do have is a rigid dateline, and the
    headline sits in one of exactly two places relative to it:

        Presseinformation vom 19.12.1997: TerraTec Electronic mit windiger Idee
        <the release>

        Presseinformation vom 24.11.1997:
        TerraTec: Mit Erfolg von Deutschland nach Asien
        <the release>

    2 entries use the first shape, 19 the second, and the English page ("Press-
    Release as of 27.9.96:") only the second. The length guard is what keeps the
    second shape from titling an entry with its opening paragraph on a page
    where the headline is missing.
    """
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    if not paras:
        return ""
    m = re.match(date_marker, paras[0])
    rest = paras[0][m.end() :].strip() if m else ""
    if not rest and len(paras) > 1:
        rest = paras[1].strip()
    if not rest or len(rest) > MAX_TITLE:
        return ""
    # A few headlines are punctuated as a lead-in ("CeBit Home 1996:"); the
    # colon belongs to the layout, not to the title.
    return " ".join(rest.split()).rstrip(":").strip()


def find_anchor_for(pos: int, anchors: list) -> str:
    best = None
    for apos, aname in anchors:
        if apos <= pos and pos - apos < 100:
            best = aname
    return best


def extract_entries(html: str, page: dict) -> list[Entry]:
    anchors = [(m.start(), m.group(1)) for m in re.finditer(page["anchor_re"], html)]
    dates = [(m.start(), m.group(1)) for m in re.finditer(page["date_marker"], html)]

    entries = []
    for i, (pos, date_str) in enumerate(dates):
        chunk_end = dates[i + 1][0] if i + 1 < len(dates) else len(html)
        chunk_html = html[pos:chunk_end]
        body, body_html = richtext.extract(BeautifulSoup(chunk_html, "html.parser"))

        date = iso_date(date_str, dayfirst=True)

        anchor = find_anchor_for(pos, anchors)
        url = page["base_url"] + (page["anchor_fmt"].format(anchor) if anchor else "")
        citation = page["wayback_url"].replace("id_", "") + (
            page["anchor_fmt"].format(anchor) if anchor else ""
        )

        entries.append(
            {
                "date_str": date_str,
                "date": date,
                "title": headline(body, page["date_marker"]),
                "body": body,
                "body_html": body_html,
                "url": url,
                "citation": citation,
                "lang": page["lang"],
            }
        )
    return entries


def scrape(catch: dict | None = None) -> None:
    conn = connection.connect()
    session = requests.Session()

    # Both listing captures are required for the cross-language dedup below, so
    # this crawl cannot be half-done - which is why the no-network flags skip
    # straight to phase 2 rather than running the loop over an empty list.
    if catch_up.no_crawl(catch):
        catch_up.run(conn, SOURCE, catch, collect=cached_entries, session=session)
        conn.close()
        return

    all_entries = []
    for page in PAGES:
        # Both pages are required: the cross-language dedup below compares the
        # German page against the English one, so a partial fetch can't be
        # salvaged - bail with a message rather than a traceback or, worse, an
        # IndexError further down.
        try:
            content = archive.fetch_snapshot(
                conn, session, page["wayback_url"], timeout=20
            )
        except Exception as e:
            raise SystemExit(
                f"Could not fetch {page['wayback_url']}: {e}\n"
                "Both the German and English page are needed for the "
                "cross-language dedup - try again later."
            )
        entries = extract_entries(content.decode("cp1252", errors="replace"), page)
        print(f"[{page['lang']}] {len(entries)} entries found", flush=True)
        all_entries.append((page, entries))

    de_page, de_entries = all_entries[0]
    en_page, en_entries = all_entries[1]

    # The English page duplicates the tail of the German page (translated) -
    # skip the German copy of these, keep the English one. Match by
    # normalized ISO date, not raw date_str (formats differ: "27.09.1996"
    # on the German page vs "27.9.96" on the English page for the same day).
    english_dates = {e["date"] for e in en_entries}

    skipped_de = 0
    new_count = 0

    for e in de_entries:
        if e["date"] in english_dates:
            skipped_de += 1
            continue
        if storage.store_release(
            conn,
            SOURCE,
            e["url"],
            title=e["title"],
            date=e["date"],
            body=e["body"],
            body_html=e["body_html"] or None,
            detail_id=de_page["timestamp"],
            commit=False,
        ):
            new_count += 1

    for e in en_entries:
        if storage.store_release(
            conn,
            SOURCE,
            e["url"],
            title=e["title"],
            date=e["date"],
            body=e["body"],
            body_html=e["body_html"] or None,
            detail_id=en_page["timestamp"],
            commit=False,
        ):
            new_count += 1

    conn.commit()
    total = storage.source_total(conn, SOURCE)
    print(
        f"\nInserted {new_count} rows ({skipped_de} German duplicates of English entries skipped). Total: {total}"
    )
    # Listing-only: every row is an anchor into one of two pages, so there is no
    # per-row capture to reparse and no parser to pass. This is also the only
    # upgrade path these rows have ever had - the crawl above can INSERT but
    # never improve a row it already stored.
    catch_up.run(conn, SOURCE, catch, collect=cached_entries, session=session)
    conn.close()


def cached_entries(conn) -> dict[str, Entry]:
    """(url -> entry) for terratec_early, for catch_up. Every row is an anchor
    (`#p20`) into one of two listing pages, so one capture yields many rows -
    which is also why only a url-keyed collector can separate them."""
    out = {}
    for page in PAGES:
        row = conn.execute(
            "SELECT content FROM page_cache WHERE url = ?", (page["wayback_url"],)
        ).fetchone()
        if row is None:
            continue
        html = row[0].decode("cp1252", errors="replace")
        for e in extract_entries(html, page):
            if e.get("body_html"):
                e["origin_url"] = page["wayback_url"]
                out[e["url"]] = e
    return out
