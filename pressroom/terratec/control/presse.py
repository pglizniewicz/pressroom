"""TerraTec's German press office, terratec.de/presse - and the gaps its
newer sibling left behind.

The primary pass for source="terratec_de", and the only one there has ever been.

It stamps **two** tags, which is ordinary here: the .de pages are entirely its
own territory, while the .net index pages only corroborate what
source="terratec" already holds, so those contribute *gaps* - an article the
other scraper never found gets stored under its tag, not a new one.

Cross-language duplicates between terratec_de and the English content are
deliberately NOT auto-deduped: matching across languages is unreliable, so
everything is stored with correct provenance and the "prefer the newer office's
English version" call is made by hand.

Discovery has two channels, and the second exists because the first is
incomplete: the archived index pages that link out to articles, plus the files
that exist in the full Wayback directory listing but were linked from no index
page at all (confirmed against a timemap diff). That url list is archaeology
worth keeping; the loop around it was a duplicate of this one.
"""

import re
import sqlite3

import requests
from bs4 import BeautifulSoup

from pressroom.release.control.storage import already_stored
from pressroom.text.control.dating import iso_date
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.scraping.control import discovery
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.terratec.control.pressemit import (
    find_headline,
    parse_snapshot as parse_net_snapshot,
)
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Detail, Entry


INDEX_PAGES = [
    (
        "http://www.terratec.de/presse/",
        "terratec_de",
        "https://web.archive.org/web/20030303193557id_/http://www.terratec.de/presse/pressearchiv.htm",
    ),
    (
        "http://www.terratec.de/presse/",
        "terratec_de",
        "https://web.archive.org/web/20030227043415id_/http://www.terratec.de/presse/pressemit.htm",
    ),
    (
        "http://www.terratec.net/press/",
        "terratec",
        "https://web.archive.org/web/20030222201120id_/http://www.terratec.net/press/pressarchive.htm",
    ),
    (
        "http://www.terratec.net/press/",
        "terratec",
        "https://web.archive.org/web/20030206053420id_/http://www.terratec.net/press/pressreleases.htm",
    ),
    (
        "http://www.terratec.net/press/",
        "terratec",
        "https://web.archive.org/web/20030405064946id_/http://www.terratec.net/press/pressreleases.htm",
    ),
]

# German-domain pages say "TerraTec PresseInfo vom DD.MM.YYYY", in addition to
# the "Presseinformation vom" style already seen on the very-early static page.
DATE_RE_DE = re.compile(
    r"Presse(?:Info|information) vom\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", re.IGNORECASE
)


# Files that exist in the full Wayback directory listing for
# terratec.de/presse/pressemit/ but were linked from none of the index pages
# above - found by a timemap diff against the database, which is not a
# measurement anyone will repeat. `already_stored` gates them, so they cost one
# cheap query each on every later run.
DIRECTORY_URLS = [
    "http://www.terratec.de:80/presse/pressemit/5800wasser.htm",
    "http://www.terratec.de:80/presse/pressemit/ad2netag.htm",
    "http://www.terratec.de:80/presse/pressemit/Cameo_200_DV.htm",
    "http://www.terratec.de:80/presse/pressemit/cameo_grabster.htm",
    "http://www.terratec.de:80/presse/pressemit/car4000.htm",
    "http://www.terratec.de:80/presse/pressemit/car_4000.htm",
    "http://www.terratec.de:80/presse/pressemit/Cinergy_400_TV.htm",
    "http://www.terratec.de:80/presse/pressemit/DR_Box_1.htm",
    "http://www.terratec.de:80/presse/pressemit/drbox1.htm",
    "http://www.terratec.de:80/presse/pressemit/DRBox1_2.htm",
    "http://www.terratec.de:80/presse/pressemit/ews96m.htm",
    "http://www.terratec.de:80/presse/pressemit/ews96m2.htm",
    "http://www.terratec.de:80/presse/pressemit/homearena_2_1.htm",
    "http://www.terratec.de:80/presse/pressemit/homearenastereo.htm",
    "http://www.terratec.de:80/presse/pressemit/ifa.htm",
    "http://www.terratec.de:80/presse/pressemit/MidiMaster_usb.htm",
    "http://www.terratec.de:80/presse/pressemit/mp3_cd_player.htm",
    "http://www.terratec.de:80/presse/pressemit/ppa-studio.htm",
    "http://www.terratec.de:80/presse/pressemit/sixpack.htm",
    "http://www.terratec.de:80/presse/pressemit/tt_besonic.htm",
    "http://www.terratec.de:80/presse/pressemit/tvalue-radio.htm",
    "http://www.terratec.de:80/presse/pressemit/TXR_335.htm",
    "http://www.terratec.de:80/presse/pressemit/TXR_665.htm",
]


def extract_links(content: bytes, base_url: str) -> list[Entry]:
    # cp1252 stated, never sniffed - see pressemit.py's parse_snapshot for why.
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    entries = []
    for row in soup.select("tr"):
        a = row.find("a", href=True)
        if not a or not a["href"].lower().startswith("pressemit/"):
            continue
        tds = row.find_all("td")
        date_str = tds[-1].get_text(strip=True) if len(tds) >= 2 else ""
        date = iso_date(date_str, dayfirst=True)
        entries.append(
            {
                "title": a.get_text(" ", strip=True),
                "date": date,
                "url": base_url + a["href"],
            }
        )
    return entries


