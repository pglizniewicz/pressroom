"""Scraper for MIDIMAN/M-Audio's German site (midiman.de) press archive ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots.

midiman.de never had any CMS - confirmed via a full-domain CDX scan (no
.php, no do=media.*, no cgi-bin, ever). It's static .htm from birth (1998)
to death (~2006). Two pages hold the whole press history:
  - oldpress.htm ("Aeltere Pressemitteilungen" - older releases archive)
  - pressemt.htm ("Presseraum" - current/rolling releases, links to
    oldpress.htm for older ones)
Both are full re-dumps of the whole history (confirmed: identical releases
appear verbatim across many captures of both pages), so - same idiom as
pressdb.py/media_pr.py - every historical capture of both is sampled via
archive.list_all_captures, and entries are deduped by (title, date), keeping
the longest body seen.

Each release lives in an HTML-comment-delimited block, either
"<!-- start -->...<!-- stop -->" or product-name-tagged ("<!-- Radium
start -->...<!-- Radium stop -->" - confirmed both forms coexist on the
same page). The overwhelming majority ("inline" blocks - verified: all 46
oldpress.htm blocks, 9/11 pressemt.htm blocks) contain the FULL release
text directly: <h4> (descriptive headline) + <h3> (short product name) +
a "Ort, DD.MM.YYYY" dateline paragraph + body <p> tags + an "Infos bei:"
contact footer (cut off, not stored) + arrow-bulleted links. Verified
against a live sample (tampa.htm) that the "weitere Produkt-Infos X" links
in this footer point to product marketing pages, NOT richer versions of
the press release - the inline block text already IS the complete release,
so those links are correctly left unfetched (out of scope, matches how
other product-page links are treated elsewhere in this project).

A minority ("linkout" blocks - seen only on pressemt.htm's newest entry so
far) are just a placeholder: a single big link to a separate page (e.g.
messe03.htm) plus a one-line teaser, with the real content living on that
linked page - verified live that messe03.htm is itself a genuine, richer
press writeup, not a product page. For these, this scraper reuses the same
fetch_detail()/stored_grade() contract already proven in
media_news.py/news_blog.py: a network hiccup never locks
in a permanent teaser-only row, only a confirmed dead end does, and a
teaser row is retried and upgraded on every future run until real content
is recovered.

Dates come in two formats depending on era: numeric "DD.MM.YYYY" (the
common case, ~35/46 on oldpress.htm) and, for the oldest 1998-1999 entries,
spelled-out German months ("15. Januar 1999") - both are extracted by
_extract_date(), numeric tried first (some blocks' trailing credit-stamp
footer repeats the date with a 2-digit year, so the numeric regex requires
an unambiguous 4-digit year to avoid ever preferring that over the real
dateline).

No `<a name>` anchors exist anywhere on either page (checked), so inline
releases have no natural per-release URL - a stable synthetic one is
generated from the slugified (title, date) key instead
(http://www.midiman.de/press/{slug}-{date}), independent of which of the
two mirror pages an entry was found on, so the same release found on both
oldpress.htm and pressemt.htm collapses to a single row rather than two.

Encoding is Windows-1252, undeclared (confirmed via raw byte inspection -
0x99 appearing as (TM) is only valid in cp1252, not Latin-1) - same
treatment as pressdb.py.
"""

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pressroom.release.control.storage import stored_grade
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraping.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.capture.control import archive
from pressroom.scraping.entity.parse import Detail, Entry

SOURCE = "midiman_de"

PAGES = [
    "http://www.midiman.de/oldpress.htm",
    "http://midiman.de/pressemt.htm",
]

