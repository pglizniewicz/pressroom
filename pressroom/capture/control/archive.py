#!/usr/bin/env python3
"""Generic Wayback Machine helpers, reusable by any dead-site scraper.

Everything that asks archive.org *what exists* goes through the public CDX
API (_cdx), and everything that asks for *content* goes through
fetch_snapshot, which caches it. The three CDX-backed queries are:

  - list_snapshots_by_prefix: every archived URL under a prefix.
  - list_all_captures: every HTTP-200 capture of one exact URL, for pages
    whose content grows over time so a single "latest" would miss revisions.
  - get_latest_working_snapshot: the newest capture of one URL that actually
    returned 200 - most captures of a dead site are 404s.

get_latest_working_snapshot used to drill the private __wb/sparkline and
__wb/calendarcaptures endpoints, which need a forged Referer to answer at all
and cost three requests per URL. CDX answers the same question in one, and
was verified to give identical timestamps on every stored row it was
compared against, including the awkward cases (newest captures are 404s;
only one capture exists; the newest servable capture is a revisit record).

HTTP 200 is necessary but not sufficient, though: it only proves archive.org
got an answer, not that the answer was the file being asked for. A capture can
be the origin server's own soft-404 served as 200, or - for a long-dead path -
a modern site answering 200 for it years later. get_latest_working_snapshot
cannot tell; fetch_first_matching_snapshot can, given a caller-supplied
validity check, by trying progressively older captures instead of stopping at
the newest.

Every HTTP attempt against archive.org - a CDX query or a content fetch - is
logged to db's wayback_calls table via call_log.record. This exists so
a question like "is CDX_TIMEOUT well-tuned?" can be answered from a query over
real traffic instead of a handful of manual curl calls in one session, which
is how CDX_TIMEOUT ended up tuned twice on thin evidence before this existed.
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

# Pause after a CDX query. archive.org's metadata endpoint - not content
# delivery - is what fails on us: across five long runs, 88 of 96 network errors
# were on /cdx/search/cdx (25 explicit HTTP 503s, 29 TCP-level refusals, the
# rest timeouts) against 8 on /web/<timestamp>id_/.
#
# Raising this to 3.5 was tried and measurably made things worse: the error rate
# per row went 10% -> 32% and throughput fell to ~0.5 rows/min. The reason is
# that these 503s are the service being globally overloaded, not per-client
# rate limiting keyed to our cadence - so waiting longer between our own
# requests buys no goodwill, it only keeps us inside the bad window longer.
# What the experiment did reveal is where the time actually went: ~120s per row,
# of which our sleeps were at most 10s and the rest was request timeouts.
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
# prefix query are not the same request - the bulk budget is the actual gain
# here, since those queries legitimately run long.
#
# The probe timeout is NOT a useful lever and was measured rather than guessed.
# Lowering it to 10s, then 25s, both turned slow successes into failures: CDX's
# latency for one and the same query shape swung between 0.7s and over 25s
# within minutes (15.5s, then a 503, then 19.8s, then two consecutive runs past
# 25s), with no value separating "doomed" from "slow but fine". If anything the
# evidence points the other way - many of the read timeouts in earlier logs were
# the 30s budget firing on requests that might have completed - so raising this
# would trade wall-clock for fewer `uncertain` rows. Left at the original 30
# pending that call.
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

    `kind` is passed explicitly by the caller ("cdx_probe" vs "cdx_bulk") for
    the wayback_calls log, rather than inferred from the `timeout` value: a
    future change to CDX_BULK_TIMEOUT's number shouldn't silently break which
    bucket a call gets logged under.
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
                # One long wait per call, not one per remaining attempt: if the
                # service is still overloaded after a full cooldown, grinding
                # here would cost every row several minutes to no purpose. Give
                # up instead and let the caller record it `uncertain` - a rerun
                # picks it up once archive.org is healthy, which is what the
                # uncertain/dead distinction is for.
                if waited_out_service:
                    raise
                waited_out_service = True
                # Announced, not silent: a multi-minute stall with no
                # explanation is exactly what the progress heartbeat exists to
                # prevent.
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
    """Return every archived URL under `prefix_url` as a list of dicts with
    keys original, mimetype, timestamp, endtimestamp, groupcount, uniqcount.
    """
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
    nothing to degrade to, and exiting non-zero is the honest signal (returning
    an empty list would print "0 candidates" and exit 0, which reads as
    success to anything wrapping the script).
    """
    try:
        return list_snapshots_by_prefix(prefix_url, **kwargs)
    except Exception as e:
        raise SystemExit(
            f"Could not list archived pages under {prefix_url}: {e}\n"
            "archive.org is unreachable or rate-limiting - try again later."
        )


