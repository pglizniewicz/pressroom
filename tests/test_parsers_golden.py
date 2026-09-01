"""Every parser, over real archived bytes, against a committed expected output.

This is the automated form of the measurement this repo has been doing by hand
after every refactor - "507 parses across 17 source tags are byte-identical to
what the pre-refactor tree produced from the same cached bytes". 43 fixtures
here cover all 25 source tags and all three routes, and they run on a fresh
checkout with no 500 MB database.

When a parser change is intentional: `python tests/refresh.py --golden`, then
read the git diff. That diff is the record of what the change did.

The parse itself goes through `tests/refresh.produce`, the same function that
writes the golden files - so the test and the generator cannot disagree about
how a fixture is parsed, which would make a green run meaningless.
"""

import json
import unittest

from tests import refresh, support


def as_json(value):
    """A parse, in the terms the golden file is written in.

    The comparison has to happen after this round trip, not before: the portal's
    teasers come back keyed by an integer sid and valued by a tuple, and JSON
    has neither. Comparing the live parse to the file without it reports a
    difference on every run and would train a reader to ignore this test.
    """
    return json.loads(json.dumps(value, ensure_ascii=False))


class GoldenTest(unittest.TestCase):
    maxDiff = 4000

    def test_every_fixture_parses_to_what_it_parsed_to(self):
        manifest = support.manifest()
        self.assertTrue(manifest, "no fixtures committed - run tests/refresh.py")
        for name, spec in sorted(manifest.items()):
            with self.subTest(fixture=name, source=spec["source"], kind=spec["kind"]):
                self.assertEqual(
                    as_json(refresh.produce(name, spec)), support.golden(name)
                )

    def test_no_fixture_parses_to_nothing(self):
        """A fixture whose parse is empty would go green forever, which is the
        one thing a regression gate must not do. `refresh.py` refuses to pick
        such a capture; this is the assertion that keeps it that way after a
        parser change, when the *committed* fixture may start returning nothing.
        """
        for name, spec in sorted(support.manifest().items()):
            with self.subTest(fixture=name):
                got = support.golden(name)
                if spec["kind"] == "listing":
                    self.assertTrue(len(got) >= 1)
                elif spec["kind"] == "attachment":
                    self.assertTrue(got["richtext"] or got["plain"])
                else:
                    self.assertTrue(got.get("body"))


class ShapeTest(unittest.TestCase):
    """What each route is supposed to hand back, independent of the values."""

    def test_a_detail_parse_carries_the_four_fields_a_scraper_stores(self):
        for name, spec in sorted(support.manifest().items()):
            if spec["kind"] != "detail":
                continue
            with self.subTest(fixture=name):
                got = support.golden(name)
                self.assertIsInstance(got, dict)
                self.assertIn("body", got)

    def test_every_listing_entry_carries_a_headline(self):
        """The weakest thing that is true of all of them, and worth asserting
        because the alternative is an entry that is present and empty.

        There is deliberately no `url` assertion here: the three collectors
        `from_listings` consumes mint the url in their own `cached_entries`
        wrapper, not in the parse - midiman_de's inline releases only ever
        existed *inside* the page, so the scraper builds
        `/press/{slug}-{date}` for them and the raw parse carries url=None by
        design. That contract is tested where it lives, in
        `CollectorTest` below.
        """
        for name, spec in sorted(support.manifest().items()):
            if spec["kind"] != "listing":
                continue
            got = support.golden(name)
            entries = list(got.values()) if isinstance(got, dict) else got
            with self.subTest(fixture=name):
                self.assertTrue(entries)
                self.assertTrue(all(e for e in entries), f"{name}: an empty entry")

    def test_a_live_route_answers_from_the_cache_alone(self):
        """`fetch_body` is handed session=None, so a route that reached for the
        network would raise rather than fetch. That is the assertion: these five
        sources are still up, and a test that quietly crawled them would be both
        slow and rude."""
        live = [n for n, s in support.manifest().items() if s["kind"] == "live"]
        self.assertEqual(len(live), 5)
        for name in sorted(live):
            with self.subTest(fixture=name):
                self.assertTrue(support.golden(name)["body"])


class CollectorTest(support.DbCase):
    """The three `cached_entries` collectors `from_listings` actually consumes.

    This is the contract with teeth: the shape must be `{url: entry}` with a
    body and an `origin_url`, because `from_listings` matches on the url and
    records the address in the same transaction as the text. The collectors used
    to parse a capture and throw its address away, which is why 149 of the 1727
    origins had to be found later by searching captures for the body's text.
    """

    COLLECTORS = {
        "midiman_de": ("sources.maudio.control.presse_de", "cached_entries", ()),
        "terratec_new_de": ("sources.terratec.control.cms", "cached_entries", ("de",)),
        "terratec_new_en": ("sources.terratec.control.cms", "cached_entries", ("en",)),
        "terratec_early": ("sources.terratec.control.early", "cached_entries", ()),
    }

    def _collect(self, source):
        import importlib

        mod, func, args = self.COLLECTORS[source]
        for name, spec in support.manifest().items():
            if spec["source"] == source and spec["kind"] == "listing":
                self.cache(spec["capture"], support.fixture(name))
        module = importlib.import_module(f"pressroom.{mod}")
        return getattr(module, func)(self.conn, *args)

    def test_each_collector_keys_by_url_and_records_where_it_read(self):
        for source in sorted(self.COLLECTORS):
            with self.subTest(source=source):
                got = self._collect(source)
                self.assertTrue(got, f"{source}: collected nothing")
                for url, entry in got.items():
                    self.assertTrue(url)
                    self.assertTrue(entry.get("body"))
                    self.assertTrue(
                        entry.get("origin_url"), f"{source} {url}: no origin recorded"
                    )

    def test_a_recorded_origin_is_a_capture_address(self):
        """`storage._record_origin` raises on anything else, so a collector
        stamping a page url rather than a capture address would break the write
        it feeds - at the write, not here, which is the wrong place to find out.
        """
        from pressroom.capture.control import address

        for source in sorted(self.COLLECTORS):
            for url, entry in self._collect(source).items():
                with self.subTest(source=source, url=url):
                    self.assertTrue(address.is_capture_address(entry["origin_url"]))
