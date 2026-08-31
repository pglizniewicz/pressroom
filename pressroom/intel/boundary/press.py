"""Intel's press releases from intc.com/news-events/press-releases.

Usage:
  pressroom-intel                            # every page
  pressroom-intel --pages 5                  # first 5 pages only
  pressroom-intel --start 10                 # start from page 10
  pressroom-intel --start 10 --pages 20      # pages 10-29
"""

from pressroom.q4.control import platform
from pressroom.scraping.boundary import command

SOURCE = "intel"
LIST_URL = "https://www.intc.com/news-events/press-releases"


def crawl(*, pages, start, catch):
    """The platform parser, pointed at Intel's listing. Intel runs the Q4
    default template, so it names no selectors."""
    platform.scrape(source=SOURCE, list_url=LIST_URL,
                    pages=pages, start=start, catch=catch)


def main():
    command.run(crawl, __doc__, command.PAGES, command.START)
