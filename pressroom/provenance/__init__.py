"""Which capture a row's body was actually read out of.

A timestamp does not say which page it is a capture of. A scraper that reads a
release out of a *listing* stores that listing's timestamp, and an archive link
built from the row's own url is then an address that never existed. This
component records the whole capture address instead, next to the body write and
in the same transaction, so a new row arrives with its link already correct.

Absence has to keep meaning "no archive link for this row", which is why it is
its own table and not a column: a live source has no entry and therefore no
link, which is the right answer.

An entry must be dropped the moment it stops being true - any pass that
rewrites a body from somewhere else either records the new address or clears
the old one.
"""
