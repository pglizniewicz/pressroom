"""Capture addresses as strings: what one looks like, what it names, and how one
is built.

Pure string arithmetic - no connection, no network, no parser - so the write
path can validate an address without importing the archive client, and the
browser can read one without importing requests.
"""

import re

# The only thing body_origin may hold, spelled out here because this is where a
# bad value is caught: a Q4 numeric id, a Drupal node id or a bare row url
# raises at the write site rather than minting a dead link.
_CAPTURE_ADDRESS_RE = re.compile(r"^https?://web\.archive\.org/web/\d{14}id_/.+")


def is_capture_address(value) -> bool:
    return bool(value) and bool(_CAPTURE_ADDRESS_RE.match(str(value)))


TS_LEN = 14


def is_timestamp(value) -> bool:
    """Whether `value` is a Wayback capture timestamp (14 digits).

    The single implementation. This test used to be spelled out in seven places
    - two regexes, a GLOB, `len() == 14 and isdigit()` twice, a bare isdigit(),
    and a string compare in the browser - each of them deciding on its own what
    a `detail_id` meant. The readers no longer ask at all (body_origin records
    the capture, releases.grade records the verdict); what is left are the
    archive-facing scripts, which legitimately need to turn a timestamp into a
    page_cache key and now do it through here.
    """
    return bool(value) and str(value).isdigit() and len(str(value)) == TS_LEN


def snapshot_url(timestamp: str, original_url: str) -> str:
    """The capture address, and the key page_cache stores its bytes under. The
    `id_` marker asks archive.org for the original bytes without its own
    toolbar injected; strip it (browser/boundary/http.py) for a link meant for a
    human."""
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def capture_key(detail_id, url: str) -> str:
    """The page_cache key for a capture of this row's own url, or "" when the
    detail_id is not a capture timestamp at all (the live sources' platform ids,
    or no reference).

    The two tests above in the order every caller needs them. It is a
    *derivation* though, and only right when the capture is of the row's own
    url - prefer provenance's origin_key(), which asks the database first.
    """
    return snapshot_url(detail_id, url) if is_timestamp(detail_id) else ""
