"""The claims the read-only passes report in prose, asserted against the real
corpus.

Skipped without `pressroom.db`, read-only always. These do not replace
`pressroom-verify-body-origin` / `-encoding` / `-names`: those print a
per-class report, which is what you read when one of these fails. What is here
is the headline each of them establishes, so it cannot quietly stop being true.

**Invariants, not tallies.** `docs/adr/numbers.md` records what happened to the
259 lines of counts this file replaced, so nothing below hardcodes 6708 or
`MobilePre 56`. A token probe
compares FTS against a scan of the same text rather than against a number
someone wrote down; the corpus can grow without turning this file red.
"""

import random
import sqlite3
import unittest

from pressroom.attachment.control import conversion
from pressroom.capture.control import address
from pressroom.provenance.boundary import verification
from pressroom.provenance.entity import origin
from pressroom.release.entity import schema
from pressroom.release.entity.grade import Grade
from pressroom.taxonomy.entity import company
from pressroom.text.control import decoding, richtext
from tests import support

# Ordinary words, not product names: what matters is that FTS and a scan agree,
# and a token that appears nowhere would make the comparison vacuous.
PROBES = ("MobilePre", "Octane", "ArKaos", "Radium", "GeForce", "press")

# The one row that provably cannot reproduce: its stored body is several
# releases concatenated by an old extraction, so rewriting it would delete text
# belonging to other rows.
KNOWN_IRREPRODUCIBLE = {6211}


