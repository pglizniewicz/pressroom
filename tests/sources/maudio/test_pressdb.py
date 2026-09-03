"""pressdb's own phase-1 loop, and the verdict its upgrade branch has to state.

This scraper keeps its own loop, for the reason its module docstring records:
entries are deduped by (title, date) across every historical re-dump of one PHP
page. So it inherits from nowhere the rule every other upgrade site carries -
a write that replaces a teaser body with the real article passes `grade="full"`.

pressdb writes `full` at insert time even when the body is a listing blurb,
which is why this loop's cursor is `stored_body_length` rather than the grade.
What the verdict buys is the row that arrives graded truthfully leaving that
way.
"""

import contextlib
import io
from unittest import mock

from pressroom.scraping.control import discovery
from pressroom.sources.maudio.control import pressdb
from pressroom.release.entity.grade import Grade
from tests import support

SOURCE = "midiman_com_pressdb"
LISTING = pressdb.DOMAINS[SOURCE]
DETAIL = "http://www.midiman.com/news/php/radium.php"

TEASER = "M-Audio ships Radium. " * 4
ARTICLE = "The full press release text of the Radium announcement. " * 40
LONG_TEASER = "x" * (pressdb.RECOVERED_LENGTH + 1)

TS = "20021016075138"
CAPTURE = f"https://web.archive.org/web/{TS}id_/{DETAIL}"


class VerdictTest(support.DbCase):
    def entry(self, url=DETAIL, body=TEASER):
        return {
            "title": "Radium",
            "date": "2002-07-02",
            "url": url,
            "body": body,
            "body_html": f"<p>{body}</p>",
            "detail_id": TS,
        }

    def detail(self, *a, **kw):
        return (
            {
                "body": ARTICLE,
                "body_html": f"<p>{ARTICLE}</p>",
                "detail_id": TS,
                "origin_url": CAPTURE,
            },
            True,
        )

    def scrape(self, entries, fetch_detail=None):
        """One domain's phase 1, with the archive answering from this test.

        `catch_up=False` leaves phase 2 out: `catch_up.run` pops that flag and
        returns, so what runs is the loop under test and nothing behind it. The
        whole thing sits inside no_network() - scrape_domain still reaches
        attachment_crawl at the end, and "there is nothing for it to fetch" is
        worth proving rather than assuming.
        """
        with (
            support.no_network(),
            mock.patch.object(
                discovery, "sample_all_captures", lambda *a, **kw: entries
            ),
            mock.patch.object(
                discovery,
                "fetch_detail_snapshot",
                fetch_detail or (lambda *a, **kw: self.detail()),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            pressdb.scrape_domain(SOURCE, LISTING, catch={"catch_up": False})

    def test_a_recovered_article_says_the_row_stopped_being_a_teaser(self):
        self.seed(source=SOURCE, url=DETAIL, body=TEASER, grade=Grade.TEASER)
        self.scrape([self.entry()])
        row = self.row(DETAIL)
        self.assertEqual(row["body"], ARTICLE)
        self.assertEqual(row["grade"], "full")

    def test_a_row_already_recovered_is_not_fetched_at_all(self):
        """The other half: the cursor has to sieve, or the verdict above is
        being stamped on every rerun over text nothing looked at."""

        def refuse(*a, **kw):
            raise AssertionError("fetched a row whose body was already recovered")

        self.seed(source=SOURCE, url=DETAIL, body=LONG_TEASER, grade=Grade.TEASER)
        self.scrape([self.entry(body=LONG_TEASER)], fetch_detail=refuse)
        row = self.row(DETAIL)
        self.assertEqual(row["body"], LONG_TEASER)
        self.assertEqual(row["grade"], "teaser")
