"""Scraper for TerraTec's 2007-2013 CMS-era press site
(terratec.net/en/company/press/ and /de/unternehmen/presse/).

One language per run: the two sites are separate source tags because the text
genuinely differs, so the language is a required argument rather than a flag
with a default.

Usage:
  pressroom-terratec-cms en
  pressroom-terratec-cms de
"""

from pressroom.scraper.boundary import command
from pressroom.sources.terratec.control import cms


def main():
    command.run(
        cms.scrape_lang,
        __doc__,
        command.Option(
            "lang", choices=["en", "de"], help="which of the two sites to scrape"
        ),
        command.LIMIT,
    )
