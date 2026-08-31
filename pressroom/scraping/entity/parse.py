"""What a source's parser hands back, independent of which source it is.

`Detail` is the whole-page parse: nine control modules produce one and
`scraping/control/catch_up.py` consumes it without knowing which. It replaces
`-> dict`, which said nothing about a shape ten parsers have to agree on.

**Every key is optional, and that is the contract, not laziness.** pressdb's
detail pages carry no headline, so its parser returns `body`/`body_html` only;
media_news has no date there; and every parser returns `{}` for "this capture
holds no article", which is what keeps a missing container from silently
becoming an empty body. Callers read it with `.get()` for exactly that reason -
`provenance/boundary/verification.py` does `parser(content).get("body") or ""`.

`body_html` is `str | None` because a parser that found no container leaves it
NULL on purpose, and `terratec/control/cms.py` returns that explicitly.

`Entry` is deliberately *not* a TypedDict, and that is a measurement rather
than a shortcut. Counted over the collectors, a listing entry is per-CMS -
eleven shapes over seventeen keys, from `citation`/`lang` on the 1996 anchor
pages to `section` on the magazine - so one TypedDict listing all of them would
be the grab-bag this tree splits components to avoid, and one listing a subset
would be a type a checker rejects the first time a parser adds its own key. So
the alias says the one true thing - a string-keyed mapping per release - and
where the shape is fixed enough to name, it gets named next to the parser that
emits it, same rule as that parser's markup notes.

What every collector *does* have to provide is what the consumer reads, and
`catch_up.from_listings` states it: `body`, `body_html`, `origin_url`, `title`,
all through `.get()`. Nothing else is required of an entry.
"""

from typing import TypedDict

# A listing entry: one release as its own listing page describes it. Loose on
# purpose - see the module docstring.
type Entry = dict[str, object]


class Detail(TypedDict, total=False):
    """A parsed detail page. See the module docstring for why nothing is
    required."""

    title: str
    date: str
    body: str
    body_html: str | None
