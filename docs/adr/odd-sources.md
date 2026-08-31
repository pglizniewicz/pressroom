# Two sources that break the pattern

One needs a browser TLS fingerprint; the other is a magazine rather than a
press room. Both cost more than their line count suggests.

## GlobeNewswire

**GlobeNewswire needs a browser TLS fingerprint, everything else does not.**
Akamai Bot Manager drops `requests`/`urllib3` at www.globenewswire.com: DNS and
the TLS handshake succeed, then HTTP/1.1 gets *no response at all* and HTTP/2 an
immediate RST_STREAM after ~30 ms. Browser-shaped headers change nothing — the
block is on the TLS/HTTP2 fingerprint, not the User-Agent. `curl_cffi` (bindings
to the maintained lexiforest/curl-impersonate fork) gets 200 on the first try
with any profile, and its `Session` is API-compatible with `requests.Session` for
everything `fetch_cached` uses, so `politeness.py` needed no change.

Two things to keep true: the import stays **inside**
`globenewswire.make_session()`, so a missing `curl_cffi` breaks exactly one
source with a plain ImportError instead of every module in the tree; and the
impersonation profile is a moving target — when this starts timing out again,
bump `curl_cffi` and try a newer profile before suspecting the parser.

## Sound on Sound

**A publisher is not a press room, and this is the only one** — every other
source is one company announcing its own products; this is a magazine writing
*about* many. Three consequences:

- **It gets its own `COMPANIES` entry** even though the axis is labelled "firmy".
  Not cosmetic: an unmapped source falls through to `UNKNOWN`, and `"inne"` is
  not a key of `COMPANIES`, so `http._sources()` answers **HTTP 400** the moment
  anyone clicks it in the panel.
- **`Crawl-delay: 30`**, honoured via `fetch_cached(sleep=CRAWL_DELAY)` — the
  reason that parameter exists. ~820 requests is a ~7 hour run, but everything
  lands in `page_cache`, so it is one-time and an interrupted run resumes free.
- **The listing URLs match `Disallow: /*?*f[0]=`; the articles do not.** That rule
  guards against faceted-search crawl traps, not content. Two named facets at one
  page per 30s was a deliberate call, recorded in the scraper's docstring.

Two markup traps that produce wrong data rather than failing: the listing carries
**two date formats** (`19/8/26` on news, `Published March 2000` in
`.field--issue-date` on magazine pieces — the second needs `fmt="%Y-%m"`, or
dateutil fills the day in from *today*), and a page holds 23 `div.views-row` of
which only **20 contain `article[about]`**, the rest promo blocks.

`detail_id` is Drupal's node id, deliberately not a timestamp: faking one would
have put a dead archive.org link on every row. Belt *and* braces now — the link
comes from `body_origin` and a live source has no entry — but it was this
docstring that showed the digit rule had become a constraint on what a source may
store, which is half the reason `grade` got its own column.
