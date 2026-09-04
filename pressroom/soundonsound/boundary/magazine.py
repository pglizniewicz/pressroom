"""Scraper for soundonsound.com's audio-interface coverage.

A full run is several hours: this site's robots.txt asks for a 30-second crawl
delay and that is honoured. The articles land in page_cache, so that cost is
paid once and an interrupted run resumes free; the listings are walked on every
run, because that is where a new article shows up.

Usage:
  pressroom-soundonsound --limit 3                    # dry run, 3 articles
  pressroom-soundonsound --list-only                  # walk listings, store nothing
  pressroom-soundonsound --refetch                    # fetch every stored article again
  pressroom-soundonsound
"""

from pressroom.scraper.boundary import command
from pressroom.soundonsound.control import magazine


def main():
    command.run(
        magazine.scrape,
        __doc__,
        command.LIMIT,
        command.Option(
            "--pages",
            type=int,
            default=None,
            help="only this many listing pages per facet",
        ),
        command.Option(
            "--list-only",
            action="store_true",
            help="walk the listings and report the count, store nothing",
        ),
        command.REFETCH,
    )
