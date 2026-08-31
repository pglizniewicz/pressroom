"""The four gates, and each allowance measured into them.

Every case here is a fact this corpus paid for. `gate.py`'s own docstring says
the two data-loss incidents were both a gate *mismatch* rather than a missing
gate, so what these tests pin is not only "does it refuse a bad write" but
"does it still allow the good ones it was widened for" - a gate that refuses
everything passes the first half and is useless.
"""

import unittest

from pressroom.release.control import gate

# A body long enough that a 2% loss is more than a rounding error.
ARTICLE = ("TerraTec Electronic stellt heute die neue Soundkarte vor. " * 20).strip()


class WordCharsTest(unittest.TestCase):
    def test_ordinal_stripped_only_at_line_start(self):
        # to_text() prepends "1. " to an <ol> item; those digits are invented
        # characters and would otherwise read as the parser making text up.
        self.assertEqual(gate.wordchars("1. Alpha\n2. Beta"), "AlphaBeta")

    def test_ordinal_mid_line_survives(self):
        self.assertIn("2004", gate.wordchars("released in 2004. Later"))

    def test_join_is_not_a_change(self):
        # `8 th` -> `8th` is a <sup> that stopped being padded with spaces.
        self.assertEqual(
            gate.wordchars("the 8 th of May"), gate.wordchars("the 8th of May")
        )


class TextDeltaTest(unittest.TestCase):
    def test_edges_only_separates_the_two_readings_of_kept_false(self):
        """kept=False, clean=True is the signature of BOTH "nav was correctly
        dropped" and "a paragraph went missing". Only edges_only tells them
        apart, so both halves are asserted together - the pair alone proving
        nothing is the whole point."""
        old = "NAV HOME " + ARTICLE + " FOOTER CONTACT"
        nav_dropped = gate.text_delta(old, ARTICLE)
        middle_gone = gate.text_delta(
            ARTICLE, ARTICLE[:200] + ARTICLE[len(ARTICLE) - 200 :]
        )

        for d in (nav_dropped, middle_gone):
            self.assertFalse(d["kept"])
            self.assertTrue(d["clean"])
        self.assertTrue(nav_dropped["edges_only"])
        self.assertFalse(middle_gone["edges_only"])


