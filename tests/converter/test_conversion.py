"""What an attachment's bytes are, and what may be done with them.

Two predicates asking the same question either side of a fetch - `is_attachment_url`
on an address, `is_attachment`/`looks_like_html` on bytes - and both of them
exist because of a failure that produced *plausible wrong data* rather than an
error.
"""

import unittest

from pressroom.converter.control import conversion
from tests import support

PDF = b"%PDF-1.3\n%\xe2\xe3\xcf\xd3\n6 0 obj\n"
DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 24

# The original server over quota in 2003, faithfully archived as HTTP 200 under
# a .pdf name. Four body_origin entries pointed at a page of this shape before
# a content check caught them.
SOFT_404 = (
    b"<HTML><HEAD><TITLE>509 Bandwidth Limit Exceeded</TITLE></HEAD>"
    b"<BODY><H1>Bandwidth Limit Exceeded</H1><HR>"
    b"<ADDRESS>Apache/1.3.27 Server at www.m-audio.com</ADDRESS></BODY></HTML>"
)


class UrlPredicateTest(unittest.TestCase):
    def test_the_two_extensions(self):
        for url, want in (
            ("http://x/M-Audio_BX5_PR.pdf", True),
            ("http://x/M-AUDIO_PROKEYS.DOC", True),
            ("http://x/press.pdf?ver=2", True),
            ("http://x/index.php?do=media.new&ID=596", False),
            ("http://x/news/en_us-596.html", False),
            ("", False),
            (None, False),
        ):
            with self.subTest(url=url):
                self.assertEqual(conversion.is_attachment_url(url), want)


class MagicByteTest(unittest.TestCase):
    def test_names_what_it_found_rather_than_answering_yes_or_no(self):
        """A named kind is what lets a mislabeled file be *reported* instead of
        silently yielding an empty body."""
        self.assertEqual(conversion.kind_of(PDF), "pdf")
        self.assertEqual(conversion.kind_of(DOC), "doc")
        self.assertEqual(conversion.kind_of(SOFT_404), "html (soft-404?)")
        self.assertEqual(conversion.kind_of(b"{\\rtf1\\ansi"), "rtf (no extractor)")
        self.assertIn("unrecognised", conversion.kind_of(b"\x00\x01\x02\x03"))

    def test_a_soft_404_under_a_pdf_name_is_not_an_attachment(self):
        """It is HTTP 200, it is longer than the teaser it would replace, and it
        would pass a length-based guard. Only the bytes say no."""
        self.assertFalse(conversion.is_attachment(SOFT_404))
        self.assertTrue(conversion.is_attachment_url("http://x/BX5_PR.pdf"))

    def test_html_parser_refuses_binary_before_the_parse(self):
        """BeautifulSoup never refuses input: hand it a PDF and get_text()
        returns the stream decoded as characters, with no exception to catch.
        23 rows held `%PDF-1.3 %...` over text pdftotext had extracted
        correctly, and it read as *encoding damage* rather than data loss."""
        self.assertFalse(conversion.looks_like_html(PDF))
        self.assertFalse(conversion.looks_like_html(DOC))
        self.assertFalse(conversion.looks_like_html(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(conversion.looks_like_html(SOFT_404))
        self.assertTrue(conversion.looks_like_html(b"  \n<html><body>hi"))
        self.assertFalse(conversion.looks_like_html(b""))


class NormalizeTest(unittest.TestCase):
    def test_keeps_the_layout_it_was_asked_for(self):
        """This was `re.sub(r"\\s+", " ", text)` once, which threw away the one
        thing `pdftotext -layout` exists to produce. 380 of 381 attachment rows
        held the flattened output for months after it was fixed, because nothing
        re-ran the extraction."""
        got = conversion.normalize("Model      Price\nBX5        199\n")
        self.assertIn("\n", got)
        self.assertIn("Model      Price", got)

    def test_drops_page_breaks_trailing_space_and_blank_runs(self):
        self.assertEqual(conversion.normalize("a   \n\n\n\nb\fc"), "a\n\nb\n\nc")


class ExtractorGoldenTest(unittest.TestCase):
    """The two routes over the two committed attachment fixtures.

    Skipped rather than failed when poppler or antiword is absent: their output
    is the thing being pinned, so a missing binary makes the test meaningless
    rather than red.
    """

    def _fixture(self, name):
        try:
            return support.fixture(name)
        except FileNotFoundError:
            self.skipTest(f"no {name} fixture")

    def test_pdf_converts_through_both_routes_and_they_agree(self):
        content = self._fixture("attachment_pdf")
        plain, kind = conversion.plain_text(content)
        if not plain:
            self.skipTest("pdftotext not available")
        self.assertEqual(kind, "pdf")
        text, html, _k = conversion.to_richtext(content)
        self.assertTrue(html.strip().startswith("<"))

        # The gate the 73 converted rows passed: a multiset of word characters,
        # blind to the two things a converter may change and to nothing else.
        from pressroom.release.control import gate

        ok, why = gate.same_words(
            plain, text, conversion.rotated_text(content), html.count("<li")
        )
        self.assertTrue(ok, why)

    def test_doc_keeps_the_text_route(self):
        content = self._fixture("attachment_doc")
        plain, kind = conversion.plain_text(content)
        self.assertEqual(kind, "doc")
        if not plain:
            self.skipTest("antiword not available")
        self.assertIn("\n", plain)
