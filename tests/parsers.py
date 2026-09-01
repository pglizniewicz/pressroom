"""How to reproduce one source's parse from bytes - the table the golden tests
walk.

Three shapes, matching the three the corpus actually has:

  detail    bytes -> {title, date, body, body_html}. A whole-page parse.
  listing   bytes -> [entry, ...]. A capture holding many releases; the golden
            file pins the whole list, which is also what pins each collector's
            `origin_url` stamping - the thing the collectors used to throw away.
  live      (conn, url) -> (body, body_html), through fetch_cached. The five
            live sources have no snapshot parser at all: their extraction is
            inside fetch_body, and calling it with the bytes already in
            page_cache exercises the cache path as well as the parser.

`verification.CACHED_PARSERS` is reused verbatim where it covers a tag, because
a second copy would drift. The rest cannot come from there:
`verification.candidate_bodies` returns *bodies* so it can compare them, and a
golden file needs the whole parse.
"""

from pressroom.creative.control import globenewswire
from pressroom.creative.control import press as creative_press
from pressroom.maudio.control import media_news, media_pr, news_blog, presse_de, pressdb
from pressroom.provenance.boundary.verification import CACHED_PARSERS
from pressroom.q4.control import platform
from pressroom.soundonsound.control import magazine
from pressroom.terratec.control import cms, early, portal, presse

MEDIA_PR = ("midiman_com_media_pr", "midiman_net_media_pr", "maudio_com_media_pr")
MEDIA_NEWS = (
    "midiman_com_media_news",
    "midiman_net_media_news",
    "maudio_com_media_news",
    "midiman_couk_news",
)
PRESSDB = ("midiman_com_pressdb", "midiman_net_pressdb")
PORTAL = ("terratec_pressde", "terratec_pressen")
CMS = ("terratec_new_de", "terratec_new_en")

# --- detail: bytes -> dict --------------------------------------------------

DETAIL = dict(CACHED_PARSERS)
DETAIL.update({tag: pressdb.parse_detail for tag in PRESSDB})
DETAIL.update({tag: media_news.parse_detail for tag in MEDIA_NEWS})
DETAIL.update({tag: portal.parse_snapshot for tag in PORTAL})
DETAIL.update({tag: cms.parse_detail for tag in CMS})

# --- listing: (bytes, base_url, timestamp) -> list --------------------------

LISTING = {}
LISTING.update({tag: pressdb.extract_entries for tag in PRESSDB})
LISTING.update({tag: media_pr.extract_entries for tag in MEDIA_PR})
LISTING.update({tag: media_news.extract_entries for tag in MEDIA_NEWS})
LISTING.update({tag: cms.extract_entries for tag in CMS})
LISTING["midiman_de"] = presse_de.parse_page
LISTING["maudio_com_news"] = lambda c, base, ts: news_blog.parse_listing_page(c, base)
LISTING["soundonsound"] = lambda c, base, ts: magazine.parse_listing(c)
LISTING["terratec_de"] = lambda c, base, ts: presse.extract_links(c, base)
LISTING["terratec"] = lambda c, base, ts: presse.extract_links(c, base)
# The yearly category listings - the channel portal.py calls "irreplaceable
# archaeology" and the one that raised NameError on every run for a day, because
# the regex it referenced was left behind in a deleted file. Nothing else in
# this repo executes it.
LISTING.update({tag: lambda c, base, ts: portal.extract_teasers(c) for tag in PORTAL})
# The two 1996-97 anchor pages: the whole source is two listings, and which
# one a capture is decides both the date marker and the anchor scheme.
LISTING["terratec_early"] = lambda c, base, ts: early.extract_entries(
    c.decode("cp1252", errors="replace"),
    next(p for p in early.PAGES if p["timestamp"] == ts or p["base_url"] == base),
)

# --- live: (conn, url) -> (body, body_html) ---------------------------------

LIVE = {
    "intel": platform.fetch_body,
    "amd": platform.fetch_body,
    "creative": creative_press.fetch_body,
    "creative_gnw": globenewswire.fetch_body,
    "soundonsound": magazine.fetch_body,
}


def parse(
    kind: str,
    source: str,
    content: bytes,
    *,
    base_url="",
    timestamp="",
    conn=None,
    url="",
):
    """Run the route this fixture declares and return something JSON-able."""
    if kind == "detail":
        return DETAIL[source](content)
    if kind == "listing":
        return LISTING[source](content, base_url, timestamp)
    if kind == "live":
        # session=None on purpose: fetch_cached must answer from page_cache and
        # never reach for it. A route that fetches raises AttributeError here,
        # which is the failure we want rather than a silent request.
        body, body_html = LIVE[source](conn, None, url)
        return {"body": body, "body_html": body_html}
    raise ValueError(f"unknown fixture kind: {kind!r}")


def routes_for(source: str) -> list:
    out = []
    if source in DETAIL:
        out.append("detail")
    if source in LISTING:
        out.append("listing")
    if source in LIVE:
        out.append("live")
    return out
