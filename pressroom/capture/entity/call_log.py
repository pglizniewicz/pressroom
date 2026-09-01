"""`wayback_calls`: one row per HTTP attempt against archive.org.

Never read by a scraper. It exists so a timeout or a sleep constant is tuned
from a latency distribution and a 503 cluster rather than from a handful of
manual curl calls - which is how these constants got mistuned before it.
"""

import time

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS wayback_calls (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ts             REAL NOT NULL,  -- time.time() when the attempt started
        kind           TEXT NOT NULL,  -- 'cdx_probe' | 'cdx_bulk' | 'content'
        url            TEXT,
        attempt        INTEGER NOT NULL,  -- 0-based, within one call's retry loop
        timeout_budget REAL NOT NULL,
        outcome        TEXT NOT NULL,  -- 'ok' | '503' | '429' | 'timeout' | 'connection_error' | 'other'
        duration       REAL NOT NULL   -- seconds this one attempt took
    );
"""


def record(
    conn,
    *,
    kind: str,
    url: str,
    attempt: int,
    timeout_budget: float,
    outcome: str,
    duration: float,
) -> None:
    """Append one attempt, whatever it resulted in.

    Swallows its own failure by design. A connection that never went through
    init_db() - a bare sqlite3.connect, or a connect_ro() handle - has no
    wayback_calls to write to, and a side channel for later analysis must never
    be able to break an actual scrape. Do not turn this into a raise.
    """
    try:
        conn.execute(
            "INSERT INTO wayback_calls (ts, kind, url, attempt, timeout_budget, outcome, duration) "
            "VALUES (?,?,?,?,?,?,?)",
            (time.time(), kind, url, attempt, timeout_budget, outcome, duration),
        )
        conn.commit()
    except Exception:
        pass
