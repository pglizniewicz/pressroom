"""Scraper for TerraTec's dead press room (terratec.net/press/pressemit/) ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots since
the live site no longer exists.
"""


import re
import time

import requests
from bs4 import BeautifulSoup

from pressroom.capture.control.politeness import SLEEP
from pressroom.release.control.storage import already_stored
from pressroom.text.control.dating import iso_date
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Detail

PREFIX = "http://www.terratec.net:80/press/pressemit/"
SOURCE = "terratec"

DATE_RE = re.compile(r"Press Release,\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", re.IGNORECASE)

# The dateline that sits immediately above the headline, in all three languages
# this one hand-built site was published in: terratec.net English, its
# /press/*_fr.htm French pages, and terratec.de German (which
# presse.py imports find_headline for). Used to
# locate the headline, never the date - DATE_RE and DATE_RE_DE own that.
MARKER_RE = re.compile(r"press\s*release|communiqu\w*\s+de\s+presse|presseinfo", re.I)

_HEADINGS = ("h1", "h2", "h3", "h4")

# What ends a bare-text headline. Deliberately not "any block": <tr>/<td> are
# the container being walked into, so stopping on those would end the walk
# before any text. Stopping on <p> is the point of the rule - a cell that opens
# with a paragraph has no headline, and descending into it would title the row
# with the release's first sentence instead.
_HEADLINE_ENDS = {"p", "h1", "h2", "h3", "h4", "table", "ul", "ol",
                  "div", "blockquote"}

# A bare-text headline longer than this is prose that slipped past the rule
# above, not a headline. The longest real one across both sources is 119
# characters.
MAX_HEADLINE = 250


def find_headline(soup) -> str:
    """The release's headline, for the terratec.net/terratec.de page family.

    Called only when the caller's own bold-tag rule came up empty, so it can
    never change a title that already parses - measured over every cached
    capture of both sources: 193 identical, 8 filled, 0 changed.

    Three shapes, because the site was hand-authored over five years and the
    headline is not marked up the same way in all of it:

      1. the first non-empty bold after the dateline that is not itself a
         dateline. Both halves matter. Taking `bold_tags[i + 1]` blindly stored
         "" whenever a capture put an empty <b> between the two (3 rows); and on
         the French pages the very next bold is the "Communiqué de Presse"
         download-link label, which would otherwise become the title of all
         four of them.
      2. the first h1-h4 in the <tr> after the dateline's <tr>. The 2000-era
         pages put the headline in a heading and never bolded it.
      3. that same <tr>'s leading text, before its first paragraph. The
         1998-era pages leave the headline as a bare text node after a <br>.

    "" when none of them finds anything: five captures under these two sources
    are 290-byte placeholder pages carrying no article at all, and one French
    release opens straight into a <p> with no headline of any kind - which
    shape 3 refuses on purpose rather than titling the row with its lead
    sentence.
    """
    bolds = soup.find_all(["b", "strong"])
    marker = None
    for i, tag in enumerate(bolds):
        if MARKER_RE.search(tag.get_text(" ", strip=True)):
            marker = tag
            for nxt in bolds[i + 1:]:
                text = " ".join(nxt.get_text(strip=True).split())
                if text and not MARKER_RE.search(text):
                    return text
            break
    if marker is None:
        return ""

    row = marker.find_parent("tr")
    box = row.find_next_sibling("tr") if row is not None else None
    if box is None:
        return ""

    for heading in box.find_all(_HEADINGS):
        text = " ".join(heading.get_text(strip=True).split())
        if text:
            return text

    # Blank strings are skipped rather than collected: the <tr>'s own
    # indentation is its first descendant, and counting it as content ended the
    # walk on the <td> that follows before any headline had been seen.
    parts = []
    for node in box.descendants:
        if isinstance(node, str):
            if node.strip():
                parts.append(node)
        elif node.name in _HEADLINE_ENDS:
            break
    text = " ".join("".join(parts).split())
    return text if len(text) <= MAX_HEADLINE else ""


def is_html_page(original_url: str) -> bool:
    return original_url.lower().split("?", 1)[0].endswith((".htm", ".html"))


def parse_snapshot(content: bytes) -> Detail:
    # from_encoding, not decode_html: these 2002 pages declare no charset at
    # all and are wholly pre-UTF-8, so the bytes are cp1252 - and in prose full
    # of German accents two adjacent high bytes can coincidentally form a valid
    # UTF-8 sequence that decode_html would honour. Left to sniff, bs4 read
    # them as ISO-8859-1 and stored the cp1252 punctuation range as C1 control
    # characters (20 rows, since repaired; the repair is in the write path now).
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    text = soup.get_text(" ", strip=True)
    # Body from the DOM, date from the flat text. These pages are one big
    # layout table, and the article's table is the one carrying the most text -
    # see richtext.densest for why that beats a width= selector here. The flat
    # text stays for DATE_RE, which scans the whole page including the
    # header where the date actually sits.
    body, body_html = richtext.extract(richtext.densest(soup, "table", border="0"))
    if not body:
        # No layout table, or one with nothing in it: a handful of these
        # captures are 290-byte "page moved" stubs. Fall back to the flat text
        # rather than to nothing - an empty body_html means "not converted",
        # an empty body would mean the row was wiped.
        body, body_html = text, None

    date = ""
    m = DATE_RE.search(text)
    if m:
        date = iso_date(m.group(1), dayfirst=True)

    title = ""
    bold_tags = soup.find_all(["b", "strong"])
    for i, tag in enumerate(bold_tags):
        if "press release" in tag.get_text(strip=True).lower():
            if i + 1 < len(bold_tags):
                title = bold_tags[i + 1].get_text(strip=True)
            break
    if not title:
        title = find_headline(soup)

    return {"title": title, "date": date, "body": body, "body_html": body_html}


def scrape(limit: int = None, catch: dict = None) -> None:
    conn = connection.connect()
    session = requests.Session()

    print(f"[{SOURCE}] Listing archived pages under {PREFIX}", flush=True)
    snapshots = [] if catch_up.no_crawl(catch) else [
        s for s in archive.list_snapshots_or_exit(PREFIX) if is_html_page(s["original"])]
    if limit:
        snapshots = snapshots[:limit]
    print(f"[{SOURCE}] {len(snapshots)} candidate press pages", flush=True)

    stats = Stats(SOURCE)

    for entry in snapshots:
        url = entry["original"]
        if already_stored(conn, url):
            stats.skipped()
            continue

        try:
            found = archive.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            stats.uncertain()
            continue
        if not found:
            stats.dead()
            continue
        snapshot_url, timestamp = found

        try:
            content = archive.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            stats.uncertain()
            continue

        if storage.store_release(conn, SOURCE, url, title=parsed["title"],
                            date=parsed["date"], body=parsed["body"],
                            body_html=parsed["body_html"], detail_id=timestamp):
            stats.added()

    stats.summary(conn)
    catch_up.run(conn, SOURCE, catch, parser=parse_snapshot, session=session)
    conn.close()
