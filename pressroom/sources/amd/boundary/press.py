"""AMD's press releases from ir.amd.com/news-events/press-releases.

Usage:
  pressroom-amd                              # every page (~129)
  pressroom-amd --pages 3                    # first 3 pages (verification)
  pressroom-amd --start 10                   # start from page 10
  pressroom-amd --start 10 --pages 20        # pages 10-29
  pressroom-amd --refetch                    # fetch every stored article again
"""

from pressroom.scraper.boundary import command
from pressroom.sources.amd.control import q4


def main():
    command.run(q4.scrape, __doc__, command.PAGES, command.START, command.REFETCH)
