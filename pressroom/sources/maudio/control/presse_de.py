"""Scraper for MIDIMAN/M-Audio's German site (midiman.de) press archive ->
unified pressroom.db, sourced entirely from Wayback Machine snapshots.

midiman.de never had any CMS - confirmed by a full-domain CDX scan: no .php, no
do=media.*, no cgi-bin, ever. Static .htm from 1998 to its death around 2006,
and three pages hold the whole press history:
  - oldpress.htm ("Aeltere Pressemitteilungen" - the older releases)
  - pressemt.htm ("Presseraum" - current releases, linking back to oldpress)
  - messe03.htm - the Musikmesse 2003 roundup, which both of them link to
The first two are full re-dumps of the whole history - the same releases appear
verbatim across many captures of both - so every historical capture of all three
is sampled through archive.list_all_captures and entries are deduped by
(title, date), keeping the longest body.

messe03.htm read as one release for a long time, which is what it looks like
from the listing side: a big link and one pointer line, "Pressemitteilung
Musikmesse Frankfurt vom 27.02.2003". It is a third listing - a table of
contents over product announcements, each with its own <h3>, its own footer and
an <a name> to link to - so the whole page as one row was one body holding
several releases. Two things follow. Its sections carry no dateline of their
own, so they inherit the date that pointer gives the page: ROUNDUP_PAGES, keyed
by file name because the listings link it relative while its captures are under
www. And they are the only releases here with an address of their own,
`messe03.htm#radium`; the two listings do carry the odd stray <a name>, but
their rows have been keyed by the synthetic url below from the start, and
re-keying them is a separate decision.

Each release lives in an HTML-comment-delimited block, and the vocabulary is
wider than it first looked: "<!-- start -->...<!-- stop -->", product-name
tagged ("<!-- Radium start -->"), tagged with no verb at all ("<!-- ozone -->
...<!-- ozone stop -->"), closed with "ende" or "end" rather than "stop", and
sometimes closed under a different product's name than it was opened with. So
blocks() pairs comments by position, never by name, and reads any comment that
does not say stop/end/ende as an opener. What it must not do is *require* the
delimiters: releases on both listings have none at all, and the regex this
replaced only reached those by accident - its `.*?` was DOTALL and ran straight
across `-->`, so the "opener" could be the stylesheet comment in <head> and one
"block" most of the page, which split_subentries then cut on "Infos bei:".
Hence the second half of blocks(): an undelimited stretch is kept when it
carries a contact footer, because that is what says a release ended there, and
dropped when it does not - page head, nav table, table of contents.

The great majority of blocks are "inline": <h4> descriptive headline + <h3>
short product name + a "Ort, DD.MM.YYYY" dateline + body <p> + a contact footer,
which is cut. The footer has two forms: "Infos bei:" and a postal address on the
two listings, and on messe03.htm nothing but the arrow-link list ("weitere
Produkt-Infos FireWire 410"). The link form is matched anchored at the start of
a text node, and that anchoring is the point rather than tidiness: calibrated
over every cached capture it hits link texts and no prose, while "Weitere Infos
erhalten Sie bei Sinec" is a sentence in the middle of a release on
oldpress.htm, and cutting there would drop the rest of its body. Verified
against a live sample that those links go to product marketing pages, not to
richer versions of the release, so they are correctly left unfetched - the block
already *is* the complete release.

Dates come in two formats: numeric "DD.MM.YYYY" for most, and spelled-out German
months ("15. Januar 1999") for the oldest entries. _extract_date() tries numeric
first and requires an unambiguous 4-digit year, because a block's trailing
credit stamp repeats the date with two digits and would otherwise win over the
real dateline. An English "Month DD, YYYY" is read too, but only once both
German forms have found nothing - _english_date().

No <a name> is dependable on the two listings, so an inline release there has no
natural per-release url; a stable synthetic one is built from the slugified
(title, date) - deliberately independent of which of the two pages the entry
came off, so a release found on both collapses to one row.

Encoding is Windows-1252, undeclared: 0x99 appearing as (TM) is only valid in
cp1252, not Latin-1. Same treatment as pressdb.py.
"""

import re

import requests
from bs4 import BeautifulSoup

from pressroom.release.control.storage import stored_grade
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraper.control import catch_up
from pressroom.text.control import richtext
from pressroom.reporting.entity.outcome import Stats
from pressroom.fetcher.control import address
from pressroom.scraper.control import discovery
from pressroom.scraper.entity.parse import Entry

SOURCE = "midiman_de"

PAGES = [
    "http://www.midiman.de/oldpress.htm",
    "http://midiman.de/pressemt.htm",
    "http://www.midiman.de/messe03.htm",
]

# The page in PAGES that is a roundup: undated sections with anchors of their
# own, and the date the two listings give the whole page. File name rather than
# url, because the listings link it relative and its captures are under www.
ROUNDUP_PAGES = {"messe03.htm": "2003-02-27"}

COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
# No word boundary in front of the verb, and it is load-bearing for exactly
# one comment in the whole archive: "<!-- PCI 22stop -->", whose release is
# the Power Mac G5 one. With \\bstop that comment reads as an opener, which
# both swallows the release before it and pairs the wrong two comments.
CLOSER_RE = re.compile(r"(stop|ende|end)\b", re.IGNORECASE)
INFOS_BEI_RE = re.compile(r"Infos bei\s*:", re.IGNORECASE)
FOOTER_LINK_RE = re.compile(r"^\s*(weitere|mehr)\s+(Produkt-)?Infos\b", re.IGNORECASE)

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

ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
ENGLISH_DATE_RE = re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\b")


def _extract_date(text: str) -> str:
    m = DATE_RE.search(text)
    if not m:
        return _english_date(text)
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


def _english_date(text: str) -> str:
    """ "Month DD, YYYY" - the fallback, and only ever a fallback.

    A handful of releases here were published in English and never translated.
    Most of them still carry the German credit stamp that ends every release on
    these pages, so the numeric rule dates them; the Avid acquisition
    announcement does not, and its only date is "Tewksbury, MA - August 20,
    2004". Asked after both German formats have found nothing, so a body that
    quotes an English date cannot outvote its own dateline.
    """
    m = ENGLISH_DATE_RE.search(text)
    if not m:
        return ""
    month = ENGLISH_MONTHS.get(m.group(1).lower())
    if not month:
        return ""
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "release"


def roundup_date(page_url: str) -> str:
    """The date every release on this page inherits, or "".

    Only the roundup page has one: its sections were published as one press kit
    and none of them repeats the date, so the only date on that page is the one
    the two listings state when they link to it.
    """
    name = (page_url or "").rsplit("/", 1)[-1].split("#")[0].lower()
    return ROUNDUP_PAGES.get(name, "")


def blocks(html: str) -> list[str]:
    """Every stretch of one page that can hold a release.

    Two rules, and the second is not a fallback that can be dropped later. The
    delimited blocks: comments paired by position, anything that does not say
    stop/end/ende opening one. And the undelimited stretches between them,
    which are real - releases on both listings carry no delimiters at all - kept
    only when a contact footer says a release ended in there, so the page head,
    the nav table and the roundup's table of contents fall out.
    """
    spans, opener = [], None
    for m in COMMENT_RE.finditer(html):
        if CLOSER_RE.search(m.group(1)):
            if opener is not None:
                spans.append((opener, m.start()))
                opener = None
        else:
            opener = m.end()

    out, prev = [], 0
    for start, end in spans + [(len(html), len(html))]:
        loose = _from_first_headline(html[prev:start])
        if loose and INFOS_BEI_RE.search(loose):
            out.append(loose)
        if end > start:
            out.append(html[start:end])
        prev = end
    return out


def _from_first_headline(html: str) -> str:
    """An undelimited stretch from its first headline on, or "".

    A delimited block starts where its release starts. An undelimited one starts
    wherever the previous block ended, which for the first stretch on a page is
    the page itself - logo, nav table, and the script whose source sits in an
    HTML comment, all of which the page's first release would otherwise open
    with.
    """
    m = re.search(r"<h[34]\b", html, re.IGNORECASE)
    return html[m.start() :] if m else ""


def _anchor_url(soup, page_url: str) -> str | None:
    """`messe03.htm#radium` for a section of the roundup page, else None.

    Asked of the roundup alone: an `<a name>` turns up here and there on the two
    listings too, and honouring it there would re-key rows that have been under
    their synthetic url since the first crawl.
    """
    if not roundup_date(page_url):
        return None
    anchor = soup.find("a", attrs={"name": True})
    return f"{page_url}#{anchor['name']}" if anchor else None


def parse_inline_block(block_html: str, page_url: str = "") -> Entry:
    soup = BeautifulSoup(block_html, "html.parser")
    url = _anchor_url(soup, page_url)
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
    # level now, not out of the flat string. blocks() already hands us an HTML
    # fragment, so there was never a reason to flatten it first - and the h3/h4
    # headers were decomposed above, so title and body stay disjoint either way.
    # "Infos bei:" first, and the link list only on the roundup. On the two
    # listings the address comes before the links, so cutting at the address
    # takes both; where a release there has no address footer at all, its link
    # list has been part of the stored body since the first crawl, and cutting
    # it now would be a shorter body - which `gate.not_shorter` refuses under
    # every other gate, force included. So those rows keep it, and tidying them
    # is a decision about rewriting rows rather than about this parser.
    if not richtext.cut_from(soup, INFOS_BEI_RE) and roundup_date(page_url):
        richtext.cut_from(soup, FOOTER_LINK_RE)
    body, body_html = richtext.extract(soup)
    if not body:
        m = INFOS_BEI_RE.search(text)
        body, body_html = (text[: m.start()].strip() if m else text.strip()), None

    return {
        "title": title,
        "date": date,
        "url": url,
        "body": body,
        "body_html": body_html,
    }


