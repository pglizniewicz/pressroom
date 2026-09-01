#!/usr/bin/env python3
"""Generic Wayback Machine helpers, reusable by any dead-site scraper.

Everything that asks archive.org *what exists* goes through the public CDX API
(`_cdx`), never the private `__wb/` endpoints; everything that asks for
*content* goes through fetch_snapshot, which caches it.

HTTP 200 is necessary but not sufficient: it proves archive.org got an answer,
not that the answer was the file asked for. A capture can be the origin server's
own soft-404 served as 200, or a modern site answering 200 years later for a
long-dead path. get_latest_working_snapshot cannot tell, and
fetch_first_matching_snapshot can, given a caller-supplied validity check.

Every HTTP attempt here - a CDX query or a content fetch - is logged to
`wayback_calls`, so a question like "is CDX_TIMEOUT well tuned?" is answered
from real traffic rather than from a handful of manual curl calls. That is how
these constants got mistuned before the log existed.
"""

import sqlite3
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from pressroom.capture.entity import call_log
from pressroom.database.control import connection
from pressroom.capture.entity import page
from pressroom.capture.control import address
from pressroom.capture.control.politeness import HEADERS, SLEEP as CONTENT_SLEEP
from pressroom.reporting.entity import outcome

CDX_URL = "https://web.archive.org/cdx/search/cdx"

# Pause after a CDX query. It is archive.org's metadata endpoint, not content
# delivery, that fails on us - nearly every network error in a long run is a CDX
# one.
#
# Raising this was tried and measurably made things worse: these 503s are the
# service being globally overloaded, not per-client rate limiting keyed to our
# cadence, so waiting longer between our own requests buys no goodwill and only
# keeps us inside the bad window longer. Query `wayback_calls` before touching
# it.
SLEEP = 1.0

# Base for the retry backoff after a connection-level CDX failure (-> 5s, 10s).
# Kept separate from SLEEP on purpose: one is how polite we are when things
# work, the other is how long we wait when they don't, and tying the second to
# the first meant every increase in politeness silently inflated retry waits.
RETRY_BACKOFF = 5.0

# HTTP 503 is not "retry me", it is "the service is overloaded" - and since the
# overload is global, the next row would hit it too. So a 503 arms one cooldown
# shared by the whole run instead of each row serving its own multi-minute
# sentence: the first caller announces and waits it out, and any caller that
# arrives during it waits only the remainder.
SERVICE_COOLDOWN = 120.0
_cooldown_until = 0.0

# Request timeouts, split because a single-row probe and a several-hundred-row
# prefix query are not the same request; the bulk budget is the gain here.
#
# The probe timeout is not a useful lever, and that was measured rather than
# guessed: lowering it turned slow successes into failures, because CDX's
# latency for one query shape swings by an order of magnitude within minutes
# with no value separating "doomed" from "slow but fine".
CDX_TIMEOUT = 30
CDX_BULK_TIMEOUT = 60


# Lazy, module-owned connection used only to log wayback_calls rows. Not
# threaded in from callers: get_latest_working_snapshot and friends are called
# from a dozen-plus sites with no db connection in scope (it is a pure
# function of a url), and stats logging is a diagnostic side channel that
# shouldn't force every one of those call sites to thread one through just for
# this. A second sqlite3.Connection to the same file is safe here: this repo
# is single-threaded and sequential, so two connections never write at the
# same instant.
_stats_conn = None


def _stats_connection() -> sqlite3.Connection:
    global _stats_conn
    if _stats_conn is None:
        _stats_conn = connection.connect()
    return _stats_conn


