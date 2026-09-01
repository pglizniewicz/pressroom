"""Scraper for MIDIMAN/M-Audio's German site (midiman.de) press archive.

Usage:
  pressroom-maudio-de                                 # everything
  pressroom-maudio-de --limit 3                       # 3 captures per page (testing)
"""

from pressroom.sources.maudio.control import presse_de
from pressroom.scraping.boundary import command


def main():
    command.run(presse_de.scrape, __doc__, command.LIMIT)
