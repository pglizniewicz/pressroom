"""The one place a scraper's command line is assembled.

Sixteen scrapers each carried a hand-written `__main__` and eleven were the same
five lines. What is worth asserting about the replacement is the two couplings
it introduced, both of which fail quietly: an Option's `dest` is the keyword the
crawl receives, and the description comes from a docstring that must be taken
whole.
"""

import argparse
import unittest

from pressroom.scraping.boundary import command


class OptionTest(unittest.TestCase):
    def test_dest_follows_argparse_s_own_rule(self):
        """`dest` is what the crawl receives, so the flag and the parameter it
        feeds are named the same thing on purpose - the first long flag wins and
        dashes become underscores."""
        self.assertEqual(command.Option("--seed-cache").dest, "seed_cache")
        self.assertEqual(command.Option("-l", "--limit").dest, "limit")
        self.assertEqual(command.Option("lang").dest, "lang")
        self.assertEqual(command.Option("--x", dest="explicit").dest, "explicit")

    def test_the_shared_options_are_named_after_their_parameters(self):
        self.assertEqual(command.LIMIT.dest, "limit")
        self.assertEqual(command.PAGES.dest, "pages")
        self.assertEqual(command.START.dest, "start")


class SummaryTest(unittest.TestCase):
    def test_the_whole_first_paragraph_is_used(self):
        """`splitlines()[0]` drops the second half of every two-line summary and
        four of these sources have one - silently, since a truncated --help is
        still a --help."""
        doc = (
            "Scraper for TerraTec's 2007-2013 CMS-era press site\n"
            "(terratec.net/en/company/press/ and /de/unternehmen/presse/).\n"
            "\n"
            "Usage:\n  pressroom-terratec-cms en\n"
        )
        got = command._summary(doc)
        self.assertIn("2007-2013 CMS-era press site", got)
        self.assertIn("unternehmen/presse", got)
        self.assertNotIn("Usage", got)

    def test_a_missing_docstring_is_not_a_crash(self):
        self.assertEqual(command._summary(None), "")
        self.assertEqual(command._summary(""), "")

    def test_every_scraper_boundary_has_one(self):
        """The description --help shows. A boundary whose docstring lost its
        first paragraph would print nothing and nobody would notice."""
        import tomllib
        from importlib import import_module
        from tests import support

        scripts = tomllib.loads((support.HERE.parent / "pyproject.toml").read_text())[
            "project"
        ]["scripts"]
        for name, target in sorted(scripts.items()):
            with self.subTest(command=name):
                module = import_module(target.split(":")[0])
                self.assertTrue(
                    command._summary(module.__doc__), f"{name} has no summary paragraph"
                )


class RunTest(unittest.TestCase):
    def test_the_declared_options_reach_the_crawl_as_keywords(self):
        seen = {}

        def crawl(**kw):
            seen.update(kw)

        parser = argparse.ArgumentParser()
        for option in (command.PAGES, command.START):
            option.add_to(parser)
        command.catch_up.add_flags(parser)
        args = parser.parse_args(["--pages", "5", "--offline"])
        crawl(
            catch=command.catch_up.options(args),
            **{o.dest: getattr(args, o.dest) for o in (command.PAGES, command.START)},
        )

        self.assertEqual(seen["pages"], 5)
        self.assertEqual(seen["start"], 1)
        self.assertTrue(seen["catch"]["offline"])

    def test_every_scraper_gets_the_catch_up_flags_for_free(self):
        parser = argparse.ArgumentParser()
        command.catch_up.add_flags(parser)
        args = parser.parse_args([])
        for flag in (
            "force",
            "yes",
            "retext",
            "offline",
            "seed_cache",
            "no_catch_up",
            "attachments",
        ):
            with self.subTest(flag=flag):
                self.assertFalse(getattr(args, flag))