def list_all_captures(exact_url: str, retries: int = 3) -> list[str]:
    """Return every historical HTTP-200 capture timestamp of one exact URL
    (no collapsing), for sites where the page's own content changes over
    time and a single "latest" snapshot would miss older revisions.
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
    """Fetch a Wayback snapshot URL's raw bytes (HTML or PDF), transparently
    caching them in the page_cache table on first fetch. A cache hit
    skips both the network call and the rate-limit sleep - only a real
    fetch needs to be polite to archive.org.

    On a real (non-cached) fetch, also records three encoding-diagnostic
    signals purely for later analysis - none of this affects the returned
    bytes or any parser's behavior:
      - id_content_type: the Content-Type header the `id_` response itself
        carried (only present when the original server declared a charset -
        often absent, see fw_guessed_charset below for a fallback signal).
      - fw_guessed_charset: the `x-archive-guessed-charset` header from the
        same capture's `fw_` variant (Wayback's own chardet-style guess -
        only fetched via a lightweight HEAD request, not a second full body).
      - bs4_encoding: what BeautifulSoup's own UnicodeDammit sniffing lands
        on with no override, for direct comparison against the two above.
    Best-effort only - any failure here is swallowed so it never affects the
    primary fetch.
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
    """Sample every historical HTTP-200 capture of `url` (a listing/dump
    page whose content grows over time - pressdb.php-style sources), calling
    `parse_fn(content, url, timestamp)` on each and returning the flat
    concatenation of everything parse_fn yields.

    This only owns the listing -> per-capture fetch -> parse loop; the
    caller does its own accumulation/dedup on the returned list, since that
    genuinely differs per source (dedup key url vs (title,date), plain
    body-length compare vs a custom rank() preferring one URL shape over
    another).
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
    """Try to fetch+parse a per-item detail page. Returns (parsed, confirmed):
    `parsed` is {} if nothing was recovered; `confirmed` distinguishes a
    verified dead end (safe to permanently record a fallback/no-op) from a
    network hiccup (caller must not commit anything this run, so the item
    stays open to a full retry next time instead of getting stuck forever).

    A probe failure backs off fetch.SLEEP*2 before returning, matching the
    older per-scraper convention this consolidates (several earlier
    fetch_detail() copies had silently dropped this pause). Note that is the
    *content* interval, not this module's CDX one - unchanged when SLEEP was
    raised, since the extra patience was aimed at the endpoint doing the
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

    Exists because CDX's statuscode:200 filter only proves the server answered
    200, not that it served the file being asked for. Two confirmed shapes of
    that gap: the origin server's own soft-404 served as HTTP 200 (a 380-byte
    "509 Bandwidth Limit Exceeded" page, in one case), and - for a URL whose
    real file is long gone - a modern, redesigned site answering 200 for the
    old path years later. get_latest_working_snapshot stops at the first
    (newest) such capture and never looks further.

    Also replaces get_latest_working_snapshot for callers that adopt this: it
    is built on list_all_captures, which already returns every HTTP-200
    timestamp including the newest, so no separate probe call is needed.

    Returns (content, timestamp, confirmed) - the same (thing, confirmed)
    contract as fetch_detail_snapshot. `confirmed` is True only when the
    non-match can be trusted: every historical capture was tried (at most
    `max_attempts`, newest first) and none validated, with no network error
    along the way. A search capped by max_attempts, or interrupted by a fetch
    or listing failure, returns confirmed=False instead - the caller must not
    treat that as a verified absence, since an untried older capture (or the
    one that errored) might have been the real file. What was tried is always
    logged, so a capped search is never mistaken for an exhaustive one.

    `is_valid` is caller-supplied on purpose - e.g. magic-byte sniffing for a
    PDF/DOC attachment - so this module stays ignorant of what any particular
    caller is looking for.
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
    """Find the most recent capture of `original_url` that returned HTTP 200.

    Returns (snapshot_url, timestamp) using the `id_` raw-content modifier, or
    None if the page never returned 200.

    `limit=-1` asks CDX for the last matching row, and it applies the filter
    before the limit - so a URL whose newest captures are 404s (common here:
    a page that later disappeared) still yields its newest *working* capture
    rather than nothing.
    """
    rows = _cdx(url=original_url, filter="statuscode:200", fl="timestamp", limit=-1)
    time.sleep(SLEEP)
    if not rows:
        return None
    timestamp = rows[0]["timestamp"]
    return address.snapshot_url(timestamp, original_url), timestamp
