"""Scraper for TerraTec's dead press room (terratec.net/press/pressemit/).

Usage:
  pressroom-terratec                                  # every archived press page
  pressroom-terratec --limit 5                        # first 5 pages (testing)
"""

from pressroom.scraping.boundary import command
from pressroom.sources.terratec.control import pressemit


def main():
    command.run(pressemit.scrape, __doc__, command.LIMIT)