def parse_de_snapshot(content: bytes) -> Detail:
    # cp1252 stated, never sniffed - see pressemit.py's parse_snapshot for why.
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    text = soup.get_text(" ", strip=True)
    # Body from the DOM, date from the flat text. These pages are one big
    # layout table, and the article's table is the one carrying the most text -
    # see richtext.densest for why that beats a width= selector here. The flat
    # text stays for DATE_RE_DE, which scans the whole page including the
    # header where the date actually sits.
    body, body_html = richtext.extract(richtext.densest(soup, "table", border="0"))
    if not body:
        # No layout table, or one with nothing in it: a handful of these
        # captures are 290-byte "page moved" stubs. Fall back to the flat text
        # rather than to nothing - an empty body_html means "not converted",
        # an empty body would mean the row was wiped.
        body, body_html = text, None

    date = ""
    m = DATE_RE_DE.search(text)
    if m:
        date = iso_date(m.group(1), dayfirst=True)

    title = ""
    bold_tags = soup.find_all(["b", "strong"])
    for i, tag in enumerate(bold_tags):
        t = tag.get_text(strip=True).lower()
        if "presseinfo" in t or "press release" in t:
            if i + 1 < len(bold_tags):
                title = bold_tags[i + 1].get_text(strip=True)
            break
    # terratec.de is the same hand-built template as terratec.net, so the same
    # three headline shapes turn up here - see scrape_terratec.find_headline.
    # Fallback only: it runs when the rule above finds nothing, which is the
    # only reason adding it changes no title that already parsed.
    if not title:
        title = find_headline(soup)

    return {"title": title, "date": date, "body": body, "body_html": body_html}


def already_have_net_filenames(conn: sqlite3.Connection) -> set[str]:
    return {
        url.rsplit("/", 1)[-1].lower() for url in storage.source_urls(conn, "terratec")
    }


def scrape(limit: int | None = None, catch: dict | None = None) -> None:
    conn = connection.connect()
    session = requests.Session()

    all_entries = {}  # url -> entry dict (title, date, url, source)
    # Both discovery channels, not just the fetching one: the directory list
    # below needs no network at all, so guarding only the index fetch still left
    # a full candidate list for the per-article probe loop to crawl. The guard
    # goes on the candidate list, which is where it goes in every other scraper.
    offline = catch_up.no_crawl(catch)

    for base_url, source, wayback_url in [] if offline else INDEX_PAGES:
        print(f"Fetching {wayback_url}", flush=True)
        # Losing one index page just means fewer candidates, so warn and carry
        # on rather than aborting the run.
        try:
            content = archive.fetch_snapshot(conn, session, wayback_url, timeout=20)
        except Exception as e:
            print(f"  ERROR fetching index page: {e}")
            continue
        for e in extract_links(content, base_url):
            e["source"] = source
            all_entries.setdefault(e["url"], e)

    # The second discovery channel: files no index page linked to.
    for url in [] if offline else DIRECTORY_URLS:
        all_entries.setdefault(
            url, {"url": url, "title": "", "date": "", "source": "terratec_de"}
        )

    net_have = already_have_net_filenames(conn)
    candidates = []
    for e in all_entries.values():
        if (
            e["source"] == "terratec"
            and e["url"].rsplit("/", 1)[-1].lower() in net_have
        ):
            continue
        candidates.append(e)

    # After both discovery channels and the .net dedup, so --limit caps what is
    # actually fetched rather than what was discovered - and before the count
    # below, so the printed number is the one the run will honour.
    if limit:
        candidates = candidates[:limit]

    print(
        f"\n{len(candidates)} candidate articles to fetch "
        f"({sum(1 for c in candidates if c['source'] == 'terratec_de')} terratec_de, "
        f"{sum(1 for c in candidates if c['source'] == 'terratec')} terratec)",
        flush=True,
    )

    stats = {}

    for e in candidates:
        s = stats.setdefault(e["source"], Stats(e["source"]))
        if already_stored(conn, e["url"]):
            s.skipped()
            continue

        parse_fn = (
            parse_net_snapshot if e["source"] == "terratec" else parse_de_snapshot
        )

        found = discovery.capture(conn, session, e["url"], parse_fn, stats=s)
        if found is None:
            continue

        # A confirmed absence is a stub here rather than `dead`: these rows came
        # off a listing that names them, so the title and date are real even
        # when no capture of the article ever existed.
        if found.timestamp is None:
            if storage.store_release(
                conn,
                e["source"],
                e["url"],
                title=e["title"],
                date=e["date"],
                grade="stub",
            ):
                s.stub()
            else:
                s.skipped()
            continue

        parsed = found.parsed
        if storage.store_release(
            conn,
            e["source"],
            e["url"],
            title=parsed["title"] or e["title"],
            date=parsed["date"] or e["date"],
            body=parsed["body"],
            body_html=parsed["body_html"],
            detail_id=found.timestamp,
            origin_url=found.origin_url if parsed["body"] else None,
        ):
            s.added()
        else:
            s.skipped()

    for s in stats.values():
        s.summary(conn)
    # Two tags, two parsers: the German pages and the .net gap rows are
    # different templates, and phase 2 has to reparse each with the one that
    # produced it.
    catch_up.run(conn, "terratec_de", catch, parser=parse_de_snapshot, session=session)
    catch_up.run(conn, "terratec", catch, parser=parse_net_snapshot, session=session)
    conn.close()
