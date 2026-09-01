"""How good a row's body is - the closed set, and nothing else.

A StrEnum, so every value that reaches SQLite is still the string the column has
always held and `grade == "teaser"` keeps working.

→ docs/adr/grade-and-detail-id.md
"""

from enum import StrEnum


class Grade(StrEnum):
    #: A real article body.
    FULL = "full"
    #: A listing blurb: the scraper could not reach the article. Retried on
    #: every run, which is why stored_grade() and not already_stored() is the
    #: question a scraper's loop asks.
    TEASER = "teaser"
    #: Title and date only, no body at all.
    STUB = "stub"


#: The grades a later run should still try to improve on.
UPGRADABLE = frozenset({Grade.TEASER, Grade.STUB})
