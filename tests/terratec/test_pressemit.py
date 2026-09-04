"""The `pressemit` pool: two hosts and two discovery channels, one work list.

This scraper is the one place in the tree where two channels name the same file
under different urls, so it is the one place a filename has to be the key. CDX
reports `www.terratec.de:80/…`, an index page links to `www.terratec.de/…` with
no port, and CDX's own prefix listing carries `terratec.de:80` as well because
SURT drops a `www.` - three spellings, one article, and `releases.url` is UNIQUE
and compared verbatim with no normalisation anywhere in this tree.

A filename is unique within one host and **not** across the two: 58 of this
generation's 166 files exist under both. So the key is (site, filename), and
that is the half these tests exist for. It was not needed while each host had a
tag of its own - the tag was the site - and collapsing the two tags into one is
exactly what would have dropped 58 German releases from the work list as
"already stored", with no error.
"""

import contextlib
import io
from unittest import mock

from pressroom.fetcher.control import archive
from pressroom.terratec.control import pressemit
from tests import support

# Read off the scraper rather than pasted in, so the two spellings stay the two
# spellings this source actually meets: the folder CDX is asked about, and the
# same folder as an index page's links resolve to.
FOLDER = pressemit.SITES["de"]["prefix"]
LINKED = FOLDER.replace(":80", "")
NET = pressemit.SITES["net"]["prefix"]


def listing(*names) -> bytes:
    """An index capture in the shape extract_links reads: one row per release,
    the link in the first cell and the date in the last."""
    rows = "".join(
        f'<tr><td><a href="pressemit/{n}">{n} headline</a></td><td>31.12.1999</td></tr>'
        for n in names
    )
    return f"<html><body><table>{rows}</table></body></html>".encode("cp1252")


def got(work) -> set[str]:
    """The urls a merged pool came back with.

    Module-level and not called `urls`: `support.DbCase.urls` is a property that
    `seed()` appends to, and a method of that name on the case shadowed it.
    """
    return {entry["url"] for entry in work}


