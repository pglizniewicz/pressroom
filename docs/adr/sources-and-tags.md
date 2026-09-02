# Sources, tags and the two axes

What a source tag identifies, why duplication across tags is intended, and why
the corpus is read along a second axis than the one a scraper is debugged along.

**A source tag identifies a scraper — a CMS generation — not a domain.** Six
scrapers cover several hosts each: `media_pr` has three start urls in one
`DOMAINS`, one CMS, one parser, and stamps three tags. The tag answers the
question it exists for: what did this scraper work on, what does it hold, what
could it still fetch, what did it skip.

**One crawler owns one pool of urls.** Two commands used to write the tag
`terratec`: one crawled the `.net` article folder through CDX, the other read the
archived index pages and contributed to `.net` the articles the first never
found. Both were right about the corpus and wrong about the arrangement. The
dedup existed in one of them and ran one way only — the index crawler skipped
what the folder crawler already held, and the folder crawler compared nothing but
the exact url — so the same article stored twice was a question of which command
someone ran first, with no error either way.

**What made that invisible is that one host has three spellings.** CDX reports
`www.terratec.de:80/…`; an index page's links resolve to `www.terratec.de/…`
with no port; and a CDX prefix query returns `terratec.de:80/…` as well, because
it matches the SURT string and SURT canonicalises a `www.` away. `releases.url`
is UNIQUE and compared verbatim, and nothing in this tree normalises a url — so
`already_stored()` could not see rows it already held, and a merge that trusted
it would have re-stored most of a tag. **The filename, lowercased, is the only
key the two channels share**, which is why the already-stored check for this
source is by filename and sits above the loop rather than inside it. It is also
what makes the two channels safe in either order.

**Both channels stay, because each finds files the other does not.** The folder
listing needs no index capture to have survived, and it retired a hand-pasted
list of urls that a timemap diff had once produced by hand — that list *was* this
query. The index pages name releases whose own page the archive never captured,
which is a row with a real title and date and no body, and CDX cannot name those
at all. One more trap on the way in: a prefix matches as a string, not as a path
segment, so the listing for `/presse/pressemit/` also carries
`/presse/pressemit.htm` — the index page itself, which the html filter is happy
with and which would otherwise be stored as a release.

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

**A language split and a mirror split are not the same question, and `twin.py`
is what tells them apart.** The rule two tags buy is the refusal to pair across
them. Across mirrors that refusal is load-bearing: the same release on
midiman.net and midiman.com carries the same title and the same date, so one tag
there would put real duplicates in one `twin.fill` group. Across languages it is
idle, because the title differs for the reason the tag existed — the language.
So **merging the ten domain-only tags stays undone** (it would also move the
panel, the audit, `taxonomy/entity/company.py` and five `checks.md` fixtures),
and the language pairs are a different decision, taken once.

**`pressemit` took it.** Its two tags were one generation on two hosts, which the
tag rule says is one tag. What the measurement asked, before anything was
changed, was three things:

- **Would `twin.fill` start pairing?** Two (collapsed title, exact date) groups
  span the two hosts, both a German/English pair with a German-looking title, and
  every row in them is far above `SHORT` — so `find_pairs` rejects both, and
  `pressemit` passes no `twins_too` anyway. Latent, not live.
- **Does one parser reproduce both templates?** The two differed only in the
  dateline and the bold markers, and `BOLD_MARKERS_DE` was already the union of
  the markers. Run against the split parser over every cached capture of both
  hosts, field by field: **220 identical, 1 filled, 0 changed** — the filled one
  a German page carrying the *English* dateline, whose date the German-only
  pattern never found. The four French `*_fr.htm` pages, which the marker rule
  exists to protect, are in that 220.
- **What does the dedup key do when the tag stops being one host?** This is the
  one that bit: **58 of the generation's 166 filenames exist on both hosts**,
  because the German copy of a release keeps the release's filename. A check
  keyed on the filename alone — which is what it was when each host had its own
  tag — reports all 58 German copies already-stored, silently. The key gained
  the site as its first half (`pressemit.site_of`, partitioning by url path),
  and `tests/sources/terratec/test_pressemit.py` is what holds it there.

**The other two language pairs are the same class and differ only in that third
question.** `terratec_new_en`/`_de` (`cms.py`) share no url key and no filename
at all — the language is in the path on one host — so nothing needs
partitioning. `terratec_pressen`/`_pressde` (`portal.py`) share **83 of 236
`sid`s**, and the `sid` is that scraper's dedup key in three discovery channels,
so the partition would have to run through the whole module. Neither is done;
what the record establishes is that the question to ask first is always the
third one.

That makes `source` the right axis for debugging a scraper and the wrong one for
reading the corpus, so **`taxonomy/entity/company.py` owns a second axis**: firms
over the 24 tags, shown by default with the flat tag list behind a switch. It is
an explicit table, never a prefix rule — an unmapped source lands in a visible
`inne` bucket rather than vanishing from the counts. `browser/boundary/http.py`
translates a company to a list of sources, so no SQL knows what a company is.
