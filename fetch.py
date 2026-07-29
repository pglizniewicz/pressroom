#!/usr/bin/env python3
"""Shared HTTP client policy: how we identify ourselves, and how fast we
hammer other people's servers.

Every scraper and wayback.py imports these, which is why they live apart from
any one scraper's logic. Two constants is a small module, but the alternative
was a `common.py` whose name promised generality while 85% of it was a
scraper for one specific CMS.
"""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
}

# Pause between content fetches. archive.org in particular starts refusing
# connections without it, and a scrape of a dead site is never urgent.
SLEEP = 1.5
