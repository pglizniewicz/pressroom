#!/usr/bin/env python3
"""Date parsing shared by every scraper.

Each source spells its dates differently - "29.10.2002", "October 8, 2014",
"11/14/2005", "June 2007" - so every scraper wrapped dateutil in the same
try/except to turn whatever it scraped into an ISO string. That wrapper is
here instead.
"""

from dateutil import parser as du


def iso_date(
    value: str, *, dayfirst: bool = False, fuzzy: bool = False, fmt: str = "%Y-%m-%d"
) -> str:
    """Parse `value` into an ISO date string, or "" if it doesn't parse.

    Returning "" rather than raising is deliberate: a release whose date can't
    be read is still worth storing, which is why `releases.date` is a plain
    TEXT column with no format constraint.

    dayfirst=True for the European DD.MM.YYYY sources (dateutil would
    otherwise read 06.05.2002 as June 5th). fuzzy=True to let dateutil skip
    surrounding prose. fmt="%Y-%m" for sources that only carry month
    precision - a few TerraTec articles are dated just "June 2007".

    A zone name dateutil does not know ("09:00 ET" on every GlobeNewswire
    listing) is accepted through `tzinfos` and never converted: a release's
    date is the calendar day the publisher printed. Without it dateutil warns
    today and will raise in a future version.
    """
    if not value:
        return ""
    try:
        return du.parse(
            value, dayfirst=dayfirst, fuzzy=fuzzy, tzinfos=_any_zone
        ).strftime(fmt)
    except Exception:
        return ""


def _any_zone(_name: str, offset) -> int:
    """Every zone name is a zone: the offset dateutil parsed if it gave one,
    else none. The date is read as written either way."""
    return offset or 0