def split_subentries(block_html: str) -> list[str]:
    """A block is usually one release, but the source occasionally omits the
    stop/start pair between two releases (confirmed live: a "Delta Audiophile
    2496" release and an unrelated "MIDIMAN loest Macintosh MIDI-USB-Problem"
    release sharing one block) - naively treating the whole thing as one entry
    merges two headlines into a garbled title while silently dropping the second
    release's actual body. Distinguish this from a genuine single release that
    just has multiple <h3> product-subsection headers (e.g. a NAMM-show roundup
    listing several new products under one dateline/contact block - also
    confirmed live) by counting "Infos bei:" contact-footer occurrences: a real
    release always ends with exactly one, so 2+ means 2+ releases were actually
    concatenated. Split at each subsequent <h3> that follows an "Infos bei:"
    occurrence.

    Counts the address form of the footer only. The roundup page has the link
    form and one release per block, so a second boundary vocabulary here would
    only find boundaries inside releases that carry both.
    """
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
    inherited = roundup_date(base_url)
    # Stamped here rather than by each caller: this is where both halves of the
    # address are in hand, and both the crawl and the collector need it - the
    # crawl to record provenance in the same transaction as the body, the
    # collector because `from_listings` reads it off the entry.
    origin_url = address.snapshot_url(timestamp, base_url) if timestamp else None
    entries = []
    for block_html in blocks(text):
        for sub_html in split_subentries(block_html):
            if not re.search(r"<h3", sub_html, re.IGNORECASE):
                continue
            parsed = parse_inline_block(sub_html, base_url)
            if not (parsed.get("title") and parsed.get("body")):
                continue
            parsed["date"] = parsed["date"] or inherited
            parsed["detail_id"] = timestamp
            parsed["origin_url"] = origin_url
            entries.append(parsed)
    return entries


def release_url(entry: Entry, title: str, date: str) -> str:
    """The entry's own address if it has one, else the synthetic url.

    One function because the crawl and the collector have to agree character for
    character: the url is the dedup key, and a second copy of this rule drifting
    would file every roundup section twice.
    """
    return entry.get("url") or (
        f"http://www.midiman.de/press/{_slugify(title)}-{date or 'undated'}"
    )


def scrape(limit: int | None = None, catch: dict | None = None) -> None:
    conn = connection.connect()
    session = requests.Session()

    best = {}  # (title, date) -> entry dict (+ detail_id)

    for page_url in PAGES:
        print(f"[{SOURCE}] Listing historical captures of {page_url}", flush=True)
        entries = (
            []
            if catch_up.no_crawl(catch)
            else discovery.sample_all_captures(
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

    stats = Stats(SOURCE, total=len(best))

    # One loop and no detail fetches: every release in this source is the
    # listing block itself. The roundup's sections have a page to link to, but
    # it is a listing too - the one thing fetching it as a detail page ever
    # produced was a row whose body was the whole page.
    for (title, date), e in best.items():
        url = release_url(e, title, date)
        existing = stored_grade(conn, url)
        if existing is not None and existing != "teaser":
            stats.skipped()
            continue
        if storage.store_release(
            conn,
            SOURCE,
            url,
            title=title,
            date=date,
            body=e["body"],
            body_html=e.get("body_html"),
            detail_id=e["detail_id"],
            origin_url=e.get("origin_url"),
        ):
            stats.added()
        else:
            stats.skipped()

    stats.summary(conn)
    # One shape only: releases that exist inside a listing and nowhere else, so
    # a collector and no detail parser - a per-row fetch here is a guaranteed
    # 404 against a url this scraper minted.
    catch_up.run(conn, SOURCE, catch, collect=cached_entries, session=session)
    conn.close()


def cached_entries(conn) -> dict[str, Entry]:
    """(url -> entry) out of every cached midiman.de capture, for catch_up.

    Here rather than in the re-extraction library because finding the releases
    inside these pages is this CMS's own knowledge - blocks(), the footer forms,
    and the fact that a release's url has to be *rebuilt* the same way the crawl
    minted it. A copy of that in a shared module is the kind of duplicate that
    goes stale silently.

    Entries carry `origin_url`: the capture they were parsed out of, so the write
    can record where the body came from instead of leaving it to a later
    inference pass. `parse_page` stamps it off the address this loop takes apart,
    so the collector and the crawl cannot disagree about it.
    """
    out = {}
    for cap_url, content in conn.execute(
        "SELECT url, content FROM page_cache WHERE url LIKE '%midiman.de%'"
    ):
        m = re.search(r"/web/(\d{14})id_/(.+)$", cap_url)
        try:
            entries = parse_page(
                content,
                m.group(2) if m else "http://www.midiman.de/",
                m.group(1) if m else None,
            )
        except Exception as e:
            print(f"\n    {cap_url}: {e}")
            continue
        for e in entries:
            if not e.get("body_html"):
                continue
            url = release_url(e, e["title"], e["date"])
            if url not in out or len(e["body"]) > len(out[url]["body"]):
                out[url] = e
    return out
