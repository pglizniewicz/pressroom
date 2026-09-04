"""Creating pressroom.db's schema, in one fixed order.

Owns no DDL: each table's CREATE belongs to the component whose responsibility
that table is, and this module only decides *when* each runs. `_OWNERS` is
homogeneous - a module in it supplies `SCHEMA_SQL` and nothing else - so adding
a table is one thing to write and a missing one is an AttributeError at
startup rather than a silently skipped step.

Two orderings matter. Every CREATE is `IF NOT EXISTS`, so a table
that already exists is left exactly as it is; an index therefore goes in its
owner's own SCHEMA_SQL, after the CREATE TABLE it reads. And the FTS triggers go
in LAST, because all three reference `releases`.

No conn.commit() here, and none is missing: executescript commits any pending
transaction before it runs and leaves none open, so DDL under IF NOT EXISTS is
self-committing and idempotent. It is called more than once per run.

There are no migrations. This file is the whole of what happens to the schema.
"""

from pressroom.fetcher.entity import call_log, page
from pressroom.provenance.entity import origin
from pressroom.release.control import index
from pressroom.release.entity import schema

# Executed as one script, so a fresh database is created complete.
_OWNERS = (schema, page, call_log, origin)


def init_db(conn) -> None:
    conn.executescript("\n".join(o.SCHEMA_SQL for o in _OWNERS))
    index.install_fts_triggers(conn)
