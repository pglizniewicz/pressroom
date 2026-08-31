"""AMD's press releases from ir.amd.com/news-events/press-releases.

Usage:
  pressroom-amd                              # every page (~129)
  pressroom-amd --pages 3                    # first 3 pages (verification)
  pressroom-amd --start 10                   # start from page 10
  pressroom-amd --start 10 --pages 20        # pages 10-29
"""

from pressroom.q4.control import platform
from pressroom.scraping.boundary import command

SOURCE = "amd"
LIST_URL = "https://ir.amd.com/news-events/press-releases"

# AMD's Q4 instance uses the Bootstrap media classes where Intel's uses the
# platform default, which is the only difference between the two sources.
CONTAINER_SEL = "div.media-body"
TITLE_LINK_SEL = "div.media-heading a"


def crawl(*, pages, start, catch):
    platform.scrape(source=SOURCE, list_url=LIST_URL,
                    pages=pages, start=start, catch=catch,
                    container_sel=CONTAINER_SEL, title_link_sel=TITLE_LINK_SEL)


def main():
    command.run(crawl, __doc__, command.PAGES, command.START)
