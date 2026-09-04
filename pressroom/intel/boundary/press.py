"""Intel's press releases from intc.com/news-events/press-releases.

Usage:
  pressroom-intel                            # every page
  pressroom-intel --pages 5                  # first 5 pages only
  pressroom-intel --start 10                 # start from page 10
  pressroom-intel --start 10 --pages 20      # pages 10-29
  pressroom-intel --refetch                  # fetch every stored article again
"""

from pressroom.scraper.control import command
from pressroom.intel.control import q4


def main():
    command.run(q4.scrape, __doc__, command.PAGES, command.START, command.REFETCH)
