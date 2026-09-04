# The gates

Nothing is written without passing one. Two gates, four attempts between them,
and every allowance named and bounded.

## The release gate

**Nothing is written without passing a gate in `release/control/gate.py`**, and
`safe_to_write` refuses exactly one thing — text disappearing from the **middle**
of a body. Three measurements shaped it:

- a word-multiset comparison is useless here: it flags every join the fix causes
  (`8 th` -> `8th`, both correct) as loss. `text_delta` compares
  word-*character sequences* and strips `to_text()`'s ordinals.
- `kept` and `clean` are not enough: **`kept=False, clean=True` is the signature
  of both "nav was correctly dropped" and "a paragraph went missing"**.
  `edges_only` separates them — chrome is at the edges, a lost paragraph does
  not.
- two middle-loss cases are allowed by name, each confirmed by a word diff: a
  teaser-grade body replaced by the real article, and a loss under 2%, which
  every time was a URL path or image alt text sitting mid-page.

Where the per-row test cannot decide, aggregate review does: across 215 terratec
captures the removed prefixes were image alt text and URL paths, and **not one
row lost a suffix** — which is what rules out article text having been trimmed
off the end.

## The attachment conversion gate

**The conversion gate is a multiset of word characters, and it is the fourth
attempt.** `gate.wordchars` drops indentation, wrapping, bullets and ordinals;
comparing the *multiset* ignores the two things a converter may change —
order and joins — while still refusing a document that lost a paragraph.
Character *sequence* refused 13 of 73 documents for pure reordering, and word
coverage refused 10 more for joins (`Composer` + `®` + `system` arriving as
`Composer®system`). Two allowances, named and bounded: the rotated banner the
converter says it dropped, and **markers that became structure**, capped at two
characters per list item so it can never permit a missing word.
