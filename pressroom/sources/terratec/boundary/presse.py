"""Scraper for TerraTec's German press office, terratec.de/presse - and the
gaps its newer sibling left behind.

Usage:
  pressroom-terratec-de
  pressroom-terratec-de --limit 5                     # cap candidates (testing)
  pressroom-terratec-de --offline                     # only catch up from cache
"""

from pressroom.scraping.boundary import command
from pressroom.sources.terratec.control import presse


def main():
    command.run(presse.scrape, __doc__, command.LIMIT)
