#!/usr/bin/env python3
"""Scraper for ir.amd.com/news-events/press-releases → unified pressroom.db.

Usage:
  python scrape_amd.py                    # scrape all pages (~129)
  python scrape_amd.py --pages 3          # first 3 pages (verification)
  python scrape_amd.py --start 10         # start from page 10
  python scrape_amd.py --start 10 --pages 20  # pages 10–29
"""


from common import make_arg_parser, scrape
from db import DB_PATH

LIST_URL = "https://ir.amd.com/news-events/press-releases"

if __name__ == "__main__":
    args = make_arg_parser("Scrape AMD IR press releases").parse_args()
    scrape(
        source="amd",
        list_url=LIST_URL,
        db_path=DB_PATH,
        pages=args.pages,
        start=args.start,
        container_sel="div.media-body",
        title_link_sel="div.media-heading a",
    )
