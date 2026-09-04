# Encoding

Every rule here came out of mangled characters someone had to go and find.

**Never use `r.text`, and never let BeautifulSoup sniff.** These sites are
pre-UTF-8 or half-converted; `requests` guesses Latin-1 and bs4 falls back to
chardet, which on this corpus has picked windows-1250 and even windows-1258
(Vietnamese). Both mangle `™` (`0x99`), `„` (`0x84`) and smart quotes.
`archive.fetch_snapshot()` returns bytes so the decision is always explicit. Two
correct choices, per scraper:

- `decoding.decode_html(content)` for pages that **claim UTF-8** — UTF-8 with a
  per-byte cp1252 fallback, because a page can be UTF-8 with a few Word-pasted
  cp1252 bytes and a strict decode failing sends the whole file to chardet.
  Three stray bytes in one footer corrupted 14 titles that way.
- `BeautifulSoup(content, from_encoding="cp1252")` for sources known to be
  **wholly pre-UTF-8** (2001-era GoLive pages, midiman.de). Not `decode_html`
  there: in prose full of accents two adjacent high bytes can coincidentally form
  a valid UTF-8 sequence and would be decoded as one.

Damage is greppable: `â€` means UTF-8 read as something 8-bit; a raw C1 control
character means cp1252 read as ISO-8859-1. `page_cache` holds the original bytes,
so both are repairable with no refetch.

**The repair is in the write path, so there is nothing to re-run.** It used to be
a rule — "re-run the repair after any pass that rewrites bodies from the network"
— and the rule is what failed: a re-extraction brought 34 rows straight back
after a repair had fixed them. It has to be at the *write*, not the decode,
because some damage is upstream: ir.amd.com serves `\xc2\x99`, valid UTF-8 for
the C1 control U+0099, where it means `™`, so decoding it correctly still yields
a control character. `richtext.extract()` repairs the emitted HTML **before**
`to_text()` renders from it — `body` is by definition `to_text(body_html)`, and
repairing the two independently could break that — and
`storage.store_release`/`upgrade_release` repair a title and a body with no
markup twin. `decoding.REPAIRS` counts what was undone and `Stats.summary()`
prints it; no output means there was nothing to fix.

**A wrong decode already stored is undone in text, not refetched.** C1 characters
go back through cp1252 *per character*, because a whole-string
`encode("latin-1")` fails on the mixed rows that need it most; mojibake is
re-encoded with the charset it was misread as, accepted only when the round trip
leaves no markers and every qualifying codepage agrees. The misreads were cp1252
and, on German prose, **cp1258** — chardet's guess, not a typo. `mac-roman` is
not a candidate: it re-encodes cleanly and produces garbage. One row
is refused by design — its only damage is three 0x81 bytes, and cp1252 does not
define that byte.

**Detection is in two places** — `decoding.C1_RE`/`MOJIBAKE_RE` for
the repair, `schema.MOJIBAKE_SQL` for the audit view, because SQLite has no
regex. They had drifted (the SQL named five C1 codepoints by hand while the regex
matched the whole `0x80-0x9F` range), so `_C1_SQL` now generates all 32
`instr()` calls from the same bounds. `tests/test_mirrored_rules.py` asserts they
agree.
