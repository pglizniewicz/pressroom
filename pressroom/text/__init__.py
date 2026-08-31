"""Bytes and markup in, a release's text and its small HTML subset out.

Three decisions this component exists to keep in one place:

Decoding is always explicit. `requests` guesses Latin-1 and BeautifulSoup falls
back to chardet, which on this corpus has picked windows-1250 and even
windows-1258; a page that claims UTF-8 gets a per-byte cp1252 fallback, a
source known to be wholly pre-UTF-8 gets cp1252 outright.

Some of the damage is upstream - a server serving valid UTF-8 for a C1 control
where a trademark sign belongs - so decoding correctly is not enough and the
repair happens at the write, before the plain text is rendered from the markup.

Plain text is rendered from the cleaned markup, never from the source node, so
the indexed text is by definition what the displayed markup says.
"""