BLOCK_RE = re.compile(
    r"<!--\s*.*?\bstart\s*-->(.*?)<!--\s*.*?\bstop\s*-->", re.DOTALL | re.IGNORECASE
)
LINKOUT_RE = re.compile(
    r'size="5"\s*>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
INFOS_BEI_RE = re.compile(r"Infos bei\s*:", re.IGNORECASE)

GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

# Numeric "DD.MM.YYYY" and spelled-out-month "DD. Month[,] YYYY" as one
# alternation, so the FIRST (leftmost) date-shaped substring in the text
# wins regardless of format - some blocks' trailing credit-stamp footer
# repeats the date in the other format, sometimes off by a day, so always
# preferring one format over the other (rather than document position)
# risks picking the footer's date instead of the real dateline.
DATE_RE = re.compile(
    r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"
    r"|\b(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+),?\s+(\d{4})\b"
)


def _extract_date(text: str) -> str:
    m = DATE_RE.search(text)
    if not m:
        return ""
    if m.group(1):
        d, mo, y = m.group(1), m.group(2), m.group(3)
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except Exception:
            return ""
    d, month_word, y = m.group(4), m.group(5), m.group(6)
    month_num = GERMAN_MONTHS.get(month_word.lower())
    if not month_num:
        return ""
    try:
        return f"{int(y):04d}-{month_num:02d}-{int(d):02d}"
    except Exception:
        return ""


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "release"


def parse_inline_block(block_html: str) -> Entry:
    soup = BeautifulSoup(block_html, "html.parser")
    headers = []
    for h in soup.find_all(["h3", "h4"]):
        text = h.get_text(" ", strip=True)
        if text:
            headers.append(text)
        h.decompose()
    title = " - ".join(dict.fromkeys(headers))

    text = soup.get_text(" ", strip=True)
    date = _extract_date(text)

    # The contact footer and the arrow links after it are cut at the ELEMENT
    # level now, not out of the flat string. BLOCK_RE already hands us an HTML
    # fragment, so there was never a reason to flatten it first - and the h3/h4
    # headers were decomposed above, so title and body stay disjoint either way.
    richtext.cut_from(soup, INFOS_BEI_RE)
    body, body_html = richtext.extract(soup)
    if not body:
        m = INFOS_BEI_RE.search(text)
        body, body_html = (text[: m.start()].strip() if m else text.strip()), None

    return {
        "kind": "inline",
        "title": title,
        "date": date,
        "url": None,
        "body": body,
        "body_html": body_html,
    }


def parse_linkout_block(block_html: str, base_url: str) -> Entry:
    m = LINKOUT_RE.search(block_html)
    if not m:
        return {}
    href, title_html = m.groups()
    title = BeautifulSoup(title_html, "html.parser").get_text(" ", strip=True)
    if not title:
        return {}
    url = urljoin(base_url, href)

    block = BeautifulSoup(block_html, "html.parser")
    text = block.get_text(" ", strip=True)
    date = _extract_date(text)
    body, body_html = richtext.extract(block)

    return {
        "kind": "linkout",
        "title": title,
        "date": date,
        "url": url,
        "body": body or text,
        "body_html": body_html or None,
    }


def split_subentries(block_html: str) -> list[str]:
    """A "<!-- start -->...<!-- stop -->" block is usually one release, but
    the source occasionally omits the stop/start pair between two releases
    (confirmed live: a "Delta Audiophile 2496" release and an unrelated
    "MIDIMAN loest Macintosh MIDI-USB-Problem" release sharing one block) -
    naively treating the whole thing as one entry merges two headlines into
    a garbled title while silently dropping the second release's actual
    body. Distinguish this from a genuine single release that just has
    multiple <h3> product-subsection headers (e.g. a NAMM-show roundup
    listing several new products under one dateline/contact block - also
    confirmed live) by counting "Infos bei:" contact-footer occurrences:
    a real release always ends with exactly one, so 2+ means 2+ releases
    were actually concatenated. Split at each subsequent <h3> that follows
    an "Infos bei:" occurrence."""
    boundaries = [m.start() for m in INFOS_BEI_RE.finditer(block_html)]
    if len(boundaries) <= 1:
        return [block_html]
    segments = []
    start = 0
    for b in boundaries:
        next_h3 = re.search(r"<h3", block_html[b:], re.IGNORECASE)
        end = b + next_h3.start() if next_h3 else len(block_html)
        segments.append(block_html[start:end])
        start = end
    return segments


def parse_page(
    content: bytes, base_url: str, timestamp: str | None = None
) -> list[Entry]:
    text = content.decode("cp1252", errors="replace")
    entries = []
    for block_html in BLOCK_RE.findall(text):
        for sub_html in split_subentries(block_html):
            if re.search(r"<h3", sub_html, re.IGNORECASE):
                parsed = parse_inline_block(sub_html)
            else:
                parsed = parse_linkout_block(sub_html, base_url)
            if parsed.get("title") and parsed.get("body"):
                parsed["detail_id"] = timestamp
                entries.append(parsed)
    return entries


def parse_generic_page(content: bytes) -> Detail:
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    for tag in soup.find_all(["script", "style", "title"]):
        tag.decompose()
    body, body_html = richtext.extract(soup)
    if not body:
        return {}
    return {"body": body, "body_html": body_html}


def scrape(limit: int | None = None, catch: dict | None = None) -> None:
    conn = connection.connect()
    session = requests.Session()

    best = {}  # (title, date) -> entry dict (+ detail_id)

    for page_url in PAGES:
        print(f"[{SOURCE}] Listing historical captures of {page_url}", flush=True)
        entries = (
            []
            if catch_up.no_crawl(catch)
            else archive.sample_all_captures(
                conn, session, page_url, parse_page, limit=limit
            )
        )
        for e in entries:
            key = (e["title"], e["date"])
            cur = best.get(key)
            if cur is None or len(e["body"]) > len(cur["body"]):
                best[key] = e

    print(
        f"\n[{SOURCE}] {len(best)} distinct release entries found across all captures",
        flush=True,
    )

    for key, e in best.items():
        if not e.get("url"):
            title, date = key
            e["url"] = (
                f"http://www.midiman.de/press/{_slugify(title)}-{date or 'undated'}"
            )

    stats = Stats(SOURCE)

    for (title, date), e in best.items():
        url = e["url"]
        existing = stored_grade(conn, url)
        if existing is not None and existing != "teaser":
            stats.skipped()
            continue

        if e["kind"] == "inline":
            storage.store_release(
                conn,
                SOURCE,
                url,
                title=title,
                date=date,
                body=e["body"],
                body_html=e.get("body_html"),
                detail_id=e["detail_id"],
            )
            stats.added()
            continue

        # linkout: try to recover the real page's full text
        parsed, confirmed = archive.fetch_detail_snapshot(
            conn, session, url, parse_generic_page
        )

        if parsed.get("body"):
            if existing == "teaser":
                # No title=/date=: the listing block's values are better than
                # the linked page's, so only the body is upgraded.
                storage.upgrade_release(
                    conn,
                    url,
                    detail_id=parsed["detail_id"],
                    body=parsed["body"],
                    body_html=parsed["body_html"],
                    grade="full",
                    commit=False,
                )
                stats.upgraded()
            else:
                storage.store_release(
                    conn,
                    SOURCE,
                    url,
                    title=title,
                    date=date,
                    body=parsed["body"],
                    body_html=parsed["body_html"],
                    detail_id=parsed["detail_id"],
                    commit=False,
                )
                stats.added()
            conn.commit()
            continue

        if existing == "teaser":
            stats.skipped()
            continue

        if not confirmed:
            stats.uncertain()
            continue

        if e["body"]:
            storage.store_release(
                conn,
                SOURCE,
                url,
                title=title,
                date=date,
                body=e["body"],
                body_html=e.get("body_html"),
                grade="teaser",
            )
            stats.teaser()
        else:
            stats.dead()

    stats.summary(conn)
    # Both shapes: this CMS has article captures of its own *and* releases that
    # only ever existed inside a listing, whose urls this scraper minted.
    catch_up.run(
        conn,
        SOURCE,
        catch,
        parser=parse_generic_page,
        collect=cached_entries,
        session=session,
    )
    conn.close()


def cached_entries(conn) -> dict[str, Entry]:
    """(url -> entry) out of every cached midiman.de capture, for catch_up.

    Here rather than in the re-extraction library because finding the releases
    inside these pages is this CMS's own knowledge - BLOCK_RE, the inline/linkout
    split, and the fact that an inline release's url has to be *rebuilt* the same
    way the crawl minted it. A copy of that in a shared module is the kind of
    duplicate that goes stale silently.

    Entries carry `origin_url`: the capture they were parsed out of, so the write
    can record where the body came from instead of leaving it to a later
    inference pass.
    """
    out = {}
    for cap_url, content in conn.execute(
        "SELECT url, content FROM page_cache WHERE url LIKE '%midiman.de%'"
    ):
        m = re.search(r"/web/(\d{14})id_/", cap_url)
        try:
            entries = parse_page(
                content, "http://www.midiman.de/", m.group(1) if m else None
            )
        except Exception as e:
            print(f"\n    {cap_url}: {e}")
            continue
        for e in entries:
            if e.get("kind") != "inline" or not e.get("body_html"):
                continue
            url = f"http://www.midiman.de/press/{_slugify(e['title'])}-{e['date']}"
            e["origin_url"] = cap_url
            if url not in out or len(e["body"]) > len(out[url]["body"]):
                out[url] = e
    return out
