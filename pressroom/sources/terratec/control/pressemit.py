"""Scraper for TerraTec's dead press room, the hand-built `pressemit` template
-> unified pressroom.db, sourced entirely from Wayback Machine snapshots since
the live site no longer exists.

One CMS generation on two hosts: terratec.net/press/pressemit/ (English, plus
its French `*_fr.htm` pages) and terratec.de/presse/pressemit/ (German). The
same document with two things swapped, and one pattern covers both swaps - see
DATE_RE. So one crawler, one pool of urls, one tag, and `SITES` is that pool
declared once.

One tag, `terratec`, because a tag identifies a scraper - a CMS generation -
and not a domain. The language is a property of a row, legible in its url
(`.de/presse/` against `.net/press/`), and it is not deduped: the
same release exists in both and the "prefer the English version" call is made by
hand. What that costs is stated plainly: no reader can ask for the German half
any more.

The rule the two tags used to buy was `twin.py`'s refusal to pair across tags,
and here it bought nothing. Measured over the corpus: two (collapsed title,
exact date) groups span the two hosts, and every row in them is over 1300
characters, so `twin.find_pairs` - which only touches a row under `SHORT` -
rejects both. `pressemit` passes no `twins_too` either. It stays a latent trap
rather than a live one, and the distinction that makes it safe is in
`docs/adr/sources-and-tags.md`: across *languages* the titles differ, across
*mirrors* they do not.

Discovery has two channels per host, and neither is redundant - each finds
files the other does not:

  - the archived index captures, whose rows link out to the articles. This is
    the channel that carries metadata: the listing states the headline and the
    date, and for a release whose own page was never captured that is all there
    is ever going to be.
  - CDX's listing of the article folder itself, which needs no index capture to
    have survived. On .de this channel replaced a hand-collected url list -
    files that were in the Wayback directory listing but linked from no index
    page - because that list *was* this query, run once by hand and pasted in.

The two are merged by **filename**, lowercased, which is the only key they
share. CDX reports `www.terratec.de:80/…`, an index page links to
`www.terratec.de/…` with no port, and CDX's own listing carries both
`terratec.de:80` and `www.terratec.de:80` because SURT drops a `www.` - three
spellings of one file, and `releases.url` is UNIQUE and compared verbatim, with
no normalisation anywhere in this tree. `already_stored()` sees only the exact
url, so feeding a raw CDX listing to it stores most of an existing corpus a
second time. `candidates()` is where that is prevented, and it is why the
already-stored check here is by filename rather than by url.

**A filename is only unique within one host.** 58 of the 166 files in this
generation exist under both, so the already-stored check is keyed by *site and
filename*, and `site_of()` is what supplies the first half. Keyed by filename
alone - which is what it was when each host had its own tag - it would drop 58
German releases from the work list as "already stored", silently and with no
error. That is the one thing collapsing the two tags actually broke.
"""

import re
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from pressroom.text.control.dating import iso_date
from pressroom.database.control import connection
from pressroom.release.control import storage
from pressroom.scraper.control import catch_up
from pressroom.scraper.control import discovery
from pressroom.text.control import richtext
from pressroom.reporting.control.outcome import Stats
from pressroom.fetcher.control import archive
from pressroom.scraper.entity.parse import Detail, Entry


SOURCE = "terratec"

#: The pool, one entry per host. Keyed by site rather than by tag - there is one
#: tag now - and carrying only the article folder CDX is asked about, since one
#: parser covers both templates.
SITES: dict[str, dict[str, str]] = {
    "net": {"prefix": "http://www.terratec.net:80/press/pressemit/"},
    "de": {"prefix": "http://www.terratec.de:80/presse/pressemit/"},
}