def _classify_error(e: requests.exceptions.RequestException) -> str:
    """A wayback_calls `outcome` string for a failed request - the HTTP status
    if the server answered at all, else what kind of connection failure it was.
    """
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status is not None:
        return str(status)
    if isinstance(e, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "connection_error"
    return "other"


def _service_cooldown() -> None:
    """Wait out a cooldown armed by an earlier 503, if one is still running."""
    remaining = _cooldown_until - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def _cdx(
    retries: int = 3, timeout: int = CDX_TIMEOUT, kind: str = "cdx_probe", **params
) -> list[dict[str, str]]:
    """One CDX query -> a list of row dicts (the header row becomes the keys).

    Failures are not all the same and are not treated the same: a refused or
    timed-out connection is worth retrying shortly, while an HTTP 503 means the
    service itself is overloaded and arms SERVICE_COOLDOWN for every caller.

    `kind` is passed explicitly ("cdx_probe" vs "cdx_bulk") rather than inferred
    from `timeout`, so changing a timeout cannot silently move a call into
    another bucket of the wayback_calls log.
    """
    global _cooldown_until

    params.setdefault("output", "json")
    waited_out_service = False
    stats_conn = _stats_connection()

    for attempt in range(retries):
        _service_cooldown()
        t0 = time.monotonic()
        try:
            r = requests.get(CDX_URL, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            call_log.record(
                stats_conn,
                kind=kind,
                url=params.get("url"),
                attempt=attempt,
                timeout_budget=timeout,
                outcome="ok",
                duration=time.monotonic() - t0,
            )
            break
        except requests.exceptions.RequestException as e:
            call_log.record(
                stats_conn,
                kind=kind,
                url=params.get("url"),
                attempt=attempt,
                timeout_budget=timeout,
                outcome=_classify_error(e),
                duration=time.monotonic() - t0,
            )
            if attempt == retries - 1:
                raise
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (503, 429):
                # One long wait per call, not one per remaining attempt: still
                # overloaded after a full cooldown means give up and let the
                # caller record `uncertain`, which a rerun picks up.
                if waited_out_service:
                    raise
                waited_out_service = True
                # Announced, not silent: a multi-minute stall with no
                # explanation is what the heartbeat exists to prevent.
                print(
                    f"\n  CDX returned {status}; pausing {SERVICE_COOLDOWN:.0f}s "
                    "for the service to recover",
                    flush=True,
                )
                _cooldown_until = time.monotonic() + SERVICE_COOLDOWN
                _service_cooldown()
            else:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    rows = r.json()
    if not rows:
        return []
    header, *data = rows
    return [dict(zip(header, row)) for row in data]


def list_snapshots_by_prefix(
    prefix_url: str, limit: int = 10000, retries: int = 3
) -> list[dict[str, str]]:
    """Every archived url under `prefix_url`, one dict per url."""
    return _cdx(
        retries=retries,
        timeout=CDX_BULK_TIMEOUT,
        kind="cdx_bulk",
        url=prefix_url,
        matchType="prefix",
        collapse="urlkey",
        fl="original,mimetype,timestamp,endtimestamp,groupcount,uniqcount",
        filter="!statuscode:[45]..",
        limit=limit,
    )


def list_snapshots_or_exit(prefix_url: str, **kwargs) -> list[dict[str, str]]:
    """list_snapshots_by_prefix, but a failure after its retries ends the run
    with a one-line message instead of a urllib3 traceback.

    For the scrapers whose entire work list comes from this one call: there is
    nothing to degrade to, and an empty list would print "0 candidates" and exit
    0, which reads as success.
    """
    try:
        return list_snapshots_by_prefix(prefix_url, **kwargs)
    except Exception as e:
        raise SystemExit(
            f"Could not list archived pages under {prefix_url}: {e}\n"
            "archive.org is unreachable or rate-limiting - try again later."
        )


def list_all_captures(exact_url: str, retries: int = 3) -> list[str]:
    """Every HTTP-200 capture timestamp of one exact url, uncollapsed - for a
    page whose content changes over time, where a single "latest" would miss
    older revisions.
    """
    rows = _cdx(
        retries=retries,
        timeout=CDX_BULK_TIMEOUT,
        kind="cdx_bulk",
        url=exact_url,
        filter="statuscode:200",
        fl="timestamp",
        limit=1000,
    )
    return sorted({row["timestamp"] for row in rows})


def fetch_snapshot(
    conn: sqlite3.Connection, session: requests.Session, url: str, timeout: int = 20
) -> bytes:
    """A Wayback snapshot's raw bytes (HTML or PDF), cached on first fetch.

    A cache hit skips both the network call and the rate-limit sleep: only a
    real fetch has to be polite to archive.org.

    A real fetch also stores three encoding signals for later analysis - the
    `id_` response's own Content-Type, the `fw_` variant's
    `x-archive-guessed-charset`, and what BeautifulSoup's sniffing would land
    on. Diagnostics only: best-effort, swallowed on failure, and read by nothing
    on the parse path.
    """
    row = conn.execute(
        "SELECT content FROM page_cache WHERE url = ?", (url,)
    ).fetchone()
    if row:
        return row[0]

    t0 = time.monotonic()
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        call_log.record(
            conn,
            kind="content",
            url=url,
            attempt=0,
            timeout_budget=timeout,
            outcome=_classify_error(e),
            duration=time.monotonic() - t0,
        )
        raise
    call_log.record(
        conn,
        kind="content",
        url=url,
        attempt=0,
        timeout_budget=timeout,
        outcome="ok",
        duration=time.monotonic() - t0,
    )
    content = r.content

    id_content_type = r.headers.get("Content-Type")

    fw_guessed_charset = None
    try:
        if "id_/" in url:
            fw_url = url.replace("id_/", "fw_/", 1)
            r_fw = session.head(fw_url, headers=HEADERS, timeout=timeout)
            fw_guessed_charset = r_fw.headers.get("x-archive-guessed-charset")
    except Exception:
        pass

    bs4_encoding = None
    try:
        # Only sniff text. On a PDF or Word attachment the answer would be
        # meaningless anyway, and UnicodeDammit prints "Some characters could
        # not be decoded..." straight into the middle of the progress markers.
        if not content[:8].startswith((b"%PDF", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")):
            bs4_encoding = BeautifulSoup(content, "html.parser").original_encoding
    except Exception:
        pass

    conn.execute(
        "INSERT OR IGNORE INTO page_cache (url, content, id_content_type, "
        "fw_guessed_charset, bs4_encoding, content_sha256, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            url,
            content,
            id_content_type,
            fw_guessed_charset,
            bs4_encoding,
            page.content_hash(content),
            time.time(),
        ),
    )
    conn.commit()
    time.sleep(CONTENT_SLEEP)
    return content


def sample_all_captures(
    conn: sqlite3.Connection,
    session: requests.Session,
    url: str,
    parse_fn,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Every historical HTTP-200 capture of `url` - a listing page whose content
    grows over time - parsed through `parse_fn(content, url, timestamp)` and
    returned as one flat list.

    Owns the listing -> fetch -> parse loop and nothing above it: the dedup that
    picks the best of several captures is per-source, so the caller does it.
    """
    try:
        timestamps = list_all_captures(url)
    except Exception as e:
        print(f"  ERROR listing captures: {e}")
        return []
    if limit:
        timestamps = timestamps[:limit]
    print(f"  {len(timestamps)} captures to sample", flush=True)

    entries = []
    for ts in timestamps:
        snap_url = address.snapshot_url(ts, url)
        try:
            content = fetch_snapshot(conn, session, snap_url, timeout=20)
            entries.extend(parse_fn(content, url, ts))
        except Exception as e:
            print(f"\n  ERROR fetching {snap_url}: {e}")
            continue
        print(outcome.CAPTURE, end="", flush=True)
    return entries


def fetch_detail_snapshot(
    conn: sqlite3.Connection,
    session: requests.Session,
    url: str,
    parse_fn,
    timeout: int = 20,
):
    """Fetch and parse a per-item detail page -> (parsed, confirmed).

    `parsed` is {} when nothing was recovered, and `confirmed` says whether that
    is a verdict: a verified dead end may be recorded permanently, a network
    hiccup must leave the item open to a full retry. A recovered `parsed`
    carries `detail_id` and `origin_url`, so the caller can write the body and
    its provenance in one transaction.

    A probe failure backs off twice the *content* interval - not this module's
    CDX one, because the extra patience is aimed at the endpoint doing the
    rate-limiting.
    """
    try:
        found = get_latest_working_snapshot(url)
    except Exception as e:
        print(f"\n  ERROR probing snapshots for {url}: {e}")
        time.sleep(CONTENT_SLEEP * 2)
        return {}, False
    if not found:
        return {}, True
    snap_url, ts = found
    try:
        content = fetch_snapshot(conn, session, snap_url, timeout=timeout)
        parsed = parse_fn(content)
    except Exception as e:
        print(f"\n  ERROR fetching {snap_url}: {e}")
        return {}, False
    if parsed.get("body"):
        parsed["detail_id"] = ts
        # The address, not just the timestamp: a caller that never sees
        # `snap_url` cannot record where the body came from.
        parsed["origin_url"] = snap_url
        return parsed, True
    return {}, True


def fetch_first_matching_snapshot(
    conn: sqlite3.Connection,
    session: requests.Session,
    url: str,
    is_valid,
    max_attempts: int = 6,
    timeout: int = 20,
):
    """Try archived captures of `url` newest-first, returning the first whose
    bytes satisfy `is_valid(content)`.

    Exists because a `statuscode:200` capture is not necessarily a capture of
    the file asked for - see this module's docstring - and
    get_latest_working_snapshot stops at the newest one regardless.

    Returns (content, timestamp, confirmed), the same contract as
    fetch_detail_snapshot. `confirmed` is True only when the non-match can be
    trusted: every capture was tried and none validated, with no network error
    along the way. A search capped by `max_attempts` or interrupted by a failure
    returns confirmed=False, because an untried older capture might have been the
    real file. What was tried is always printed, so a capped search cannot be
    mistaken for an exhaustive one.

    `is_valid` is caller-supplied, so this module stays ignorant of what any
    particular caller is looking for.
    """
    try:
        timestamps = list_all_captures(url)
    except Exception as e:
        print(f"\n  ERROR listing captures for {url}: {e}")
        return None, None, False

    if not timestamps:
        return None, None, True  # never had any HTTP-200 capture at all

    newest_first = list(reversed(timestamps))
    exhaustive = len(newest_first) <= max_attempts
    candidates = newest_first[:max_attempts]

    had_error = False
    tried = []
    for ts in candidates:
        snap_url = address.snapshot_url(ts, url)
        try:
            content = fetch_snapshot(conn, session, snap_url, timeout=timeout)
        except Exception as e:
            print(f"\n  ERROR fetching {snap_url}: {e}")
            had_error = True
            tried.append(f"{ts}:error")
            continue
        if is_valid(content):
            return content, ts, True
        tried.append(f"{ts}:no-match")

    scope = "all" if exhaustive else f"newest {max_attempts} of {len(timestamps)}"
    print(f"\n  no matching capture for {url} - tried {scope}: {', '.join(tried)}")
    return None, None, exhaustive and not had_error


def get_latest_working_snapshot(original_url: str):
    """(snapshot_url, timestamp) for the newest capture of `original_url` that
    returned HTTP 200, through the `id_` raw-content modifier, or None.

    `limit=-1` asks CDX for the last matching row, and it applies the filter
    before the limit - so a url whose newest captures are 404s, which is the
    common shape here, still yields its newest *working* capture.
    """
    rows = _cdx(url=original_url, filter="statuscode:200", fl="timestamp", limit=-1)
    time.sleep(SLEEP)
    if not rows:
        return None
    timestamp = rows[0]["timestamp"]
    return address.snapshot_url(timestamp, original_url), timestamp
