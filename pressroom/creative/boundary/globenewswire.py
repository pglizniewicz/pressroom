"""Scraper for GlobeNewswire's "Creative Labs, Inc." organization archive.

Usage:
  pressroom-creative-gnw                              # all pages
  pressroom-creative-gnw --pages 2                    # first 2 pages only
"""

from pressroom.creative.control import globenewswire
from pressroom.scraping.boundary import command


def main():
    command.run(globenewswire.scrape, __doc__, command.PAGES)