class SafeToWriteTest(unittest.TestCase):
    def test_allows_chrome_removed_from_the_edges(self):
        ok, why = gate.safe_to_write("NAV HOME " + ARTICLE + " IMPRESSUM", ARTICLE)
        self.assertTrue(ok, why)

    def test_allows_a_teaser_recovering_its_article(self):
        # terratec_pressen sid=204 held 216 characters of the portal's own
        # comment widget where the release should have been.
        teaser = "Login Create account Comments Threshold 1 0 1 2 3 4 5 No comments"
        ok, why = gate.safe_to_write(teaser, ARTICLE)
        self.assertTrue(ok, why)

    def test_allows_a_loss_under_two_percent(self):
        # Every instance measured was a URL path or image alt text mid-page.
        old = ARTICLE + "x" * 4
        new = old[: len(old) // 2] + old[len(old) // 2 + 4 :]
        self.assertLessEqual(gate.text_delta(old, new)["removed"], 0.02)
        self.assertTrue(gate.safe_to_write(old, new)[0])

    def test_refuses_a_paragraph_lost_from_the_middle(self):
        cut = len(ARTICLE) // 4
        new = ARTICLE[:cut] + ARTICLE[cut * 3 :]
        ok, why = gate.safe_to_write(ARTICLE, new)
        self.assertFalse(ok)
        self.assertIn("srodku", why)

    def test_the_teaser_allowance_needs_both_of_its_bounds(self):
        """A short old body is not enough on its own, and neither is a long new
        one - the allowance is for a teaser becoming an article, and widening
        either half of it would readmit the middle-loss case above."""
        short_but_not_doubled = "y" * 300
        self.assertFalse(
            gate.safe_to_write(
                short_but_not_doubled,
                short_but_not_doubled[:50] + "z" * 200 + short_but_not_doubled[250:],
            )[0]
        )


class StrictSameTextTest(unittest.TestCase):
    def test_allows_whitespace_and_bullets_to_move(self):
        ok, _ = gate.strict_same_text("Alpha\n\nBeta", "  Alpha \n • Beta  ")
        self.assertTrue(ok)

    def test_refuses_another_release_s_text(self):
        """The gate that costs 64 rows when it is the wrong one. A whole-page
        parse of a listing returns the longest article on it, which
        safe_to_write reads as the teaser-to-article upgrade it allows."""
        self.assertTrue(
            gate.safe_to_write("a teaser, 40 chars or so, no more", ARTICLE)[0]
        )
        self.assertFalse(
            gate.strict_same_text("a teaser, 40 chars or so, no more", ARTICLE)[0]
        )


class NotShorterTest(unittest.TestCase):
    def test_refuses_a_listing_teaser_replacing_the_article(self):
        # #4445: 4287 characters overwritten by 359, 122 rows in one run.
        ok, why = gate.not_shorter("x" * 4287, "y" * 359)
        self.assertFalse(ok)
        self.assertIn("krotszy", why)

    def test_a_lost_tail_reads_as_edges_only_to_the_other_gate(self):
        """Why this floor cannot be folded into safe_to_write: the write it
        exists to stop is invisible to it."""
        full = ARTICLE
        truncated = ARTICLE[: len(ARTICLE) // 8]
        self.assertTrue(gate.safe_to_write(full, truncated)[0])
        self.assertFalse(gate.not_shorter(full, truncated)[0])

    def test_equal_length_passes(self):
        self.assertTrue(gate.not_shorter("abc", "xyz")[0])

    def test_none_is_treated_as_empty(self):
        self.assertTrue(gate.not_shorter(None, "")[0])
        self.assertFalse(gate.not_shorter("abc", None)[0])


class SameWordsTest(unittest.TestCase):
    """The attachment gate: the same bytes read a second way. Blind to order and
    to joins, which is exactly what a converter may change, and to nothing else.
    """

    def test_allows_a_fragment_moved(self):
        # pdftotext -layout puts a superscript on its own line; the structured
        # route puts it back beside the number it belongs to.
        ok, why = gate.same_words("Composer 2 nd system", "Composer 2nd system", [], 0)
        self.assertTrue(ok, why)

    def test_allows_a_join(self):
        ok, why = gate.same_words("Composer ® system", "Composer®system", [], 0)
        self.assertTrue(ok, why)

    def test_allows_the_blocks_the_converter_says_it_dropped(self):
        ok, why = gate.same_words(
            "headline body ROTATEDBANNER", "headline body", ["ROTATEDBANNER"], 0
        )
        self.assertTrue(ok, why)

    def test_allows_markers_absorbed_into_structure_within_the_bound(self):
        # 1)..3) becoming an <ol>: three digits, three list items.
        ok, why = gate.same_words(
            "1) alpha 2) beta 3) gamma", "alpha beta gamma", [], 3
        )
        self.assertTrue(ok, why)

    def test_refuses_marker_absorption_beyond_the_bound(self):
        """Bounded at two characters per list item so it can never excuse a
        missing word - here the "markers" are a year, not a bullet."""
        ok, _ = gate.same_words("alpha 1234567890 beta", "alpha beta", [], 1)
        self.assertFalse(ok)

    def test_refuses_a_missing_paragraph(self):
        ok, why = gate.same_words(ARTICLE, ARTICLE[: len(ARTICLE) // 2], [], 0)
        self.assertFalse(ok)
        self.assertIn("brak", why)

    def test_refuses_invented_text(self):
        ok, why = gate.same_words("alpha beta", "alpha beta gamma", [], 0)
        self.assertFalse(ok)
        self.assertIn("z niczego", why)
