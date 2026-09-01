"""Scraper for Midiman/M-Audio's 2004-2013 "media_pr" era
(index.php?do=media.media_pr on midiman.net, midiman.com and m-audio.com).

Usage:
  pressroom-maudio-media-pr                           # everything
  pressroom-maudio-media-pr --limit 3                 # 3 captures per domain (testing)
  pressroom-maudio-media-pr --attachments             # also crawl for .doc/.pdf bytes
"""

from pressroom.sources.maudio.control import media_pr
from pressroom.scraping.boundary import command


def main():
    command.run(media_pr.scrape, __doc__, command.LIMIT)
