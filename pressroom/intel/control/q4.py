"""Intel's press releases, on the Q4 Inc. investor-relations platform.

The crawler and the parser are `q4.control.platform`'s, shared with AMD; what is
Intel's is the listing url, and nothing else: Intel runs the platform's default
template, so it names no selectors.
"""

from pressroom.q4.control import platform

SOURCE = "intel"
LIST_URL = "https://www.intc.com/news-events/press-releases"


def scrape(*, pages, start, refetch, catch) -> None:
    platform.scrape(
        source=SOURCE,
        list_url=LIST_URL,
        pages=pages,
        start=start,
        refetch=refetch,
        catch=catch,
    )
