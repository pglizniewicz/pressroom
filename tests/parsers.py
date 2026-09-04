"""How to reproduce one scraper's parse from bytes - the table the golden tests
walk.

Two shapes, matching the two the corpus actually has:

  detail    bytes -> {title, date, body, body_html}. A whole-page parse. The
            five live scrapers are in here too: their `parse_detail` takes the
            bytes `politeness.fetch_cached` cached under the row's own url, so
            the fixture is that page rather than an archive capture -
            `LIVE_SOURCES` says which, for `refresh.py`'s picker.
  listing   bytes -> [entry, ...]. A capture holding many releases; the golden
            file pins the whole list, which is also what pins each collector's
            `origin_url` stamping - the thing the collectors used to throw away.

`verification.CACHED_PARSERS` is reused verbatim where it covers a tag, because
a second copy would drift. The rest cannot come from there:
`verification.candidate_bodies` returns *bodies* so it can compare them, and a
golden file needs the whole parse.
"""

from pressroom.creative.control import globenewswire
from pressroom.creative.control import press as creative_press
from pressroom.maudio.control import (
    media_news,
    media_pr,
    news_blog,
    presse_de,
    pressdb,
)
from pressroom.provenance.boundary.verification import CACHED_PARSERS
from pressroom.q4.control import platform
from pressroom.soundonsound.control import magazine
from pressroom.terratec.control import cms, early, portal, pressemit

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

# The tags whose pages are fetched live and cached under the row's own url, so
# their detail fixture is that page and carries no capture timestamp.
LIVE_SOURCES = ("intel", "amd", "creative", "creative_gnw", "soundonsound")

# --- detail: bytes -> dict --------------------------------------------------

DETAIL = dict(CACHED_PARSERS)
DETAIL.update({tag: pressdb.parse_detail for tag in PRESSDB})
DETAIL.update({tag: media_news.parse_detail for tag in MEDIA_NEWS})
DETAIL.update({tag: portal.parse_snapshot for tag in PORTAL})
DETAIL.update({tag: cms.parse_detail for tag in CMS})
DETAIL.update(
    {
        "intel": platform.parse_detail,
        "amd": platform.parse_detail,
        "creative": creative_press.parse_detail,
        "creative_gnw": globenewswire.parse_detail,
        "soundonsound": magazine.parse_detail,
    }
)

# --- listing: (bytes, base_url, timestamp) -> list --------------------------

LISTING = {}
LISTING.update({tag: pressdb.extract_entries for tag in PRESSDB})
LISTING.update({tag: media_pr.extract_entries for tag in MEDIA_PR})
LISTING.update({tag: media_news.extract_entries for tag in MEDIA_NEWS})
LISTING.update({tag: cms.extract_entries for tag in CMS})
LISTING["midiman_de"] = presse_de.parse_page
LISTING["maudio_com_news"] = lambda c, base, ts: news_blog.parse_listing_page(c, base)
LISTING["soundonsound"] = lambda c, base, ts: magazine.parse_listing(c)
LISTING["terratec"] = lambda c, base, ts: pressemit.extract_links(c, base)
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


def parse(kind: str, source: str, content: bytes, *, base_url="", timestamp=""):
    """Run the route this fixture declares and return something JSON-able."""
    if kind == "detail":
        return DETAIL[source](content)
    if kind == "listing":
        return LISTING[source](content, base_url, timestamp)
    raise ValueError(f"unknown fixture kind: {kind!r}")


def routes_for(source: str) -> list:
    out = []
    if source in DETAIL:
        out.append("detail")
    if source in LISTING:
        out.append("listing")
    return out
