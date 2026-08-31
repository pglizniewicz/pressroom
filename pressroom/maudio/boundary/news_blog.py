"""Scraper for M-Audio's m-audio.com "/news" blog.

Usage:
  pressroom-maudio-news                               # everything (~25 articles)
  pressroom-maudio-news --limit 5                     # cap articles (testing)
"""

from pressroom.maudio.control import news_blog
from pressroom.scraping.boundary import command


def main():
    command.run(news_blog.scrape, __doc__, command.LIMIT)