class PoolTest(support.DbCase):
    def pool(self, *, linked=(), crawled=(), site="de"):
        """The merged work list, with both channels answering from this test."""
        with (
            support.no_network(),
            mock.patch.object(
                archive, "fetch_snapshot", lambda *a, **kw: listing(*linked)
            ),
            mock.patch.object(
                archive,
                "list_snapshots_or_exit",
                lambda prefix, **kw: [{"original": u} for u in crawled],
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return pressemit.candidates(
                self.conn, None, site, offline=False, limit=None
            )

    def test_one_file_named_by_both_channels_is_one_candidate(self):
        """The url that survives is the archive's own spelling of it, because
        that is what every stored row of this generation already carries; the
        title and date that survive are the listing's, because they are the only
        metadata either channel has."""
        work = self.pool(linked=("foo.htm",), crawled=(FOLDER + "foo.htm",))
        self.assertEqual(got(work), {FOLDER + "foo.htm"})
        self.assertEqual(work[0]["title"], "foo.htm headline")
        self.assertEqual(work[0]["date"], "1999-12-31")

    def test_the_www_less_spelling_cdx_also_returns_collapses_too(self):
        """A prefix query for `www.terratec.de` matches `terratec.de` as well -
        SURT canonicalises the `www.` away - so one listing can carry two
        spellings of one host on its own, with no index page involved."""
        work = self.pool(
            crawled=(
                FOLDER + "foo.htm",
                "http://terratec.de:80/presse/pressemit/FOO.HTM",
            )
        )
        self.assertEqual(len(work), 1)

    def test_the_index_page_itself_is_not_a_release(self):
        """CDX matches a prefix as a string, not as a path segment, so the
        listing for `/presse/pressemit/` also carries `/presse/pressemit.htm` -
        the index page, which `is_html_page` accepts."""
        work = self.pool(
            crawled=(
                "http://www.terratec.de:80/presse/pressemit.htm",
                FOLDER + "foo.htm",
            )
        )
        self.assertEqual(got(work), {FOLDER + "foo.htm"})

    def test_an_attachment_is_not_a_candidate(self):
        work = self.pool(crawled=(FOLDER + "foo.doc", FOLDER + "foo.htm"))
        self.assertEqual(got(work), {FOLDER + "foo.htm"})

    def test_a_file_already_stored_under_another_spelling_drops_out(self):
        """The check `already_stored()` cannot do: it compares the exact url, so
        the row seeded here is invisible to it and this candidate would be
        stored a second time."""
        self.seed(pressemit.SOURCE, url=LINKED + "foo.htm", body="already here")
        work = self.pool(crawled=(FOLDER + "foo.htm", FOLDER + "bar.htm"))
        self.assertEqual(got(work), {FOLDER + "bar.htm"})

    def test_the_check_runs_for_whichever_channel_gets_there_first(self):
        """The other direction, which the two-command arrangement never had: a
        file the index channel stored under its own spelling is not re-stored
        when the folder listing names it later."""
        self.seed(pressemit.SOURCE, url=FOLDER + "foo.htm", body="already here")
        work = self.pool(linked=("foo.htm", "bar.htm"))
        self.assertEqual(got(work), {LINKED + "bar.htm"})

    def test_the_same_filename_on_the_other_host_is_still_a_candidate(self):
        """The one that makes a single tag workable. 58 of this generation's
        filenames exist on both hosts - the German copy of a release keeps the
        release's filename - so a check keyed on the filename alone would call
        every one of them stored. The row seeded here is the .net copy; the .de
        candidate has to come through anyway.
        """
        self.seed(pressemit.SOURCE, url=NET + "foo.htm", body="the English one")
        work = self.pool(crawled=(FOLDER + "foo.htm",))
        self.assertEqual(got(work), {FOLDER + "foo.htm"})

    def test_an_index_only_file_survives(self):
        """Both channels contribute rows: this is the shape of the rows that are
        in the corpus because an index page named them and the folder listing
        does not have them at all."""
        work = self.pool(linked=("gone.htm",), crawled=(FOLDER + "foo.htm",))
        self.assertEqual(got(work), {FOLDER + "foo.htm", LINKED + "gone.htm"})

    def test_offline_asks_neither_channel(self):
        """`--offline` has to touch nothing, and the guard goes on the candidate
        list rather than on the fetch: guarding only the index fetch once left a
        full candidate list for the per-article loop to crawl."""
        with support.no_network(), contextlib.redirect_stdout(io.StringIO()):
            work = pressemit.candidates(self.conn, None, "de", offline=True, limit=None)
        self.assertEqual(work, [])

    def test_only_this_site_s_index_pages_are_fetched(self):
        """INDEX_PAGES is one flat list carrying both hosts, so the filter that
        picks a site's own captures out of it is what keeps the pools apart:
        without it the .net index pages would contribute .net urls to the .de
        pool."""
        fetched = []

        def remember(conn, session, url, timeout=20):
            fetched.append(url)
            return listing()

        with (
            support.no_network(),
            mock.patch.object(archive, "fetch_snapshot", remember),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            pressemit.from_indexes(self.conn, None, "de")

        self.assertEqual(
            fetched, [c for _base, site, c in pressemit.INDEX_PAGES if site == "de"]
        )
        self.assertTrue(fetched, "the .de site has index captures in INDEX_PAGES")


class SiteTest(support.DbCase):
    def test_the_one_tag_has_a_company(self):
        """A tag with no `COMPANIES` entry 400s in the panel
        (`tests/taxonomy/test_company.py`)."""
        from pressroom.taxonomy.entity import company

        self.assertNotEqual(company.company_of(pressemit.SOURCE), company.UNKNOWN)

    def test_every_site_declares_a_folder(self):
        for site, cfg in pressemit.SITES.items():
            with self.subTest(site=site):
                self.assertTrue(cfg["prefix"].endswith("/"))

    def test_the_prefixes_are_pairwise_disjoint_by_path(self):
        """What `site_of` rests on, and the whole dedup key with it: a third
        host sharing a path prefix with one of these would fall into its
        partition and every one of its files would be reported already-stored.
        """
        paths = [
            pressemit.urlsplit(cfg["prefix"]).path for cfg in pressemit.SITES.values()
        ]
        for i, one in enumerate(paths):
            for other in paths[i + 1 :]:
                self.assertFalse(one.startswith(other), (one, other))
                self.assertFalse(other.startswith(one), (other, one))

    def test_site_of_places_a_url_from_each_host(self):
        for site, cfg in pressemit.SITES.items():
            with self.subTest(site=site):
                self.assertEqual(pressemit.site_of(cfg["prefix"] + "x.htm"), site)

    def test_site_of_refuses_a_page_outside_the_folders(self):
        self.assertIsNone(
            pressemit.site_of("http://www.terratec.de:80/presse/pressemit.htm")
        )
        self.assertIsNone(pressemit.site_of("http://example.test/x.htm"))

    def test_every_index_page_belongs_to_a_site(self):
        self.assertEqual(
            {site for _base, site, _c in pressemit.INDEX_PAGES}, set(pressemit.SITES)
        )
