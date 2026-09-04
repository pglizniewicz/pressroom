"""AMD's press releases, on the Q4 Inc. investor-relations platform.

The crawler and the parser are `q4.control.platform`'s, shared with Intel; what
is AMD's is the listing url and two selectors: AMD's Q4 instance uses the
Bootstrap media classes where Intel's uses the platform default, which is the
only difference between the two sources.
"""

from pressroom.q4.control import platform

SOURCE = "amd"
LIST_URL = "https://ir.amd.com/news-events/press-releases"
CONTAINER_SEL = "div.media-body"
TITLE_LINK_SEL = "div.media-heading a"


def scrape(*, pages, start, refetch, catch) -> None:
    platform.scrape(
        source=SOURCE,
        list_url=LIST_URL,
        pages=pages,
        start=start,
        refetch=refetch,
        catch=catch,
        container_sel=CONTAINER_SEL,
        title_link_sel=TITLE_LINK_SEL,
    )
