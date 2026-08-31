"""Bringing an existing pressroom.db up to date, in one fixed order.

Owns no DDL either: each table's CREATE and each table's own column migrations
belong to the component whose responsibility that table is, and this module
only decides *when* each runs. Three orderings here are load-bearing and every
one of them cost a wrong database before it was written down:

  1. the renames run BEFORE the CREATE TABLEs. A rename guarded on "the target
     does not exist" never fires once the script has made an empty table beside
     the populated one.
  2. the column additions run AFTER them, and the indexes after the columns:
     CREATE INDEX on a column that is not there yet fails the whole init.
  3. the FTS triggers go in LAST, and repair-then-install stays one function -
     see release/control/index.py for what installing them early would do.

Idempotent throughout: every scraper calls init_db() once at startup.
"""

from pressroom.capture.entity import call_log, page
from pressroom.provenance.entity import origin
from pressroom.release.control import index
from pressroom.release.entity import schema

# Every table, in the order the tables were introduced. Executed as one script
# so a fresh database arrives complete.
_OWNERS = (schema, page, call_log, origin)


def init_db(conn) -> None:
    """Create the schema if absent, apply migrations, install FTS triggers."""
    for owner in _OWNERS:
        rename = getattr(owner, "rename_before_create", None)
        if rename:
            rename(conn)

    conn.executescript("\n".join(o.SCHEMA_SQL for o in _OWNERS))
    conn.commit()

    for owner in _OWNERS:
        migrate = getattr(owner, "migrate", None)
        if migrate:
            migrate(conn)

    index.sync_fts_triggers(conn)
