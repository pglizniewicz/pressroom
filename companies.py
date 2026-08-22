#!/usr/bin/env python3
"""Which company each `source` tag belongs to.

`releases.source` is per-domain, not per-brand, on purpose: the same release
genuinely exists on several mirrors under unrelated URL schemes, and there is no
reliable cross-domain dedup key, so `midiman_net_pressdb` and
`midiman_com_pressdb` are separate sources by design. That makes 25 tags - a
useful axis when you are debugging a scraper, and the wrong one when you are
looking for what a company announced. This module owns that second axis, and
nothing else: no SQL, no HTTP, stdlib only.

The mapping is an explicit table rather than a prefix rule ("everything
starting with terratec_"). A prefix rule looks cheaper right up to the next
scraper, where it silently files a new source under the wrong company - or
under none - and nothing fails. A table is reviewable, and `company_of()`
reports what it does not recognise instead of guessing.

Two groupings that are decisions, not data:

- **Midiman and M-Audio are one company.** Midiman renamed itself M-Audio
  around 2002 and kept publishing to the same hosts: midiman.com serves press
  releases signed "M-Audio", m-audio.com serves ones signed Midiman. Splitting
  by domain would not split by brand, only by CMS generation.
- **`creative_gnw` belongs to Creative.** It is Creative's own newsroom feed
  hosted on GlobeNewswire - a wire service, not a company of its own.
- **Sound on Sound is a publisher, and gets its own entry anyway.** It is the
  first row in this axis that is not a hardware maker, and the only one whose
  articles are *about* many manufacturers rather than issued by one. Filing it
  under any existing firm would be a lie, and leaving it out of the table is
  not an option: an unmapped source falls through to UNKNOWN, and `"inne"` is
  not a key of COMPANIES, so serve._sources() answers HTTP 400 the moment
  anyone clicks it in the panel. The axis is labelled "firmy" in the browser,
  which is now slightly loose - a magazine among manufacturers - and that is
  the cheaper inaccuracy.
"""

# Declaration order is documentation (roughly: chip vendors, then the audio
# brands); callers that care about size sort by count, which is what roll_up
# does, so this order never has to be maintained against the corpus.
COMPANIES = {
    "intel": ("Intel", ["intel"]),
    "amd": ("AMD", ["amd"]),
    "creative": ("Creative", ["creative", "creative_gnw"]),
    "terratec": ("TerraTec", [
        "terratec",
        "terratec_de",
        "terratec_early",
        "terratec_new_de",
        "terratec_new_en",
        "terratec_pressde",
        "terratec_pressen",
    ]),
    "soundonsound": ("Sound on Sound", ["soundonsound"]),
    "maudio": ("Midiman / M-Audio", [
        "maudio_com_media_news",
        "maudio_com_media_pr",
        "maudio_com_news",
        "midiman_com",
        "midiman_com_media_news",
        "midiman_com_media_pr",
        "midiman_com_pressdb",
        "midiman_couk_news",
        "midiman_de",
        "midiman_net",
        "midiman_net_media_news",
        "midiman_net_media_pr",
        "midiman_net_pressdb",
    ]),
}

# Where a source lands when the table has not been told about it - a new
# scraper, or a renamed tag. It must stay visible: a source that quietly
# vanished from the company view would make every count on screen a lie.
UNKNOWN = "inne"
UNKNOWN_LABEL = "Nieprzypisane"

_OF_SOURCE = {src: slug for slug, (_, sources) in COMPANIES.items() for src in sources}


def label(slug: str) -> str:
    """Human-readable company name for a slug, including UNKNOWN."""
    if slug == UNKNOWN:
        return UNKNOWN_LABEL
    return COMPANIES[slug][0] if slug in COMPANIES else slug


def company_of(source: str) -> str:
    """The company slug a source belongs to, or UNKNOWN if the table has no
    entry - never a guess derived from the tag's spelling."""
    return _OF_SOURCE.get(source, UNKNOWN)


def sources_for(slugs) -> list:
    """Every source tag belonging to any of `slugs`, in table order.

    This is the whole company->query translation: a company filter is just the
    source filter db.search_releases() already implements, so no new SQL exists
    for it. Unknown slugs contribute nothing; callers validate first (serve.py
    answers 400) rather than silently searching the entire corpus.
    """
    out = []
    for slug in slugs or ():
        for src in COMPANIES.get(slug, ("", []))[1]:
            if src not in out:
                out.append(src)
    return out


def unknown_slugs(slugs) -> list:
    """Which of `slugs` are not companies - for the caller's error message."""
    return [s for s in slugs or () if s not in COMPANIES]


_SUMS = ("count", "teaser", "short", "nodate", "mojibake", "plain")


def roll_up(source_rows) -> list:
    """Fold db.list_sources() rows into one row per company.

    Takes the rows rather than a connection so this module stays free of the
    database concern: the counts and gap columns are whatever list_sources
    already computed, summed, with the date span widened across the company's
    mirrors. Sorted by size, biggest first, like list_sources itself.

    Each company row carries its own `sources` list, so one request can feed
    both the company panel and the per-source view it toggles to.
    """
    by_slug = {}
    for row in source_rows:
        slug = company_of(row["source"])
        agg = by_slug.setdefault(slug, {
            "company": slug, "label": label(slug), "sources": [],
            "first": "", "last": "", **{k: 0 for k in _SUMS},
        })
        agg["sources"].append(row)
        for key in _SUMS:
            agg[key] += row.get(key, 0)
        # Empty strings are "no date at all" and must not win a min().
        if row["first"] and (not agg["first"] or row["first"] < agg["first"]):
            agg["first"] = row["first"]
        if row["last"] and (not agg["last"] or row["last"] > agg["last"]):
            agg["last"] = row["last"]
    return sorted(by_slug.values(), key=lambda c: -c["count"])
