"""The three rules this repo deliberately spells out twice.

Each pair is documented as "change one and change the other", which is a comment
asking a human to remember something - and one of the three had already drifted
before anyone noticed: the audit view reported 12 encoding-damaged rows where
the repair found 35, because the SQL named five C1 codepoints by hand while the
regex had always matched the whole 0x80-0x9F range.

A comment cannot fail. These can.
"""

import json
import pathlib
import re
import unittest

from pressroom.attachment.control import conversion
from pressroom.browser import boundary
from pressroom.release.entity import schema
from pressroom.text.control import decoding, richtext
from tests import support

# The browser's three files travel with the package as package data, so this is
# where they are - not a path relative to the checkout.
APP_JS = pathlib.Path(boundary.__file__).resolve().parent / "static" / "app.js"


class EncodingRuleTest(support.DbCase):
    """`decoding.C1_RE`/`MOJIBAKE_RE` against `schema.MOJIBAKE_SQL`.

    Two languages, one rule - SQLite has no regex, so the audit view needs the
    rule as a WHERE clause. The counts they produce are compared on every run of
    `pressroom-verify-encoding` for exactly this reason; here the comparison is
    per row and over the whole range rather than over whatever the corpus
    happens to contain.
    """

    def _damaged_by_sql(self):
        return {
            r[0]
            for r in self.conn.execute(
                f"SELECT url FROM releases r WHERE {schema.MOJIBAKE_SQL}"
            )
        }

    def _damaged_by_regex(self):
        return {
            url
            for url, body in self.conn.execute("SELECT url, body FROM releases")
            if decoding.C1_RE.search(body or "")
            or decoding.MOJIBAKE_RE.search(body or "")
        }

    def test_the_two_agree_over_the_whole_c1_range(self):
        for code in range(0x80, 0xA0):
            self.seed(
                "src",
                url=f"http://x/c1-{code:02x}",
                body=f"text {chr(code)} more",
                body_html=f"<p>text {chr(code)} more</p>",
            )
        self.assertEqual(len(self._damaged_by_sql()), 32)
        self.assertEqual(self._damaged_by_sql(), self._damaged_by_regex())

    def test_the_two_agree_on_mojibake_and_on_clean_rows(self):
        self.seed(
            "src",
            url="http://x/moji",
            body="TerraTec â€ž Cinergy",
            body_html="<p>x</p>",
        )
        self.seed("src", url="http://x/atilde", body="vÃ¶llig", body_html="<p>x</p>")
        self.seed(
            "src",
            url="http://x/clean",
            body="völlig ordinary „quoted“",
            body_html="<p>x</p>",
        )
        self.assertEqual(self._damaged_by_sql(), self._damaged_by_regex())
        self.assertNotIn("http://x/clean", self._damaged_by_sql())

    def test_the_c1_half_is_generated_rather_than_typed_out(self):
        """It named five codepoints by hand once, and under-reported by 23 rows.
        32 instr() calls, one per codepoint in the range the regex matches."""
        self.assertEqual(schema.C1_SQL.count("instr("), 0xA0 - 0x80)


class AllowlistTest(unittest.TestCase):
    """`richtext._ALLOWED`/`_ATTRS` against `RICH_TAGS` in app.js.

    The frontend rebuilds every node one by one instead of trusting innerHTML,
    so it needs its own copy of the allowlist. A tag that Python emits and the
    browser does not know is silently unwrapped on screen; one the browser
    allows and Python never emits is dead code that looks like policy.
    """

    def _rich_tags(self):
        source = APP_JS.read_text()
        body = re.search(r"const RICH_TAGS = \{(.*?)\n\};", source, re.S).group(1)
        out = {}
        for tag, attrs in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", body):
            out[tag] = [a.strip().strip('"') for a in attrs.split(",") if a.strip()]
        return out

    def test_the_two_tag_sets_are_identical(self):
        self.assertEqual(set(self._rich_tags()), richtext._ALLOWED)

    def test_the_per_tag_attributes_match(self):
        js = self._rich_tags()
        for tag, attrs in js.items():
            with self.subTest(tag=tag):
                self.assertEqual(set(attrs), richtext._ATTRS.get(tag, set()))

    def test_the_safe_scheme_rule_admits_and_refuses_the_same_things(self):
        """Compared by behaviour rather than by spelling: the JS writes
        `https?:` where Python lists http: and https: separately, and what has
        to agree is which addresses survive - relative paths harmless, and
        javascript:/data: gone."""
        js = re.search(r"const SAFE_SCHEME = (/.*?/)i;", APP_JS.read_text()).group(1)
        pattern = re.compile(js.strip("/"), re.I)
        for scheme in richtext._SAFE_SCHEMES:
            with self.subTest(scheme=scheme):
                self.assertTrue(pattern.match(scheme + "//x"))
                self.assertTrue(richtext._safe_url(scheme + "//x"))
        for bad in ("javascript:evil()", "data:image/gif;base64,AA"):
            with self.subTest(value=bad):
                self.assertIsNone(pattern.match(bad))
                self.assertFalse(richtext._safe_url(bad))


class AttachmentExtensionTest(unittest.TestCase):
    """`conversion.ATTACHMENT_EXTS` against the LIKE clauses in FLAG_SQL['plain'].

    `schema.py` may not import a parser, so the rule about which urls name a
    file rather than a page is written twice. If they drift, the audit view
    counts attachment rows as outstanding formatting work - which it did, for
    358 rows of 1706.
    """

    def test_every_extension_is_excluded_by_the_plain_flag(self):
        clause = schema.FLAG_SQL["plain"]
        for ext in conversion.ATTACHMENT_EXTS:
            with self.subTest(ext=ext):
                self.assertIn(f"'%{ext}'", clause)

    def test_the_sql_excludes_nothing_else(self):
        excluded = re.findall(r"NOT LIKE '%\.\w+'", schema.FLAG_SQL["plain"])
        self.assertEqual(len(excluded), len(conversion.ATTACHMENT_EXTS))


class FixtureManifestTest(unittest.TestCase):
    """The fixtures themselves: every manifest entry has its bytes and its
    expected output, and nothing is committed that nothing reads."""

    def test_every_fixture_has_captures_and_golden(self):
        manifest = support.manifest()
        self.assertTrue(manifest, "no fixtures committed")
        for name, spec in sorted(manifest.items()):
            with self.subTest(fixture=name):
                self.assertTrue(support.fixture(name))
                self.assertTrue((support.GOLDEN / f"{name}.json").exists())

    def test_no_orphan_capture_or_golden_file(self):
        manifest = support.manifest()
        wanted = {spec.get("file", n) for n, spec in manifest.items()}
        self.assertEqual({p.stem for p in support.CAPTURES.glob("*.gz")}, wanted)
        self.assertEqual({p.stem for p in support.GOLDEN.glob("*.json")}, set(manifest))

    def test_every_source_tag_has_a_fixture(self):
        """25 tags, and a tag with no fixture is a parser nothing pins."""
        from pressroom.taxonomy.entity import company

        covered = {spec["source"] for spec in support.manifest().values()}
        tags = {s for _, (_, ss) in company.COMPANIES.items() for s in ss}
        self.assertEqual(tags - covered, set())

    def test_the_manifest_is_valid_json_with_the_keys_the_loader_needs(self):
        for name, spec in support.manifest().items():
            with self.subTest(fixture=name):
                self.assertEqual(
                    set(spec) & {"source", "kind", "capture"},
                    {"source", "kind", "capture"},
                )
                json.dumps(spec)
