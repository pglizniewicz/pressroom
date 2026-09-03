"""Scraper for TerraTec's dead press room, the hand-built `pressemit` template
on terratec.net (English, French) and terratec.de (German).

Usage:
  pressroom-terratec                                  # both hosts, every page
  pressroom-terratec --limit 5                        # first 5 per host (testing)
  pressroom-terratec --offline                        # only catch up from cache
  pressroom-terratec --site de                        # one host only
"""

from pressroom.scraper.boundary import command
from pressroom.sources.terratec.control import pressemit


def main():
    command.run(
        pressemit.scrape,
        __doc__,
        command.LIMIT,
        command.Option(
            "--site",
            dest="sites",
            action="append",
            choices=sorted(pressemit.SITES),
            help="only this host; repeatable (default: both). Not --source: "
            "both hosts are one source tag",
        ),
    )
