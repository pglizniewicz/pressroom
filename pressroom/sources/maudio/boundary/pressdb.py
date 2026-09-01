"""Scraper for Midiman/M-Audio's 2001-2003 "pressdb.php" era, on midiman.net
and midiman.com.

Usage:
  pressroom-maudio-pressdb                            # everything
  pressroom-maudio-pressdb --limit 3                  # 3 captures per domain (testing)
  pressroom-maudio-pressdb --attachments              # also crawl for .pdf bytes
"""

from pressroom.sources.maudio.control import pressdb
from pressroom.scraping.boundary import command


def main():
    command.run(pressdb.scrape, __doc__, command.LIMIT)
