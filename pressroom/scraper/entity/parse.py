"""What a scraper's parser hands back, independent of which scraper it is."""

from typing import Any, TypedDict

# One release as its own listing page describes it. A dict alias rather than a
# TypedDict because the keys are per-CMS: a `citation` on the 1996 anchor pages,
# a `section` on the magazine. What a consumer may rely on is what
# `catch_up.from_listings` reads - `body`, `body_html`, `origin_url`, `title` -
# and it reads all of them through `.get()`.
type Entry = dict[str, Any]


class Detail(TypedDict, total=False):
    """A parsed detail page. Every key is optional, and that is the contract:
    pressdb's detail pages carry no headline, media_news carries no date, and a
    parser that found no article at all returns `{}` rather than an empty body.
    `body_html` is None where a parser found no container. Read with `.get()`.
    """

    title: str
    date: str
    body: str
    body_html: str | None
