"""Scraper for TerraTec's earliest press page (terratec.de, 1996-1997).

Usage:
  pressroom-terratec-early
"""

from pressroom.scraper.boundary import command
from pressroom.terratec.control import early


def main():
    command.run(early.scrape, __doc__)
