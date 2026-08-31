"""Scraper for Midiman/M-Audio's dead 2001-era press room - the GoLive-generated
static pages mirrored across midiman.net, midiman.com and m-audio.com.

Usage:
  pressroom-maudio-golive                             # 5 index pages + prefix crawl
  pressroom-maudio-golive --limit 5                   # first 5 candidates (testing)
  pressroom-maudio-golive --no-prefix-crawl           # index-page candidates only
"""

from pressroom.maudio.control import golive
from pressroom.scraping.boundary import command


def main():
    command.run(
        golive.scrape,
        __doc__,
        command.LIMIT,
        command.Option(
            "--no-prefix-crawl",
            dest="prefix_crawl",
            action="store_false",
            help="skip prefix-crawling the press/ folders; use only "
            "the 5 known index pages",
        ),
    )
