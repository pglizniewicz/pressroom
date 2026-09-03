"""Every write over `releases`, and the four questions a scraper asks first.

Two of these are documented invariants whose breakage is silent: the phantom
"new" counter, and provenance claimed for a body somebody else wrote.
"""

from pressroom.release.control import storage
from pressroom.release.entity.grade import Grade
from tests import support

CAPTURE = (
    "https://web.archive.org/web/20111011173713id_/http://www.terratec.de/presse.html"
)


class DedupTest(support.DbCase):
    def test_the_return_value_is_the_only_honest_new_counter(self):
        """Invariant 5. `releases.url` is UNIQUE and inserts are INSERT OR
        IGNORE, so an unconditional `count += 1` after this call reports phantom
        inserts on every rerun - which was a real bug."""
        self.assertTrue(storage.store_release(self.conn, "src", "http://x/1"))
        self.assertFalse(storage.store_release(self.conn, "src", "http://x/1"))
        self.assertEqual(storage.source_total(self.conn, "src"), 1)

    def test_a_second_store_changes_nothing(self):
        storage.store_release(self.conn, "src", "http://x/1", body="the article")
        storage.store_release(self.conn, "src", "http://x/1", body="something else")
        self.assertEqual(self.row("http://x/1")["body"], "the article")


class OriginTest(support.DbCase):
    def test_a_platform_id_cannot_reach_this_write(self):
        """The guard `tests/fetcher/test_address.py` is about, at the write site:
        a Q4 numeric id or a bare row url raises here rather than silently
        minting a dead link."""
        for bad in ("970", "node-4935591", "http://www.terratec.de/presse.html"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    storage.store_release(
                        self.conn, "src", f"http://x/{bad}", origin_url=bad
                    )

    def test_a_refused_origin_takes_the_release_row_down_with_it(self):
        """The write above and the entry it refuses are one transaction.

        Without `with conn:` in storage, the INSERT that ran before the raise
        stayed in an open transaction: not written, but not gone either, and
        the next commit() from anywhere on that connection adopted it. The row
        then existed with no body_origin - exactly the false statement the
        guard had just refused to make. A bare conn.commit() cannot express
        this; only a rollback can.
        """
        with self.assertRaises(ValueError):
            storage.store_release(
                self.conn, "src", "http://x/9", body="b", origin_url="970"
            )
        # Read on the same connection, which would see its own uncommitted
        # INSERT - so this fails unless the write was actually rolled back.
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM releases WHERE url = 'http://x/9'"
            ).fetchone()
        )
        self.conn.commit()
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM releases WHERE url = 'http://x/9'"
            ).fetchone()
        )

    def test_recorded_only_when_a_row_actually_came_into_being(self):
        """A url the UNIQUE constraint made this a no-op for holds a body some
        other pass wrote, and claiming our capture as its origin would be a
        false statement about text we did not store."""
        storage.store_release(self.conn, "src", "http://x/1", body="first")
        storage.store_release(
            self.conn, "src", "http://x/1", body="second", origin_url=CAPTURE
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT origin_url FROM body_origin WHERE url = 'http://x/1'"
            ).fetchone()
        )

    def test_an_upgrade_that_moves_only_a_title_leaves_the_entry_alone(self):
        """The entry describes where a *body* came from."""
        storage.store_release(self.conn, "src", "http://x/1", body="b")
        storage.upgrade_release(
            self.conn, "http://x/1", title="A better title", origin_url=CAPTURE
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT origin_url FROM body_origin WHERE url = 'http://x/1'"
            ).fetchone()
        )

        storage.upgrade_release(
            self.conn, "http://x/1", body="the real article", origin_url=CAPTURE
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT origin_url FROM body_origin WHERE url = 'http://x/1'"
            ).fetchone()[0],
            CAPTURE,
        )


class RepairAtTheWriteTest(support.DbCase):
    def test_a_title_is_always_repaired(self):
        """A title has no markup twin, so nothing upstream can have repaired it."""
        storage.store_release(self.conn, "src", "http://x/1", title="GeForce\x99")
        self.assertEqual(self.row("http://x/1")["title"], "GeForce™")

    def test_a_body_with_markup_is_left_to_richtext(self):
        """`richtext.extract` repairs the markup before rendering the text, so
        `body == to_text(body_html)` holds by construction; its docstring says
        why the two cannot be repaired independently."""
        storage.store_release(
            self.conn,
            "src",
            "http://x/1",
            body="GeForce\x99",
            body_html="<p>GeForce\x99</p>",
        )
        self.assertEqual(self.row("http://x/1")["body"], "GeForce\x99")

    def test_a_flat_body_is_repaired(self):
        storage.store_release(self.conn, "src", "http://x/1", body="GeForce\x99")
        self.assertEqual(self.row("http://x/1")["body"], "GeForce™")


class GradeTest(support.DbCase):
    def test_stored_grade_is_the_question_an_upgradable_row_needs(self):
        """`already_stored()` alone would wedge teaser rows permanently: the
        scrapers' condition is `grade is not None and grade != "teaser"`."""
        storage.store_release(self.conn, "src", "http://x/1", grade=Grade.TEASER)
        self.assertTrue(storage.already_stored(self.conn, "http://x/1"))
        self.assertEqual(storage.stored_grade(self.conn, "http://x/1"), "teaser")
        self.assertIsNone(storage.stored_grade(self.conn, "http://x/never"))

    def test_an_upgrade_must_say_the_verdict_changed(self):
        """Otherwise the row keeps a verdict that stopped being true and the
        next run is handed it as still-upgradable."""
        storage.store_release(
            self.conn, "src", "http://x/1", body="blurb", grade=Grade.TEASER
        )
        storage.upgrade_release(self.conn, "http://x/1", body="the real article")
        self.assertEqual(storage.stored_grade(self.conn, "http://x/1"), "teaser")
        storage.upgrade_release(
            self.conn, "http://x/1", body="the real article", grade=Grade.FULL
        )
        self.assertEqual(storage.stored_grade(self.conn, "http://x/1"), "full")

    def test_none_means_leave_that_column_alone(self):
        storage.store_release(
            self.conn, "src", "http://x/1", title="T", date="2003-01-01", body="b"
        )
        storage.upgrade_release(self.conn, "http://x/1", body="better")
        row = self.row("http://x/1")
        self.assertEqual(
            (row["title"], row["date"], row["body"]), ("T", "2003-01-01", "better")
        )

    def test_length_stays_the_honest_check(self):
        """A media_pr row stores the *listing* capture's timestamp even when the
        body is only that listing's blurb, so `full` there means "not marked
        otherwise"."""
        storage.store_release(
            self.conn,
            "src",
            "http://x/1",
            body="45 chars or so",
            detail_id="20030212170800",
        )
        self.assertEqual(storage.stored_grade(self.conn, "http://x/1"), "full")
        self.assertEqual(storage.stored_body_length(self.conn, "http://x/1"), 14)


class SelectorTest(support.DbCase):
    def test_source_urls_yields_only_that_source(self):
        storage.store_release(self.conn, "a", "http://x/1")
        storage.store_release(self.conn, "b", "http://x/2")
        self.assertEqual(list(storage.source_urls(self.conn, "a")), ["http://x/1"])
        self.assertEqual(storage.source_total(self.conn, "b"), 1)
