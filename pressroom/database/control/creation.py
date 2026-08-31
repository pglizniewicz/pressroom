"""Creating pressroom.db's schema, in one fixed order.

Owns no DDL: each table's CREATE belongs to the component whose responsibility
that table is, and this module only decides *when* each runs. `_OWNERS` is
homogeneous - a module in it supplies `SCHEMA_SQL` and nothing else - so adding
a table is one thing to write and a missing one is a loud AttributeError at
startup rather than a silently skipped step.

Two orderings are load-bearing. Every CREATE is `IF NOT EXISTS`, so a table
that already exists is left exactly as it is; an index therefore goes in its
owner's own SCHEMA_SQL, after the CREATE TABLE it reads. And the FTS triggers
go in LAST, because all three reference `releases`.

No conn.commit() here, and none is missing: executescript commits any pending
transaction before it runs and leaves none open, so DDL under IF NOT EXISTS is
already self-committing and individually idempotent. The `with conn:` rule is
about a *write* of two statements that must roll back together; wrapping this
in one would be theatre.

Idempotent throughout: every scraper calls init_db() once at startup, and
capture/control/archive.py opens a second connection mid-run and calls it again.

There are no migrations. This file is the whole of what happens to the schema.
"""

from pressroom.capture.entity import call_log, page
from pressroom.provenance.entity import origin
from pressroom.release.control import index
from pressroom.release.entity import schema

# Every table, in the order the tables were introduced. Executed as one script
# so a fresh database arrives complete.
_OWNERS = (schema, page, call_log, origin)


def init_db(conn) -> None:
    """Create the schema if absent, then install the FTS triggers."""
    conn.executescript("\n".join(o.SCHEMA_SQL for o in _OWNERS))
    index.install_fts_triggers(conn)
