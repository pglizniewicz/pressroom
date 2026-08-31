"""Bytes to text when the declared charset lies, and undoing the times it did.

The failures pinned here are all silent ones: every case below produced text
that looked plausible and was wrong, and was found by reading the corpus rather
than by anything raising.
"""

import unittest

from pressroom.text.control import decoding


class DecodeHtmlTest(unittest.TestCase):
    def test_a_stray_word_byte_does_not_cost_the_whole_page(self):
        """The midiman.co.uk failure, in one assertion: three cp1252 bytes in a
        footer made a strict UTF-8 decode fail, bs4 fell back to chardet, chardet
        guessed windows-1250, and every *correct* UTF-8 sequence in the file
        came out as mojibake - 14 titles corrupted over one pasted apostrophe."""
        content = "smart “quotes”".encode("utf-8") + b" and Word\x92s byte"
        got = decoding.decode_html(content)
        self.assertIn("“quotes”", got)  # the valid sequences survive
        self.assertIn("Word’s", got)  # the stray reads as cp1252

    def test_the_five_undefined_bytes_become_one_replacement_each(self):
        # errors="replace" for them: one U+FFFD beats an exception that loses
        # the page.
        self.assertEqual(decoding.decode_html(b"a\x81b"), "a�b")


class UndoC1Test(unittest.TestCase):
    def test_per_character_survives_a_mixed_row(self):
        """21 of creative's 23 damaged rows hold a stray 0x95 bullet *and*
        correctly decoded typography above U+00FF. The obvious whole-string
        inversion raises on exactly those rows - the ones that need it most."""
        text = "‘quoted’ \x95 bullet €"
        with self.assertRaises(UnicodeEncodeError):
            text.encode("latin-1")
        self.assertEqual(decoding.undo_c1(text), "‘quoted’ • bullet €")

    def test_leaves_the_bytes_cp1252_does_not_define(self):
        undefined = "\x81\x8d\x8f\x90\x9d"
        self.assertEqual(decoding.undo_c1(undefined), undefined)


class UndoMojibakeTest(unittest.TestCase):
    def test_undoes_utf8_read_as_cp1252(self):
        broken = "völlig".encode("utf-8").decode("cp1252")
        self.assertEqual(decoding.undo_mojibake(broken)[0], "völlig")

    def test_undoes_utf8_read_as_cp1258(self):
        """Not a typo: cp1258 is Vietnamese, and it is what chardet guessed on
        terratec.net's German pages. It is what tells 'FĂ¼r' apart from cp1250's
        otherwise identical A-breve."""
        broken = "Für".encode("utf-8").decode("cp1258")
        fixed, codec = decoding.undo_mojibake(broken)
        self.assertEqual(fixed, "Für")
        self.assertEqual(codec, "cp1258")

    def test_mac_roman_is_not_a_candidate(self):
        """It re-encodes this text happily and leaves no damage markers, so
        including it would make a second, contradicting candidate and the
        agreement check would refuse the row - while its output is simply wrong
        ('völlig' becomes v-cedilla-llig)."""
        self.assertNotIn("mac-roman", decoding._MOJIBAKE_CODECS)
        self.assertNotIn("mac_roman", decoding._MOJIBAKE_CODECS)

    def test_is_guarded_by_the_marker_regex_rather_than_by_itself(self):
        """Plain ASCII round-trips through cp1252 unchanged, so this reports a
        "repair" that repaired nothing. That is not a bug and it is worth
        pinning: the guard is the caller's MOJIBAKE_RE test, and repair_text -
        the entry point everything actually uses - answers None."""
        self.assertEqual(decoding.undo_mojibake("ordinary prose")[0], "ordinary prose")
        self.assertIsNone(decoding.repair_text("ordinary prose"))

    def test_refuses_when_no_candidate_comes_out_clean(self):
        """A round trip counts only if it leaves no damage markers behind, so a
        codec that merely happens to encode the string cannot win. 'Ã‚Â©' is a
        double encoding: one pass yields 'Â©', which is still damaged."""
        self.assertTrue(decoding.MOJIBAKE_RE.search("Ã‚Â©"))
        self.assertIsNone(decoding.undo_mojibake("Ã‚Â©"))
        self.assertIsNone(decoding.repair_text("Ã‚Â©"))


class RepairTextTest(unittest.TestCase):
    def test_is_idempotent(self):
        """What lets this live in the write path at all: every store and every
        upgrade calls it, and an already-repaired row costs a regex match."""
        once = decoding.repair_text("völlig".encode("utf-8").decode("cp1252"))
        self.assertIsNotNone(once)
        self.assertIsNone(decoding.repair_text(once[0]))

    def test_refuses_the_row_it_cannot_decode(self):
        """#4978's three 0x81 bytes. cp1252 does not define that byte, so there
        is nothing to decode it *to*; leaving it alone keeps the row detectable
        and repairable later rather than destroying a character."""
        self.assertIsNone(decoding.repair_text("Alesis\x81 Studio\x81 24\x81"))

    def test_never_trades_damage_for_a_lost_character(self):
        for text in ("caf� \x95 bullet", "\x81\x8d only undefined bytes"):
            fixed = decoding.repair_text(text)
            if fixed:
                self.assertLessEqual(fixed[0].count("�"), text.count("�"))

    def test_names_the_method_it_used(self):
        self.assertEqual(decoding.repair_text("a \x95 b")[1], "cp1252-read-as-latin1")


class RepairedTest(unittest.TestCase):
    def test_counts_what_it_undid(self):
        """Silence from a run means there was nothing to fix, so the counter is
        the only thing separating "clean" from "the repair stopped working"."""
        before = sum(decoding.REPAIRS.values())
        self.assertEqual(decoding.repaired("a \x95 b"), "a • b")
        self.assertEqual(sum(decoding.REPAIRS.values()), before + 1)

    def test_leaves_clean_text_and_empty_values_alone(self):
        for value in ("", None, "ordinary prose"):
            self.assertEqual(decoding.repaired(value), value)

    def test_only_touches_the_damaged_ranges(self):
        """The reason a body with markup can be repaired one level up without
        the repair reaching into a tag."""
        markup = '<p class="x">a \x95 b</p>'
        self.assertEqual(decoding.repaired(markup), '<p class="x">a • b</p>')