# (base url the links resolve against, site, the index page's capture). A flat
# list of triples rather than a key inside SITES, because tests/refresh.py walks
# the whole thing to keep the listing fixtures fresh.
INDEX_PAGES = [
    (
        "http://www.terratec.de/presse/",
        "de",
        "https://web.archive.org/web/20030303193557id_/http://www.terratec.de/presse/pressearchiv.htm",
    ),
    (
        "http://www.terratec.de/presse/",
        "de",
        "https://web.archive.org/web/20030227043415id_/http://www.terratec.de/presse/pressemit.htm",
    ),
    (
        "http://www.terratec.net/press/",
        "net",
        "https://web.archive.org/web/20030222201120id_/http://www.terratec.net/press/pressarchive.htm",
    ),
    (
        "http://www.terratec.net/press/",
        "net",
        "https://web.archive.org/web/20030206053420id_/http://www.terratec.net/press/pressreleases.htm",
    ),
    (
        "http://www.terratec.net/press/",
        "net",
        "https://web.archive.org/web/20030405064946id_/http://www.terratec.net/press/pressreleases.htm",
    ),
]

# Both spellings of the dateline in one pattern: "Press Release, DD.MM.YYYY" on
# the .net pages, "TerraTec PresseInfo vom DD.MM.YYYY" on the .de ones (plus the
# "Presseinformation vom" style the very-early static page already used).
#
# One pattern rather than two calls because the union was measured against the
# split, over every cached capture of both hosts, field by field: 220 identical,
# 1 filled, 0 changed. The one filled is `beck.htm` - a German page carrying the
# *English* dateline, whose date the .de-only pattern never found.
DATE_RE = re.compile(
    r"(?:Press Release,|Presse(?:Info|information) vom)\s*(\d{1,2}\.\d{1,2}\.\d{2,4})",
    re.IGNORECASE,
)

# The bold dateline the headline follows, in both languages. Substrings, not
# MARKER_RE - parse_page's docstring says why widening this further is a trap,
# and this is exactly as wide as the .de pages already needed. The four French
# `*_fr.htm` pages are the ones that rule protects, and they are in the corpus
# with cached bytes, so the measurement above covers them.
BOLD_MARKERS = ("presseinfo", "press release")

# The dateline that sits immediately above the headline, in all three languages
# this one hand-built site was published in: terratec.net English, its
# /press/*_fr.htm French pages, and terratec.de German. It locates the headline,
# never the date - DATE_RE and DATE_RE_DE own that.
MARKER_RE = re.compile(r"press\s*release|communiqu\w*\s+de\s+presse|presseinfo", re.I)

_HEADINGS = ("h1", "h2", "h3", "h4")

