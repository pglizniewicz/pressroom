#!/usr/bin/env python3
"""Scraper for intc.com/news-events/press-releases → unified pressroom.db.

Usage:
  python scrape_intel.py                    # scrape all pages
  python scrape_intel.py --pages 5          # first 5 pages only
  python scrape_intel.py --start 10         # start from page 10
  python scrape_intel.py --start 10 --pages 20  # pages 10–29
"""


from q4 import make_arg_parser, scrape
from db import DB_PATH
import reextract

LIST_URL = "https://www.intc.com/news-events/press-releases"

if __name__ == "__main__":
    args = make_arg_parser("Scrape Intel IR press releases").parse_args()
    scrape(source="intel", list_url=LIST_URL, db_path=DB_PATH, pages=args.pages,
           start=args.start, catch=reextract.options(args))
