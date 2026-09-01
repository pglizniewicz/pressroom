"""Every scraper's --offline, with the network refused.

This is the manual ritual CLAUDE.md documents under "Working here", as a test.
It is here rather than in `pressroom-verify-names` because it is not static: it
*runs* each scraper, and that is the point. Four of the five bugs the 2026-08-26
restructuring introduced were the same mistake - a `no_crawl` guard applied to a
scraper's *first* discovery channel and not its second - and three of the four
were findable only this way.

Two things make it work, and both were learned the hard way:

  - the probe raises a **BaseException** (support.no_network). Half these fetch
    sites wrap their call in `except Exception` and degrade gracefully, so an
    Exception is swallowed and the source reports clean. The first version of
    this probe did exactly that and pronounced five crawling sources offline.
  - the guard belongs on the **candidate list**, not on the fetch. An empty
    candidate list leaves the loop body untouched, which is what keeps this a
    one-line change per source - and `creative`'s year loop is a computed range,
    so there was nothing for the usual `[] if no_crawl` idiom to empty.

The database is a throwaway: with no rows, phase 2 has nothing to do and what is
under test is phase 1's discovery.
"""

import contextlib
import io
import sys
import tomllib
import unittest
from importlib import import_module

from tests import support

PYPROJECT = support.HERE.parent / "pyproject.toml"

# The commands that are not scrapers: two readers, three verify passes, two
# calibration passes. They take no --offline and have no discovery phase.
NOT_A_SCRAPER = {
    "pressroom-search",
    "pressroom-serve",
    "pressroom-verify-names",
    "pressroom-verify-body-origin",
    "pressroom-verify-encoding",
    "pressroom-calibrate-containers",
    "pressroom-calibrate-attachments",
}


def console_scripts() -> dict:
    return tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]


# The per-source arguments a command cannot run without. One entry, and both of
# its values are exercised: terratec-cms takes the language as a *positional*
# because the two sites are separate source tags whose text genuinely differs,
# so there is no sensible default to pick.
POSITIONALS = {"pressroom-terratec-cms": (["en"], ["de"])}


def scrapers() -> dict:
    return {
        name: target
        for name, target in console_scripts().items()
        if name not in NOT_A_SCRAPER
    }


def invocations(flag: str):
    """(command, target, argv) for every scraper, one per required-argument
    combination."""
    for name, target in sorted(scrapers().items()):
        for extra in POSITIONALS.get(name, ([],)):
            yield name, target, list(extra) + [flag]


class OfflineTest(support.DbCase):
    def _run(self, target: str, *argv):
        module_name, func = target.split(":")
        main = getattr(import_module(module_name), func)
        saved = sys.argv
        sys.argv = ["test"] + list(argv)
        try:
            with contextlib.redirect_stdout(io.StringIO()), support.no_network():
                main()
        finally:
            sys.argv = saved

    def test_every_scraper_touches_nothing_on_the_network(self):
        for name, target, argv in invocations("--offline"):
            with self.subTest(command=name, argv=argv):
                self._run(target, *argv)

    def test_retext_needs_nothing_but_the_database(self):
        for name, target, argv in invocations("--retext"):
            with self.subTest(command=name, argv=argv):
                self._run(target, *argv)

    def test_seed_cache_discovers_nothing(self):
        """--seed-cache fetches captures for rows that already exist and
        discovers nothing, so on an empty database it must make no request
        either."""
        for name, target, argv in invocations("--seed-cache"):
            with self.subTest(command=name, argv=argv):
                self._run(target, *argv)

    def test_a_command_that_cannot_be_invoked_is_a_failure_not_a_skip(self):
        """argparse exits the process on a missing required argument, so a
        scraper that grew one would drop out of the loops above as a SystemExit
        rather than as a red test. POSITIONALS is what keeps them runnable, and
        this is what notices when it goes stale."""
        for name, target, argv in invocations("--offline"):
            with self.subTest(command=name):
                try:
                    self._run(target, *argv)
                except SystemExit as e:
                    self.fail(f"{name} {argv} exited {e.code}: add it to POSITIONALS")

    def test_all_sixteen_scrapers_are_covered(self):
        """A scraper added without an --offline path would silently drop out of
        the loops above by not being in pyproject; this is the count that
        notices."""
        self.assertEqual(len(scrapers()), 16)


class NoCrawlBranchTest(support.DbCase):
    def test_soundonsound_substitutes_a_dict_not_a_list(self):
        """Its no-crawl branch substituted an empty *list* where the next line
        called .values() on a dict, so every no-network run of that source was
        an AttributeError - which the loops above would catch, but naming it
        here says which line to look at."""
        from pressroom.soundonsound.control import magazine

        with contextlib.redirect_stdout(io.StringIO()), support.no_network():
            magazine.scrape(catch={"offline": True, "catch_up": True})


class NetworkRefusalTest(unittest.TestCase):
    """The probe itself, since a probe that cannot fire proves nothing."""

    def test_a_request_inside_the_block_raises(self):
        import requests

        with self.assertRaises(support.NetworkTouched):
            with support.no_network():
                requests.get("http://127.0.0.1:1/")

    def test_it_is_not_an_exception_so_a_graceful_degrade_cannot_swallow_it(self):
        import requests

        with self.assertRaises(support.NetworkTouched):
            with support.no_network():
                try:
                    requests.get("http://127.0.0.1:1/")
                except Exception:
                    self.fail("the probe was swallowed by `except Exception`")

    def test_a_raw_socket_is_refused_too(self):
        import socket

        # A socket of our own rather than create_connection()'s: that one closes
        # what it built only on OSError, and NetworkTouched is not one, so the
        # test used to prove its point and leak a descriptor doing it.
        with socket.socket() as sock:
            with self.assertRaises(support.NetworkTouched):
                with support.no_network():
                    sock.connect(("127.0.0.1", 1))

    def test_the_originals_come_back_afterwards(self):
        import requests
        import socket

        before = (requests.Session.request, socket.socket.connect)
        with support.no_network():
            pass
        self.assertEqual((requests.Session.request, socket.socket.connect), before)
