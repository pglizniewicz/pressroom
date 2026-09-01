"""Scraper for TerraTec's "Presse @ TerraTec" PHP-Nuke portals, English and
German.

Usage:
  pressroom-terratec-portal                           # both portals, every article
  pressroom-terratec-portal --limit 5                 # first 5 per portal (testing)
  pressroom-terratec-portal --offline                 # only catch up from page_cache
"""

from pressroom.scraping.boundary import command
from pressroom.sources.terratec.control import portal


def main():
    command.run(portal.scrape, __doc__, command.LIMIT)
