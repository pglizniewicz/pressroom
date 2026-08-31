"""Scraper for the Midiman/M-Audio "News" section (index.php?do=media.news),
across all four domains that ran it.

Usage:
  pressroom-maudio-media-news                         # all four domains
  pressroom-maudio-media-news --limit 5               # cap captures/IDs (testing)
  pressroom-maudio-media-news --no-prefix-crawl       # listing-derived entries only
  pressroom-maudio-media-news --source maudio_com_media_news
"""

from pressroom.maudio.control import media_news
from pressroom.scraping.boundary import command


def main():
    command.run(
        media_news.scrape, __doc__,
        command.LIMIT,
        command.Option("--no-prefix-crawl", dest="prefix_crawl",
                       action="store_false",
                       help="skip the ID= prefix-crawl discovery"),
        command.Option("--source", dest="sources", action="append",
                       choices=sorted(media_news.DOMAINS),
                       help="only this source; repeatable "
                            "(default: all four domains)"),
    )