@support.needs_corpus
class CorpusCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(f"file:{support.CORPUS_DB}?mode=ro", uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()


class TaxonomyTest(CorpusCase):
    def test_every_source_in_the_corpus_has_a_company(self):
        """An unmapped source lands in `inne`, which is not a key of COMPANIES -
        so its panel entry answers HTTP 400 the moment anyone clicks it."""
        unmapped = [
            s
            for (s,) in self.conn.execute("SELECT DISTINCT source FROM releases")
            if company.company_of(s) == company.UNKNOWN
        ]
        self.assertEqual(unmapped, [])

    def test_every_grade_is_in_the_closed_set(self):
        got = {g for (g,) in self.conn.execute("SELECT DISTINCT grade FROM releases")}
        self.assertEqual(got - {str(g) for g in Grade}, set())


class BodyInvariantTest(CorpusCase):
    def test_body_is_to_text_of_body_html_everywhere_it_exists(self):
        """The invariant that makes `--retext` free and safe. Verified across
        every such row rather than sampled: it is the one claim the whole
        re-extraction design rests on."""
        bad = [
            rid
            for rid, body, html in self.conn.execute(
                "SELECT id, body, body_html FROM releases WHERE body_html IS NOT NULL"
            )
            if (body or "") != richtext.to_text(html)
        ]
        self.assertEqual(bad, [])

    def test_a_pdf_or_doc_row_never_holds_a_decoded_binary(self):
        """23 rows once held `%PDF-1.3 %...` over text pdftotext had extracted
        correctly, and it read as encoding damage rather than as data loss."""
        bad = [
            rid
            for rid, body in self.conn.execute(
                "SELECT id, COALESCE(body, '') FROM releases"
            )
            if body.startswith("%PDF") or "\x00" in body[:200]
        ]
        self.assertEqual(bad, [])


class EncodingTest(CorpusCase):
    def test_nothing_stored_is_repairable(self):
        """`pressroom-verify-encoding`'s headline: 0 repairable. The repair
        lives in the write path now, so a row that arrives damaged is repaired
        on the way in - and anything left is something the repair refuses by
        design (#4978's three 0x81 bytes, which cp1252 does not define)."""
        repairable = [
            rid
            for rid, title, body in self.conn.execute(
                "SELECT id, title, body FROM releases"
            )
            if decoding.repair_text(title or "") or decoding.repair_text(body or "")
        ]
        self.assertEqual(repairable, [])

    def test_the_sql_rule_and_the_regex_agree_on_the_live_corpus(self):
        """They had drifted once: the SQL named five C1 codepoints by hand while
        the regex matched the whole range, so the audit view reported 12 damaged
        rows where the repair found 35."""
        by_sql = {
            rid
            for (rid,) in self.conn.execute(
                f"SELECT r.id FROM releases r WHERE {schema.MOJIBAKE_SQL}"
            )
        }
        rows = self.conn.execute("SELECT id, body FROM releases")
        by_regex = {
            rid
            for rid, body in rows
            if decoding.C1_RE.search(body or "")
            or decoding.MOJIBAKE_RE.search(body or "")
        }
        self.assertEqual(by_sql, by_regex)


class SearchIndexTest(CorpusCase):
    def test_the_index_agrees_with_the_content_table(self):
        """Not `'integrity-check'`, which passes on a stale index because it
        only checks internal consistency."""
        orphans = self.conn.execute(
            "SELECT count(*) FROM releases_fts f LEFT JOIN releases r"
            " ON r.id = f.rowid WHERE r.id IS NULL"
        ).fetchone()[0]
        missing = self.conn.execute(
            "SELECT count(*) FROM releases r WHERE NOT EXISTS"
            " (SELECT 1 FROM releases_fts f WHERE f.rowid = r.id)"
        ).fetchone()[0]
        self.assertEqual((orphans, missing), (0, 0))

    def test_a_token_probe_finds_what_a_scan_finds(self):
        """The real invariant behind the five token counts this repo re-checks by
        hand after every change. A count would rot; agreement does not."""
        for token in PROBES:
            with self.subTest(token=token):
                fts = {
                    r[0]
                    for r in self.conn.execute(
                        "SELECT rowid FROM releases_fts WHERE releases_fts MATCH ?",
                        (token,),
                    )
                }
                scan = {
                    r[0]
                    for r in self.conn.execute(
                        "SELECT id FROM releases WHERE lower(COALESCE(title, '')) LIKE ?"
                        " OR lower(COALESCE(body, '')) LIKE ?",
                        (f"%{token.lower()}%",) * 2,
                    )
                }
                self.assertTrue(fts, f"{token} is indexed nowhere")
                # FTS is word-bounded and the scan is a substring match, so the
                # scan is the superset: every indexed hit must appear in it.
                self.assertEqual(fts - scan, set())

    def test_the_column_order_is_still_title_then_body(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(releases_fts)")]
        self.assertEqual(cols[:2], ["title", "body"])

    def test_all_three_triggers_are_installed(self):
        got = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        self.assertEqual(got, {"releases_ai", "releases_au", "releases_ad"})


class ProvenanceTest(CorpusCase):
    def test_every_recorded_origin_is_a_capture_address(self):
        """The guard at the write site, checked from the other end: a Q4 numeric
        id or a bare row url in this column would mint a dead link."""
        bad = [
            u
            for (u,) in self.conn.execute("SELECT origin_url FROM body_origin")
            if not address.is_capture_address(u)
        ]
        self.assertEqual(bad, [])

    def test_no_live_source_row_has_a_capture(self):
        """Their detail_id is their platform's own id and never named a capture,
        so no link is the honest answer."""
        live = tuple(company.sources_for(["intel", "amd", "creative", "soundonsound"]))
        got = self.conn.execute(
            "SELECT count(*) FROM body_origin o JOIN releases r ON r.url = o.url"
            f" WHERE r.source IN ({','.join('?' * len(live))})",
            live,
        ).fetchone()[0]
        self.assertEqual(got, 0)

    def test_a_sample_of_recorded_origins_reproduces_its_body(self):
        """`pressroom-verify-body-origin`, sampled. The full pass is the command;
        this is the version that fits in a test run, and it uses the command's
        own reproduction so the two cannot disagree about what a parser is.

        Sampled deterministically (seeded) so a red run is reproducible - a
        flaky sample would be worse than no test.
        """
        rows = self.conn.execute(
            """SELECT r.id, r.source, r.url, r.detail_id, COALESCE(r.body, ''),
                      r.body_html, c.origin_url
                 FROM body_origin c JOIN releases r ON r.url = c.url
                ORDER BY r.id"""
        ).fetchall()
        by_source = {}
        for row in rows:
            by_source.setdefault(row[1], []).append(row)

        rng = random.Random(20260826)
        problems = []
        for source, group in sorted(by_source.items()):
            # terratec_early's bodies are anchors into one listing page that
            # only the url-keyed collector can separate, so a whole-page
            # reproduction says nothing about them.
            if source == "terratec_early":
                continue
            for rid, src, url, ts, body, body_html, capture in rng.sample(
                group, min(3, len(group))
            ):
                if rid in KNOWN_IRREPRODUCIBLE:
                    continue
                got = self.conn.execute(
                    "SELECT content FROM page_cache WHERE url = ?", (capture,)
                ).fetchone()
                if got is None:
                    continue  # bytes not cached: nothing to check
                content = got[0]
                if conversion.is_attachment(content):
                    v = verification.attachment_verdict(body, body_html, content)
                else:
                    page = origin.page_of(capture) or capture
                    v = verification.verdict(
                        body, verification.candidate_bodies(src, content, page, ts, url)
                    )
                if v.split()[0].isupper():
                    problems.append((rid, src, v))
        self.assertEqual(problems, [])

    def test_origin_class_is_derivable_from_the_address(self):
        """Which is what made `body_origin.matched` redundant - it agreed with
        the inferred class on all 149 rows and on no others."""
        seen = set()
        for url, ts, capture in self.conn.execute(
            "SELECT r.url, r.detail_id, c.origin_url"
            "  FROM body_origin c JOIN releases r ON r.url = c.url"
        ):
            seen.add(verification.origin_class(url, ts, capture))
        self.assertEqual(seen - {"computed", "located", "inferred"}, set())


class AttachmentTest(CorpusCase):
    def test_the_plain_flag_never_counts_an_attachment_row(self):
        """Counting them made the audit overstate the remaining formatting work
        by 358 rows of 1706, and the point of that view is that its numbers are
        not a lie."""
        counted = [
            u
            for (u,) in self.conn.execute(
                f"SELECT r.url FROM releases r WHERE {schema.FLAG_SQL['plain']}"
            )
            if conversion.is_attachment_url(u)
        ]
        self.assertEqual(counted, [])
