"""Opening pressroom.db, in the two ways this repo allows.

Knows no table. That is the whole point of keeping it apart from migration.py:
a reader can open the database without dragging in a single component that owns
a schema.
"""

import os
import sqlite3
from pathlib import Path

from pressroom.database.control import migration

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
    migration.init_db(conn)
    return conn


def connect_ro(db_path=None) -> sqlite3.Connection:
    """Open pressroom.db read-only, for a reader that must not be able to
    change it.

    Deliberately NOT connect(): that calls init_db(), which migrates and
    installs FTS triggers. A browser has no business doing either, and
    ?mode=ro makes an accidental write an OperationalError from SQLite rather
    than a corrupted index nobody notices. check_same_thread=False because the
    browser hands each request thread its own connection.
    """
    path = Path(db_path) if db_path else DB_PATH
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
