"""One parsed node -> the two representations a release is stored as.

Three of these tests are about things `richtext.py` does that are *decisions*
rather than cleanup, and the rest are about the shapes 1998-2005 markup actually
takes. The invariant at the top is the one everything else in the repo leans on.
"""

import re
import unittest

from bs4 import BeautifulSoup

from pressroom.text.control import richtext
from tests import support


def soup(markup):
    return BeautifulSoup(markup, "html.parser")


def container(markup):
    """The article wrapper a scraper hands extract(), not the whole document.

    Every caller passes the element `densest()` found, and `clean()` drops that
    element and keeps its children - half the time it is a <td>, which cannot
    legally survive on its own. Passing the soup instead makes the wrapper a
    *child*, which is a different and much less interesting parse.
    """
    return soup(markup).find(True)


class ExtractInvariantTest(unittest.TestCase):
    def test_body_is_to_text_of_body_html(self):
        """`body` is *by definition* to_text(body_html), which is what makes a
        renderer fix free (--retext, no network, no parser). extract() exists as
        one call for exactly this reason: the indexed text and the displayed
        markup are computed from each other and cannot drift."""
        body, html = richtext.extract(container(
            "<td><b>Headline</b><br><br>First para.<br><br>"
            "<ul><li>one</li><li>two</li></ul></td>"))
        self.assertEqual(body, richtext.to_text(html))

    def test_holds_over_every_committed_fixture(self):
        for name, spec in sorted(support.manifest().items()):
            if spec["kind"] != "detail":
                continue
            with self.subTest(fixture=name):
                got = support.golden(name)
                if got.get("body_html"):
                    self.assertEqual(got["body"], richtext.to_text(got["body_html"]))

    def test_a_missing_container_falls_back_rather_than_blanking(self):
        """extract(None) returns ("", "") so a parser keeps its "no body found"
        branch - a handful of these captures are 290-byte "page moved" stubs
        with no table in them at all, and a blanked body would be data loss
        dressed as a successful parse."""
        self.assertEqual(richtext.extract(None), ("", ""))


class ParagraphTest(unittest.TestCase):
    def test_double_br_becomes_a_paragraph_break(self):
        """These CMSes mostly did not use <p> at all: a paragraph break was
        <br><br>, and plenty of pages are one long text node per paragraph."""
        _body, html = richtext.extract(container("<td>First.<br><br>Second.</td>"))
        self.assertEqual(html.count("<p>"), 2)

    def test_a_single_br_is_left_alone(self):
        """Inside an address block or a spec line it is the real thing."""
        _body, html = richtext.extract(container("<td>Street 1<br>12345 Town</td>"))
        self.assertEqual(html.count("<p>"), 1)
        self.assertIn("<br/>", html)

    def test_no_stray_leading_break_survives_the_flush(self):
        _body, html = richtext.extract(container("<td><br><br>Real text.<br><br></td>"))
        self.assertFalse(html.strip().startswith("<br"))

    def test_every_break_in_a_run_is_seen(self):
        """Walked over a snapshot rather than with find_next_sibling(), which
        skips NavigableStrings - so "A<br>x<br>y<br>B" looked like one run of
        three <br> and every break in the document got eaten at once."""
        _body, html = richtext.extract(
            container("<td>A<br><br>x<br><br>y<br><br>B</td>"))
        self.assertEqual(html.count("<p>"), 4)


class TableTest(unittest.TestCase):
    def test_a_one_cell_table_is_demoted_to_blocks(self):
        """Every one of these sites laid its pages out in nested tables. A table
        where no row has more than one cell carries no row/column relationship
        at all."""
        _body, html = richtext.extract(container(
            "<div><table><tr><td>Just the article text.</td></tr></table></div>"))
        self.assertNotIn("<table", html)
        self.assertIn("Just the article text.", html)

    def test_a_real_spec_sheet_survives_with_its_spans(self):
        _body, html = richtext.extract(container(
            '<div><table><tr><th colspan="2">Specs</th></tr>'
            "<tr><td>Inputs</td><td>8</td></tr></table></div>"))
        self.assertIn("<table>", html)
        self.assertIn('colspan="2"', html)
        self.assertIn("<td>", html)

    def test_an_image_only_header_table_is_pruned(self):
        """The 2003 CMS page header: a nested table of banner and 1x1 spacer
        gifs with no text in it anywhere."""
        _body, html = richtext.extract(container(
            '<div><table><tr><td><img src="top.gif"></td>'
            '<td><img src="spacer.gif" width="1" height="1"></td></tr></table>'
            "<p>The release.</p></div>"))
        self.assertNotIn("top.gif", html)
        self.assertIn("The release.", html)

    def test_a_product_photo_in_its_own_paragraph_survives(self):
        """The other half of the same rule: for the table family an image does
        not count as content, everywhere else it does."""
        _body, html = richtext.extract(container(
            '<div><p><img src="prod.jpg" alt="BX5"></p><p>Text.</p></div>'))
        self.assertIn("prod.jpg", html)

    def test_pruning_and_demoting_alternate(self):
        """One pass is not enough: a page-header table has two cells per row, so
        it survives the demotion test until pruning removes its image-only rows -
        and what is left is a one-cell wrapper that is pure layout and must go
        too."""
        _body, html = richtext.extract(container(
            '<div><table><tr><td><img src="a.gif"></td><td><img src="b.gif"></td></tr>'
            "<tr><td>The whole release, one cell.</td></tr></table></div>"))
        self.assertNotIn("<table", html)
        self.assertIn("The whole release", html)


