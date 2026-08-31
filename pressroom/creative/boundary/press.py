"""Scraper for sg.creative.com/corporate/pressroom -> unified pressroom.db.

Usage:
  pressroom-creative                                  # all years, 1999-current
  pressroom-creative --from-year 2020                 # 2020 onward
  pressroom-creative --from-year 2020 --to-year 2022
"""

from pressroom.creative.control import press
from pressroom.scraping.boundary import command


def main():
    command.run(
        press.scrape, __doc__,
        command.Option("--from-year", type=int, default=press.FIRST_YEAR,
                       help=f"start year (default: {press.FIRST_YEAR})"),
        command.Option("--to-year", type=int, default=None,
                       help="end year (default: current year)"),
    )
