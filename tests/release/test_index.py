"""The three FTS invariants, each of which fails in total silence.

`'integrity-check'` passing is not evidence of a healthy index: it checks that
the index is internally consistent, not that it agrees with `releases`. So every
assertion here is an orphan/missing count or a token probe, never that pragma -
and one test asserts outright that the pragma is not enough, because a check
that cannot fail is what this repo has been burned by before.
"""

import sqlite3
import unittest

from pressroom.database.control import connection, migration
from pressroom.release.control import index, query, storage
from pressroom.release.entity import schema
from tests import support


def indexed(conn, token):
    return {
        r[0]
        for r in conn.execute(
            "SELECT rowid FROM releases_fts WHERE releases_fts MATCH ?", (token,)
        )
    }


def disagreements(conn):
    """(orphans, missing) - index rows with no content row, and the reverse."""
    orphans = conn.execute(
        "SELECT count(*) FROM releases_fts f"
        " LEFT JOIN releases r ON r.id = f.rowid WHERE r.id IS NULL"
    ).fetchone()[0]
    missing = conn.execute(
        "SELECT count(*) FROM releases r WHERE NOT EXISTS"
        " (SELECT 1 FROM releases_fts f WHERE f.rowid = r.id)"
    ).fetchone()[0]
    return orphans, missing


class ColumnOrderTest(support.DbCase):
    def test_snippet_ordinal_one_is_the_body(self):
        """Invariant 1. `snippet(releases_fts, 1, ...)` is a *positional*
        ordinal: swap the two columns in schema.SCHEMA_SQL and the reader starts
        snippeting titles, with no error anywhere."""
        self.seed("src", title="Kryptonite", body="Radium powers the device")
        got = query.search_releases(self.conn, "Radium")["results"]
        self.assertEqual(len(got), 1)
        self.assertIn("Radium", got[0]["excerpt"])
        self.assertNotIn("Kryptonite", got[0]["excerpt"])

    def test_the_declaration_still_says_title_then_body(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(releases_fts)")]
        self.assertEqual(cols[:2], ["title", "body"])


class TriggerTest(support.DbCase):
    def test_all_three_exist(self):
        """Invariant 2. Only releases_ai existed once, so every UPDATE-based
        recovery left its text unsearchable and every out-of-band DELETE left an
        orphan - which also skews bm25() for every other query."""
        got = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        self.assertEqual(got, {"releases_ai", "releases_au", "releases_ad"})

    def test_an_insert_is_findable(self):
        self.seed("src", body="MobilePre interface")
        self.assertTrue(indexed(self.conn, "MobilePre"))

    def test_an_update_forgets_the_old_text_and_learns_the_new(self):
        url = self.seed("src", body="the listing teaser")
        storage.upgrade_release(self.conn, url, body="Octane preamplifier")
        self.assertTrue(indexed(self.conn, "Octane"))
        self.assertFalse(indexed(self.conn, "teaser"))
        self.assertEqual(disagreements(self.conn), (0, 0))

    def test_a_delete_leaves_no_orphan(self):
        url = self.seed("src", body="ArKaos visual software")
        self.conn.execute("DELETE FROM releases WHERE url = ?", (url,))
        self.conn.commit()
        self.assertFalse(indexed(self.conn, "ArKaos"))
        self.assertEqual(disagreements(self.conn), (0, 0))


class StaleIndexRepairTest(unittest.TestCase):
    """Invariant 3: never create releases_au ahead of a rebuild.

    Its 'delete' subtracts postings for `old.body`'s tokens, and on a stale row
    those are not the tokens actually indexed - so an update would actively
    corrupt the index rather than merely leave it wrong.
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        # The pre-SCHEMA_VERSION state, reproduced: schema, insert trigger only,
        # then rows changed underneath the index.
        self.conn.executescript(schema.SCHEMA_SQL)
        self.conn.executescript(
            schema.TRIGGERS_SQL.split("CREATE TRIGGER IF NOT EXISTS releases_au")[0]
        )
        for i in range(3):
            self.conn.execute(
                "INSERT INTO releases (source, url, title, body, grade)"
                " VALUES ('src', ?, 'T', 'the listing teaser', 'teaser')",
                (f"http://x/{i}",),
            )
        self.conn.execute("UPDATE releases SET body = 'Radium the real article'")
        self.conn.commit()
        self.addCleanup(self.conn.close)

    def test_integrity_check_passes_on_a_stale_index(self):
        """Asserted, not assumed: this is why none of the other tests here use
        it. The index is internally consistent - it simply does not know the
        content table moved underneath it."""
        self.conn.execute(
            "INSERT INTO releases_fts(releases_fts) VALUES('integrity-check')"
        )
        self.assertFalse(indexed(self.conn, "Radium"))

    def test_init_db_repairs_before_installing_the_update_trigger(self):
        migration.init_db(self.conn)
        self.assertEqual(len(indexed(self.conn, "Radium")), 3)
        self.assertFalse(indexed(self.conn, "teaser"))
        self.assertEqual(disagreements(self.conn), (0, 0))

    def test_the_repair_runs_once_and_stamps_the_version(self):
        migration.init_db(self.conn)
        self.assertEqual(
            self.conn.execute("PRAGMA user_version").fetchone()[0],
            schema.SCHEMA_VERSION,
        )
        index.sync_fts_triggers(self.conn)  # idempotent
        self.assertEqual(disagreements(self.conn), (0, 0))


class MigrationTest(support.DbCase):
    def test_init_db_is_idempotent(self):
        self.seed("src", body="MobilePre")
        migration.init_db(self.conn)
        migration.init_db(self.conn)
        self.assertEqual(disagreements(self.conn), (0, 0))
        self.assertEqual(len(indexed(self.conn, "MobilePre")), 1)

    def test_grade_moves_out_of_detail_id_and_only_once(self):
        """The 2026-08-22 split: a *verdict about the body* sat in the field
        meant for a *reference to where the body came from*, and seven places
        re-derived which of the two a value was by counting digits."""
        self.conn.execute(
            "INSERT INTO releases (source, url, title, body, detail_id, grade)"
            " VALUES ('src', 'http://x/1', 'T', 'b', 'teaser', 'full')"
        )
        self.conn.commit()
        schema.migrate(self.conn)
        row = self.conn.execute(
            "SELECT grade, detail_id FROM releases WHERE url = 'http://x/1'"
        ).fetchone()
        self.assertEqual((row["grade"], row["detail_id"]), ("teaser", None))

        # Nothing left to move, and a title or body is never touched either way.
        self.assertEqual(
            self.conn.execute(
                "UPDATE releases SET grade = detail_id, detail_id = NULL"
                " WHERE detail_id IN ('teaser', 'stub')"
            ).rowcount,
            0,
        )


class ReadOnlyTest(support.DbCase):
    def test_connect_ro_refuses_a_write(self):
        """A browser has no business migrating a database, and ?mode=ro turns an
        accidental write into an OperationalError instead of a silently damaged
        index."""
        ro = connection.connect_ro(self.db_path)
        self.addCleanup(ro.close)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("INSERT INTO releases (source, url) VALUES ('x','y')")

    def test_connect_ro_does_not_migrate(self):
        """connect() runs init_db(); connect_ro() must not, which on a read-only
        handle would raise rather than quietly do nothing."""
        ro = connection.connect_ro(self.db_path)
        self.addCleanup(ro.close)
        self.assertIsNotNone(ro.execute("SELECT count(*) FROM releases").fetchone())
