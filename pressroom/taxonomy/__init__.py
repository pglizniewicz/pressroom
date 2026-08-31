"""The second axis: which source tags are one firm.

A source tag identifies a scraper - a CMS generation - not a domain, which
makes it the right axis for debugging a scraper and the wrong one for reading
the corpus. Six firms cover the twenty-five tags.

An explicit table, never a prefix rule: an unmapped source lands in a visible
"unassigned" bucket rather than vanishing from the counts, because a source
that quietly disappeared from the company view would make every count on
screen a lie.
"""
