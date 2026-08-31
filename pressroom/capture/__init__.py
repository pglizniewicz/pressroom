"""The bytes: what was fetched, from archive.org or from a site still up, and
what it cost to ask.

Every fetched page's raw bytes are cached in the database, which is what makes
a parser fix free - reparsing costs nothing and refetching is a mistake.
Fetching a page any other way is a bug.

Bytes are handed out as bytes, never as decoded text: these sites are pre-UTF-8
or half-converted, so the decoding decision belongs to the caller that knows
which generation of markup it is reading.

Every HTTP attempt against archive.org is logged, because tuning a timeout from
a handful of manual curl calls is how those constants got mistuned twice.
"""
