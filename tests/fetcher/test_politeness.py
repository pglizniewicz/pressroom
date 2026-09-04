"""The fetcher's two live paths, and what each one keeps.

`fetch_cached` had no test of its own until `refetch` gave it a second
behaviour, and `fetch` is new. What is worth asserting is the contract the
rulebook states: a listing is kept nowhere, an article is kept once and fetched
again only on request, and a failed fetch leaves the cache as it was.
"""

from pressroom.fetcher.control import politeness
from pressroom.fetcher.entity import page
from tests import support

URL = "https://live.example/press/1"
OLD = b"<html>the article as first published</html>"
NEW = b"<html>the article as first published, and a correction</html>"


class FetchTest(support.DbCase):
    def test_a_listing_is_fetched_and_kept_nowhere(self):
        session = support.Serving(NEW)
        self.assertEqual(politeness.fetch(session, URL, sleep=0), NEW)
        self.assertEqual(session.urls, [URL])
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM page_cache WHERE url = ?", (URL,)
            ).fetchone()
        )


class FetchCachedTest(support.DbCase):
    def cached(self):
        return self.conn.execute(
            "SELECT content, content_sha256, fetched_at FROM page_cache WHERE url = ?",
            (URL,),
        ).fetchone()

    def test_a_cached_article_costs_no_request(self):
        self.cache(URL, OLD)
        got = politeness.fetch_cached(self.conn, support.Refusing(), URL, sleep=0)
        self.assertEqual(got, OLD)

    def test_a_miss_is_fetched_and_kept_with_its_time(self):
        session = support.Serving(NEW)
        self.assertEqual(politeness.fetch_cached(self.conn, session, URL, sleep=0), NEW)
        content, sha, fetched_at = self.cached()
        self.assertEqual(content, NEW)
        self.assertEqual(sha, page.content_hash(NEW))
        self.assertIsNotNone(fetched_at)

    def test_refetch_asks_again_and_replaces_the_row(self):
        self.cache(URL, OLD)
        session = support.Serving(NEW)
        got = politeness.fetch_cached(self.conn, session, URL, sleep=0, refetch=True)
        self.assertEqual(got, NEW)
        self.assertEqual(session.urls, [URL])
        content, sha, fetched_at = self.cached()
        self.assertEqual(content, NEW)
        self.assertEqual(sha, page.content_hash(NEW))
        self.assertIsNotNone(fetched_at)

    def test_a_failed_refetch_raises_and_keeps_the_old_bytes(self):
        self.cache(URL, OLD)
        with self.assertRaises(OSError):
            politeness.fetch_cached(
                self.conn,
                support.Refusing(OSError("timed out")),
                URL,
                sleep=0,
                refetch=True,
            )
        self.assertEqual(self.cached()[0], OLD)
