"""How good a row's body is - the closed set, and nothing else.

A StrEnum rather than a plain class so `grade == "teaser"` and
`store_release(..., grade="teaser")` keep working unchanged: every value that
reaches SQLite is still the string the column has always held, and no call site
had to be converted for this to exist.

These three spent a long time inside `detail_id`, where a *verdict about the
body* sat in a field meant for a *reference to where the body came from*, and
seven places had to re-derive which of the two a given value was - six of them
by counting digits. Splitting them apart on 2026-08-22 is why this is a set of
three values and not a shape rule.
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
