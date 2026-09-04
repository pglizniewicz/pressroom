"""The attachment passes' write: text and provenance, or neither.

`attachment_crawl.py` was the last place in the tree where a body write and its
`body_origin` entry were two separately committed statements. In the two offline
passes that was invisible - `origin_url` comes out of a JOIN on `body_origin`,
so the second write only ever restated the value the first had just read - which
is why the shape was left when everything else was fixed. The network path is
where it showed: there the address is new, and a crash between the two commits
leaves the row holding a PDF's text and still naming the capture it had before.
"""

import re
from unittest import mock

from pressroom.converter.control import conversion
from pressroom.fetcher.control import archive
from pressroom.provenance.entity import origin
from pressroom.release.entity.grade import Grade
from pressroom.scraper.control import attachment_crawl
from tests import support

URL = "http://www.midiman.com/news/pdf/PR07022002A.pdf"
CAPTURE = (
    "https://web.archive.org/web/20021016075137id_/"
    "http://www.midiman.com/news/pdf/PR07022002A.pdf"
)


class ReextractWriteTest(support.DbCase):
    """One row in exactly the state the corpus was in before normalize()'s
    layout fix reached it: the extractor's text with every newline collapsed by
    the old `re.sub(r"\\s+", " ", text)`. That is what makes strict_same_text
    pass and the re-extraction actually write."""

    def setUp(self):
        super().setUp()
        self.text, _ = conversion.plain_text(support.fixture("attachment_pdf"))
        self.assertIn("\n", self.text)
        self.seed(
            source="midiman_com_pressdb",
            url=URL,
            body=re.sub(r"\s+", " ", self.text),
        )

    def hold(self, key: str) -> None:
        """The attachment's bytes in page_cache under `key`, and `key` recorded
        as where this row's body came from - the join both offline passes walk."""
        self.cache(key, support.fixture("attachment_pdf"))
        origin.record(self.conn, URL, key)

    def test_the_layout_is_recovered_and_the_entry_still_stands(self):
        self.hold(CAPTURE)
        attachment_crawl.reextract_from_cache()
        self.assertEqual(self.row(URL)["body"], self.text)
        self.assertEqual(self.origin_of(URL), CAPTURE)

    def test_a_refused_origin_takes_the_new_body_down_with_it(self):
        """The write and the entry are one transaction.

        With the two commits split, `upgrade_release` had already committed the
        re-extracted text by the time `origin.record` ran - and `record` does
        not validate, so nothing raised and the row kept a body whose recorded
        origin was never a capture address. Routing the entry through
        `upgrade_release(origin_url=...)` puts `is_capture_address` inside the
        same `with conn:`, so the refusal now rolls the body back.
        """
        stored = self.row(URL)["body"]
        self.hold("970")
        with self.assertRaises(ValueError):
            attachment_crawl.reextract_from_cache()
        self.assertEqual(self.row(URL)["body"], stored)

    def origin_of(self, url: str):
        row = self.conn.execute(
            "SELECT origin_url FROM body_origin WHERE url = ?", (url,)
        ).fetchone()
        return row[0] if row else None


