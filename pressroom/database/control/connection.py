"""Opening pressroom.db, in the two ways this repo allows.

References no table, so a reader does not have to read the schema to find out how to
open a file.

Both ways set `row_factory = sqlite3.Row`, so a query may read its columns by
name - use the names for anything wider than two columns, because a SELECT read
as row[0]..row[10] shifts silently the day a column is inserted.
"""

import os
import sqlite3
from pathlib import Path

from pressroom.database.control import creation

# The repo root, four levels up: connection.py / control / database / pressroom.
# The depth matters - moving this module without fixing the count gives
# a silently empty database somewhere else on disk rather than an error.
#
# PRESSROOM_DB overrides it, which makes a whole-corpus dry run against a copy
# possible: a `--force` costs no request but cannot be undone.
_DEFAULT = Path(__file__).resolve().parents[3] / "pressroom.db"
DB_PATH = Path(os.environ.get("PRESSROOM_DB") or _DEFAULT)


def connect(db_path=None) -> sqlite3.Connection:
    """Open pressroom.db with the schema ensured."""
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    creation.init_db(conn)
    return conn


def connect_ro(db_path=None) -> sqlite3.Connection:
    """Open pressroom.db read-only, for a reader that must not be able to
    change it.

    Deliberately NOT connect(): that calls init_db(), which runs CREATE TABLE
    and installs FTS triggers. A browser must do neither, and
    ?mode=ro makes an accidental write an OperationalError from SQLite rather
    than a corrupted index nobody notices.

    check_same_thread stays at its default, so a handle that crosses threads
    raises instead of happening to work: the browser opens one connection per
    request and closes it in the same thread.
    """
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
