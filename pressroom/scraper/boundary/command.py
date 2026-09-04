"""The command line every scraper has, assembled once.

What a boundary module owes `run()`: a crawl callable, its own `__doc__`, and
zero or more Options. What it gets back: the catch-up flags for free, and no
argparse of its own. The per-scraper differences - a year range, a language, a
discovery channel that can be switched off - are declared here as values rather
than re-implemented as argparse calls.

The crawl is always called with `catch=` plus one keyword per declared Option,
so an Option's `dest` is part of the crawl's signature. That coupling is the
point: the flag and the parameter it feeds share a name.
"""

import argparse

from pressroom.scraper.control import catch_up


class Option:
    """One per-scraper command-line option, as a value rather than a call.

    Everything argparse accepts is passed straight through, so a scraper that
    needs something unusual declares it here rather than reaching for its own
    parser.
    """

    def __init__(self, *flags, **kwargs):
        self.flags = flags
        self.kwargs = kwargs
        explicit = kwargs.get("dest")
        # argparse's own rule, spelled out because `dest` is what the crawl
        # receives: the first long flag wins, dashes become underscores, and a
        # positional is already a name.
        self.dest = explicit or next(
            (f.lstrip("-").replace("-", "_") for f in flags if f.startswith("--")),
            flags[0].lstrip("-").replace("-", "_"),
        )

    def add_to(self, parser) -> None:
        parser.add_argument(*self.flags, **self.kwargs)


#: Cap the work for a test run. The option nearly every scraper takes.
LIMIT = Option(
    "--limit",
    type=int,
    default=None,
    help="cap the number of items processed (testing)",
)

#: Q4's two: the platform paginates and a run can resume at a page.
PAGES = Option(
    "--pages",
    type=int,
    default=None,
    help="number of listing pages to scrape (default: all)",
)
START = Option("--start", type=int, default=1, help="start from this page number")

#: The live sources' one: an article on a site that is still up can change.
REFETCH = Option(
    "--refetch",
    action="store_true",
    help="fetch every stored article of this source again, cached or not, "
    "and re-extract it (asks first, like --force)",
)


def _summary(doc: str | None) -> str:
    """A module docstring's first paragraph, as one line.

    The boundary modules pass `__doc__` whole rather than slicing it, because
    slicing it wrong is silent: `splitlines()[0]` drops the second half of every
    two-line summary, and four of these sources have one.
    """
    head = (doc or "").strip().split("\n\n", 1)[0]
    return " ".join(head.split())


def run(crawl, doc: str | None, *options) -> None:
    """Parse this scraper's command line and hand it to `crawl`.

    `doc` is the boundary module's `__doc__`: its first paragraph becomes the
    --help description, and the Usage block below that is documentation for a
    reader of the file.
    """
    parser = argparse.ArgumentParser(description=_summary(doc))
    for option in options:
        option.add_to(parser)
    catch_up.add_flags(parser)
    args = parser.parse_args()
    crawl(
        catch=catch_up.options(args), **{o.dest: getattr(args, o.dest) for o in options}
    )