class RichtextCursorTest(support.DbCase):
    """`write_richtext`'s cursor is `body_html IS NULL`, and that is the whole
    of what keeps its report right.

    Without the filter the pass re-converted every PDF it had already converted:
    the same extractor over the same bytes, writing them back identical, and
    `upgrade_release` returning True because the UPDATE did match a row. So the
    counter said `upgraded` on a run that changed nothing, every run, and
    gating on the write's return value - the rule that catches this everywhere
    else - could not see it.
    """

    PDF = "http://www.midiman.com/news/pdf/PR07022002B.pdf"
    PDF_CAPTURE = (
        "https://web.archive.org/web/20021016075138id_/"
        "http://www.midiman.com/news/pdf/PR07022002B.pdf"
    )
    HAND_WRITTEN = "<p>nie z konwertera</p>"

    def hold(self, url: str, capture: str) -> None:
        self.cache(capture, support.fixture("attachment_pdf"))
        origin.record(self.conn, url, capture)

    def test_a_row_that_already_has_markup_is_not_in_the_cursor(self):
        self.seed(
            source="midiman_com_pressdb",
            url=self.PDF,
            body="tekst",
            body_html=self.HAND_WRITTEN,
        )
        self.hold(self.PDF, self.PDF_CAPTURE)
        attachment_crawl.write_richtext()
        self.assertEqual(self.row(self.PDF)["body_html"], self.HAND_WRITTEN)

    def test_a_row_without_markup_still_gets_it(self):
        """The filter has to narrow the cursor, not empty it."""
        self.seed(source="midiman_com_pressdb", url=self.PDF, body="zajawka")
        self.hold(self.PDF, self.PDF_CAPTURE)
        attachment_crawl.write_richtext()
        self.assertIn("<p>", self.row(self.PDF)["body_html"] or "")


class VerdictTest(support.DbCase):
    """Which of the three writes here states `grade`, and which one must not.

    The rule is one line in CLAUDE.md - an upgrade that replaces a teaser body
    with the real article passes `grade="full"`. What that line does not settle
    is that only two of this module's three writes are that upgrade, and the
    difference is in the gates rather than in the passes' names:

      - `write_richtext` compares the two conversions of the same bytes to each
        other, never to what is stored, and its cursor asks about `body_html`.
        A row whose `body` is still the listing blurb goes straight through it.
      - the network crawl writes exactly when the fetched text is longer than
        the stored one, which is the replacement itself.
      - `reextract_from_cache` writes only what its gate proved is already
        stored character for character, so there is no grade there to change.

    Every row of these sources is graded `full` today, blurb or not, so what
    this pins is that a row which arrives correctly graded leaves that way.
    """

    PDF = "http://www.midiman.com/news/pdf/PR07022002C.pdf"
    TS = "20021016075139"
    CAPTURE = f"https://web.archive.org/web/{TS}id_/{PDF}"

    def setUp(self):
        super().setUp()
        self.bytes = support.fixture("attachment_pdf")
        self.text, _ = conversion.plain_text(self.bytes)

    def teaser(self, body="zajawka z listingu"):
        self.seed(
            source="midiman_com_pressdb", url=self.PDF, body=body, grade=Grade.TEASER
        )

    def hold(self):
        self.cache(self.CAPTURE, self.bytes)
        origin.record(self.conn, self.PDF, self.CAPTURE)

    def test_richtext_over_a_teaser_says_the_verdict_changed(self):
        self.teaser()
        self.hold()
        attachment_crawl.write_richtext()
        row = self.row(self.PDF)
        self.assertIn("<p>", row["body_html"] or "")
        self.assertEqual(row["grade"], "full")

    def test_the_crawl_says_it_too(self):
        """The one write here whose bytes are new, so nothing else can say it."""
        self.teaser()
        with mock.patch.object(
            archive,
            "fetch_best_matching_snapshot",
            lambda *a, **kw: (self.bytes, self.TS, True),
        ):
            attachment_crawl.catch_up_network_source("midiman_com_pressdb")
        row = self.row(self.PDF)
        self.assertEqual(row["body"], self.text)
        self.assertEqual(row["grade"], "full")

    def test_a_re_extraction_recovers_layout_and_states_no_verdict(self):
        """The asymmetry, pinned so it can fail rather than drift.

        `strict_same_text` admits only a body that already is this extraction,
        so nothing that reaches the write is a teaser and nothing here has
        established that it stopped being one.
        """
        self.teaser(re.sub(r"\s+", " ", self.text))
        self.hold()
        attachment_crawl.reextract_from_cache()
        row = self.row(self.PDF)
        self.assertEqual(row["body"], self.text)
        self.assertEqual(row["grade"], "teaser")