# What ends a bare-text headline. Not "any block": <tr>/<td> are
# the container being walked into, so stopping on those would end the walk
# before any text. Stopping on <p> is the point of the rule - a cell that opens
# with a paragraph has no headline, and descending into it would title the row
# with the release's first sentence instead.
_HEADLINE_ENDS = {"p", "h1", "h2", "h3", "h4", "table", "ul", "ol", "div", "blockquote"}

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
    shape 3 refuses rather than titling the row with its lead
    sentence.
    """
    bolds = soup.find_all(["b", "strong"])
    marker = None
    for i, tag in enumerate(bolds):
        if MARKER_RE.search(tag.get_text(" ", strip=True)):
            marker = tag
            for nxt in bolds[i + 1 :]:
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


def parse_page(content: bytes, date_re: re.Pattern, markers: tuple[str, ...]) -> Detail:
    """One release page of the hand-built terratec.net/terratec.de template.

    Both hosts are the same document with two things swapped, so they are two
    calls rather than two copies: `date_re` is the dateline as that site spells
    it, and `markers` are the lowercase substrings that identify the bold
    dateline the headline follows.

    `markers` is not MARKER_RE, which covers all three languages
    and would be wrong here. The rule below takes `bold_tags[i + 1]` blindly, so
    on the French pages a "communiqué de presse" match would title all four of
    them with the download-link label that follows it. Today nothing matches
    there, the title stays empty, and find_headline - which *does* skip a bold
    that is itself a marker - gets it right. Substrings rather than a regex for
    the same reason of not widening anything in passing: `get_text(strip=True)`
    joins with no separator, so a pattern with an optional space would match a
    "PressRelease" that `"press release" in t` does not.
    """
    # from_encoding, not decode_html: these 2002 pages declare no charset at all
    # and are wholly pre-UTF-8, so the bytes are cp1252 - and in prose full of
    # German accents two adjacent high bytes can coincidentally form a valid
    # UTF-8 sequence that decode_html would honour. Left to sniff, bs4 reads
    # these as ISO-8859-1 and stores the cp1252 punctuation range as C1 control
    # characters.
    soup = BeautifulSoup(content, "html.parser", from_encoding="cp1252")
    text = soup.get_text(" ", strip=True)
    # Body from the DOM, date from the flat text. These pages are one big
    # layout table, and the article's table is the one carrying the most text -
    # see richtext.densest for why that beats a width= selector here. The flat
    # text stays for `date_re`, which scans the whole page including the
    # header where the date actually sits.
    body, body_html = richtext.extract(richtext.densest(soup, "table", border="0"))
    if not body:
        # No layout table, or one with nothing in it: a handful of these
        # captures are 290-byte "page moved" stubs. Fall back to the flat text
        # rather than to nothing - an empty body_html means "not converted",
        # an empty body would mean the row was wiped.
        body, body_html = text, None

    date = ""
    m = date_re.search(text)
    if m:
        date = iso_date(m.group(1), dayfirst=True)

    title = ""
    bold_tags = soup.find_all(["b", "strong"])
    for i, tag in enumerate(bold_tags):
        t = tag.get_text(strip=True).lower()
        if any(marker in t for marker in markers):
            if i + 1 < len(bold_tags):
                title = bold_tags[i + 1].get_text(strip=True)
            break
    if not title:
        title = find_headline(soup)

    return {"title": title, "date": date, "body": body, "body_html": body_html}


def parse_snapshot(content: bytes) -> Detail:
    """Every page of this generation - .net English and French, .de German.

    One function, so phase 2 and `provenance`'s verifier can be handed a parser
    without knowing which host a row came from. `parse_page` keeps its two
    parameters: they are what made proving the union possible, one call against
    the other.
    """
    return parse_page(content, DATE_RE, BOLD_MARKERS)


def extract_links(content: bytes, base_url: str) -> list[Entry]:
    # cp1252 stated, never sniffed - see parse_page for why.
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


def _filename(url: str) -> str:
    """The key the two discovery channels share - see the module docstring."""
    return url.rsplit("/", 1)[-1].lower()


def _under(prefix: str, url: str) -> bool:
    """Whether `url` really sits inside the folder `prefix` names.

    Paths, not urls, because CDX matches a prefix against the SURT string and
    that gets two things wrong for this purpose. It ignores a `www.`, so the .de
    listing carries both spellings of the host; and it does not stop at a path
    segment, so the listing for `/presse/pressemit/` also carries
    `/presse/pressemit.htm` - the index page itself, which `is_html_page` is
    happy with and which would be stored as a release.
    """
    return urlsplit(url).path.startswith(urlsplit(prefix).path)


def site_of(url: str) -> str | None:
    """Which of `SITES` a url belongs to, or None if none of them.

    The second half of the dedup key, and the reason `SITES`' prefixes have to
    stay pairwise disjoint by path - `tests/sources/terratec/test_pressemit.py`
    asserts that, because a third host sharing a path would silently fall into
    another site's partition. Also what picks the fixtures, one per template:
    `tests/refresh.py` asks this rather than counting.
    """
    for site, cfg in SITES.items():
        if _under(cfg["prefix"], url):
            return site
    return None


def from_indexes(conn, session, site: str) -> dict[str, Entry]:
    """This site's articles as its archived index captures link to them.

    Keyed by filename, so what comes back can be merged with the other channel.
    """
    found: dict[str, Entry] = {}
    for base_url, tag, wayback_url in INDEX_PAGES:
        if tag != site:
            continue
        print(f"[{SOURCE}/{site}] Fetching {wayback_url}", flush=True)
        # Losing one index page just means fewer candidates, so warn and carry
        # on rather than aborting the run.
        try:
            content = archive.fetch_snapshot(conn, session, wayback_url, timeout=20)
        except Exception as e:
            print(f"  ERROR fetching index page: {e}")
            continue
        for entry in extract_links(content, base_url):
            found.setdefault(_filename(entry["url"]), entry)
    return found


def from_prefix(site: str, prefix: str) -> dict[str, Entry]:
    """Every article page CDX lists under this site's folder.

    Keyed by filename, and carrying no metadata: a url is all this channel
    knows, which makes a confirmed absence here a `dead` rather than
    the stub an index entry earns.

    `or_exit` rather than the degrading form golive.py uses, even though there
    is a second channel to fall back on: nearly every row of this generation
    came from this listing, so continuing on the index pages alone would
    under-discover and print a small candidate count that reads as success. It
    does mean a CDX outage on one host ends the run before the other host and
    before phase 2 - which is what a rerun is for, and portal.py's two portals
    already make the same call.
    """
    print(f"[{SOURCE}/{site}] Listing archived pages under {prefix}", flush=True)
    return {
        _filename(s["original"]): {"url": s["original"], "title": "", "date": ""}
        for s in archive.list_snapshots_or_exit(prefix)
        if is_html_page(s["original"]) and _under(prefix, s["original"])
    }


def stored_filenames(conn, site: str) -> set[str]:
    """The filenames this site already holds, out of the one tag's rows.

    One query for the whole tag, partitioned by `site_of` - not a query per
    site, and not `already_stored()`, which compares the exact url and so
    cannot see the same file under a second spelling of its host.
    """
    return {
        _filename(url)
        for url in storage.source_urls(conn, SOURCE)
        if site_of(url) == site
    }


def candidates(conn, session, site: str, *, offline: bool, limit=None) -> list[Entry]:
    """One site's whole work list: both channels, merged and filtered.

    Merged by filename for the reason the module docstring gives. Where both
    channels have a file, the index entry's metadata wins - it is the only
    metadata there is - and the CDX spelling of the url wins, because that is
    what archive.org itself reports and what the stored rows of this generation
    already carry.

    Already-stored is checked per site, never across the tag: 58 filenames exist
    on both hosts, so the flat check would call the German copy of an English
    release stored. It is also what makes the two channels safe in either order,
    and it is one query rather than one per candidate.
    """
    listed = {} if offline else from_indexes(conn, session, site)
    crawled = {} if offline else from_prefix(site, SITES[site]["prefix"])

    pool: dict[str, Entry] = dict(crawled)
    for name, entry in listed.items():
        # The listing's title and date, the archive's spelling of the url where
        # the folder listing has this file too.
        known = pool.get(name)
        pool[name] = {**entry, "url": known["url"] if known else entry["url"]}

    have = stored_filenames(conn, site)
    work = [entry for name, entry in pool.items() if name not in have]

    # After the merge and the already-stored filter, so `--limit` caps what is
    # actually fetched rather than what was discovered - and before the count
    # below, so the printed number is the one the run will honour.
    if limit:
        work = work[:limit]
    print(
        f"[{SOURCE}/{site}] {len(work)} candidate articles to fetch "
        f"({len(listed)} from index pages, {len(crawled)} from the folder, "
        f"{len(pool)} distinct)",
        flush=True,
    )
    return work


def scrape(
    limit: int | None = None,
    sites: list | None = None,
    catch: dict | None = None,
) -> None:
    conn = connection.connect()
    session = requests.Session()
    wanted = list(sites or SITES)

    # Both pools before any article is fetched, so one `Stats` can carry the
    # total across the hosts - one tag, one summary - and so a CDX
    # outage on either host ends the run before it has done any work.
    pools = {
        site: candidates(
            conn, session, site, offline=catch_up.no_crawl(catch), limit=limit
        )
        for site in wanted
    }
    stats = Stats(SOURCE, total=sum(len(w) for w in pools.values()))
    for work in pools.values():
        discovery.from_candidates(
            conn,
            session,
            SOURCE,
            [entry["url"] for entry in work],
            parse=parse_snapshot,
            stats=stats,
            titles={e["url"]: e["title"] for e in work if e["title"]},
            dates={e["url"]: e["date"] for e in work if e["date"]},
            # An index page names releases whose own page the archive never
            # captured: the title and date are real, so the row is worth keeping
            # as a stub. A url only CDX named has nothing to keep, and CDX
            # having just listed it makes an absence near-impossible anyway.
            stub_if_absent=True,
        )
    stats.summary(conn)

    # One tag, so one phase 2.
    catch_up.run(conn, SOURCE, catch, parser=parse_snapshot, session=session)
    conn.close()
