# Sources, tags and the two axes

What a source tag identifies, why duplication across tags is intended, and why
the corpus is read along a second axis than the one a scraper is debugged along.

**A source tag identifies a scraper — a CMS generation — not a domain.** Six
scrapers cover several hosts each: `media_pr` has three start urls in one
`DOMAINS`, one CMS, one parser, and stamps three tags. The tag answers the
question it exists for: what did this scraper work on, what does it hold, what
could it still fetch, what did it skip.

**So a file read off a sibling domain is not a cross-source claim** — treating
one as foreign is what left a block of attachment rows without provenance for a
day. Two different permissions, worth keeping straight: the host is one of that
scraper's own start urls (nothing to justify), or it is not, and then the
permission is `MIRROR_DOMAINS` — an attachment-level claim that the three hosts
served the same release *files*, backed per row rather than on faith: identical
path, host swapped, extracted text character-identical to what the row held.
Rows that looked like that class and were not held the origin server's `509
Bandwidth Limit Exceeded` page, so `attachment_captures` checks magic bytes
before recording a candidate.

**Duplication across tags is intended.** The same release lives on several
mirrors under unrelated URL schemes and their bodies are separate extractions,
which is why `twin.py` refuses to pair across tags — within one tag it fills a
teaser, across tags it would invent a fact.

**A duplicate inside one source can be a repair, not a bug.** The CMS sometimes
published one release under two URL schemes on one domain
(`news/en_us-596.html` and `index.php?do=media.new&ID=596`), one a blurb and one
the article. `twin.fill` fills the short one from its twin with **no network at
all**, and never deletes or merges: both URLs really existed, so both rows stay
and only `body` (and `detail_id`, to keep provenance honest) changes. Pairing
needs source + collapsed title + an exact, non-empty date, because this CMS
reused headlines across years.

**Merging the ten domain-only tags is a separate, mechanical change and has not
been done.** The `_de`/`_en` splits are right — the text differs. Collapsing the
ten would move the panel, the audit, `taxonomy/entity/company.py` and five
`checks.md` fixtures.

That makes `source` the right axis for debugging a scraper and the wrong one for
reading the corpus, so **`taxonomy/entity/company.py` owns a second axis**: firms
over the 25 tags, shown by default with the flat tag list behind a switch. It is
an explicit table, never a prefix rule — an unmapped source lands in a visible
`inne` bucket rather than vanishing from the counts. `browser/boundary/http.py`
translates a company to a list of sources, so no SQL knows what a company is.
