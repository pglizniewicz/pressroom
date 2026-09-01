"""Opening pressroom.db, in the two ways this repo allows.

Knows no table. That is the whole point of keeping it apart from creation.py:
an opener that names a table would have every reader reading the schema to find
out how to open a file.

Both ways set `row_factory = sqlite3.Row`, so a query may read its columns by
name. It is stdlib and it is not an ORM, a query builder or a row dataclass -
it is the same tuple with labels. Nothing had to change for it: a Row still
indexes positionally, slices to a tuple and unpacks, so the reads that predate
it keep working. Use the names for anything wider than two columns; a SELECT
read as row[0]..row[10] shifts silently the day a column is inserted, which is
what this repo already lost a body-origin query to.
"""

import os
import sqlite3
from pathlib import Path

from pressroom.database.control import creation

# The repo root, four levels up: connection.py / control / database / pressroom.
# The depth is load-bearing - moving this module without fixing the count gives
# a silently empty database somewhere else on disk rather than an error.
#
# PRESSROOM_DB overrides it, which is what makes a whole-corpus dry run against
# a copy possible: `--force` over 6700 rows is free but not reversible, and the
# alternative was moving a 500 MB file in and out of place twice.
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
    and installs FTS triggers. A browser has no business doing either, and
    ?mode=ro makes an accidental write an OperationalError from SQLite rather
    than a corrupted index nobody notices.

    check_same_thread stays at its default, so a handle that crosses threads
    raises instead of working by luck. It used to be False, for a browser that
    kept a connection on a threading.local; the browser now opens one per
    request and closes it in the same thread, and every other caller here is
    single-threaded.
    """
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