class AttributeTest(unittest.TestCase):
    def test_img_is_preserved_verbatim(self):
        """These sites are dead so most of these addresses resolve to nothing,
        but they are the only record of which image belonged where."""
        _body, html = richtext.extract(container(
            '<div><p><img src="images/prod.gif" alt="BX5" title="t" '
            'width="120" height="80" class="x" onload="evil()"></p></div>'))
        for kept in ('src="images/prod.gif"', 'alt="BX5"', 'title="t"',
                     'width="120"', 'height="80"'):
            self.assertIn(kept, html)
        self.assertNotIn("class", html)
        self.assertNotIn("onload", html)

    def test_dangerous_schemes_go_and_relative_paths_stay(self):
        _body, html = richtext.extract(container(
            '<div><p><a href="javascript:evil()">x</a>'
            '<a href="/press/foo.html">y</a>'
            '<img src="data:image/gif;base64,AAAA"></p></div>'))
        self.assertNotIn("javascript:", html)
        self.assertNotIn("data:", html)
        self.assertIn('href="/press/foo.html"', html)

    def test_scripts_and_forms_go_with_their_contents(self):
        """`form` is in the drop set because several of these templates put a
        search box inside the article container itself."""
        _body, html = richtext.extract(container(
            "<div><script>var a=1;</script><form><input></form>"
            "<p>The release.</p></div>"))
        self.assertNotIn("var a=1", html)
        self.assertEqual(html.strip(), "<p>The release.</p>")

    def test_headings_are_pushed_below_the_page_s_own_h2(self):
        _body, html = richtext.extract(container("<div><h1>A</h1><h2>B</h2></div>"))
        self.assertNotIn("<h1>", html)
        self.assertNotIn("<h2>", html)
        self.assertEqual(html.count("<h3>"), 2)


class CutFromTest(unittest.TestCase):
    def test_cuts_a_marker_that_sits_in_a_bare_text_node(self):
        """The shape the naive leaf-element scan missed: "Infos bei:" on
        midiman.de sits in a bare text node between two <br>, so a scan over
        leaf *elements* found nothing and the contact footer survived into 37
        bodies that used to have it cut."""
        page = soup("<div>The release.<br>Infos bei: Midiman GmbH<br>"
                    "<p>Tel 12345</p></div>")
        self.assertTrue(richtext.cut_from(page, re.compile("Infos bei:")))
        self.assertNotIn("Midiman GmbH", str(page))
        self.assertNotIn("Tel 12345", str(page))
        self.assertIn("The release.", str(page))

    def test_reports_when_there_was_nothing_to_cut(self):
        self.assertFalse(richtext.cut_from(soup("<div>x</div>"),
                                           re.compile("Infos bei:")))


class DensestTest(unittest.TestCase):
    def test_returns_the_biggest_not_the_first(self):
        """Why "the biggest one" beats a CSS selector here: the only thing
        distinguishing the article's table from the navigation's is a
        width="535" that changes between captures of the same site. Measured,
        width selectors missed 36 of 186 portal captures."""
        page = soup('<html><table width="535"><tr><td>nav</td></tr></table>'
                    '<table width="100"><tr><td>' + "the article " * 20 +
                    "</td></tr></table></html>")
        self.assertIn("the article", richtext.densest(page, "table").get_text())

    def test_none_when_there_is_nothing_of_that_kind(self):
        self.assertIsNone(richtext.densest(soup("<div>x</div>"), "table"))


class ToTextTest(unittest.TestCase):
    def test_a_cell_is_one_flat_chunk(self):
        """These templates wrap every cell's content in its own <p>, and letting
        that emit a paragraph break turned Intel's quarterly results table into
        a column of disconnected numbers - 36.1% and 39.2% two blank lines apart
        instead of two cells of one row."""
        got = richtext.to_text("<table><tr><td><p>36.1%</p></td>"
                               "<td><p>39.2%</p></td></tr></table>")
        self.assertEqual(got.split("\n")[0], "36.1%\t39.2%")

    def test_ordered_and_unordered_lists_are_marked_differently(self):
        ordered = richtext.to_text("<ol><li>alpha</li><li>beta</li></ol>")
        self.assertIn("1. alpha", ordered)
        self.assertIn("2. beta", ordered)
        self.assertIn("• alpha", richtext.to_text("<ul><li>alpha</li></ul>"))

    def test_an_image_contributes_nothing_to_the_indexed_text(self):
        """`alt` on this corpus is as often 'spacer.gif' or '' as it is a real
        caption, and `body` feeds the FTS index."""
        markup = '<p>a<img src="x.gif" alt="spacer.gif">b</p>'
        self.assertEqual(richtext.to_text(markup), "ab")

    def test_empty_html_is_empty_text(self):
        self.assertEqual(richtext.to_text(""), "")
