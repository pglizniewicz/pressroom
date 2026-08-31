"""Creative Technology, over two channels.

Its own press room is a live site paginated by year rather than by page number.
Some releases from 2011-2021 went out through GlobeNewswire instead, and that
wire needs a browser TLS fingerprint: Akamai drops requests/urllib3 on the
handshake, so browser-shaped headers change nothing. The two channels overlap
and are deliberately not deduplicated against each other.
"""
