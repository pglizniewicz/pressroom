#!/usr/bin/env python3
"""Which company each `source` tag belongs to.

A source tag is per-domain, not per-brand, so it is the right axis for debugging
a scraper and the wrong one for reading the corpus. This module owns the second
axis and nothing else: no SQL, no HTTP, stdlib only.

An explicit table rather than a prefix rule, which looks cheaper right up to the
next scraper, where it silently files a new source under the wrong company and
nothing fails. `company_of()` reports what it does not recognise.

Three groupings that are decisions, not data:

- **Midiman and M-Audio are one company.** The rename kept publishing to the
  same hosts, so splitting by domain would split by CMS generation, not brand.
- **`creative_gnw` belongs to Creative.** Its own newsroom feed on a wire
  service, not a company of its own.
- **Sound on Sound is a publisher, and gets its own entry anyway.** Its articles
  are *about* many manufacturers rather than issued by one, so filing it under an
  existing firm would be a lie - and leaving it out is not an option, because
  UNKNOWN is not a key of COMPANIES and the panel's link would answer HTTP 400.
  The axis is labelled "firmy", which is that much looser, and that is the
  cheaper inaccuracy.

→ docs/adr/sources-and-tags.md
"""

from typing import Any

# Declaration order is documentation (roughly: chip vendors, then the audio
# brands); callers that care about size sort by count, which is what roll_up
# does, so this order never has to be maintained against the corpus.
COMPANIES: dict[str, tuple[str, list[str]]] = {
    "intel": ("Intel", ["intel"]),
    "amd": ("AMD", ["amd"]),
    "creative": ("Creative", ["creative", "creative_gnw"]),
    "terratec": (
        "TerraTec",
        [
            "terratec",
            "terratec_early",
            "terratec_new_de",
            "terratec_new_en",
            "terratec_pressde",
            "terratec_pressen",
        ],
    ),
    "soundonsound": ("Sound on Sound", ["soundonsound"]),
    "maudio": (
        "Midiman / M-Audio",
        [
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
        ],
    ),
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


def sources_for(slugs) -> list[str]:
    """Every source tag belonging to any of `slugs`, in table order.

    A company filter is just the source filter `search_releases()` already
    implements, so no new SQL exists for it. Unknown slugs contribute nothing:
    callers validate first rather than silently searching the whole corpus.
    """
    out = []
    for slug in slugs or ():
        for src in COMPANIES.get(slug, ("", []))[1]:
            if src not in out:
                out.append(src)
    return out


def unknown_slugs(slugs) -> list[str]:
    """Which of `slugs` are not companies - for the caller's error message."""
    return [s for s in slugs or () if s not in COMPANIES]


_SUMS = ("count", "teaser", "short", "nodate", "mojibake", "plain")


def roll_up(source_rows) -> list[dict[str, Any]]:
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
        agg = by_slug.setdefault(
            slug,
            {
                "company": slug,
                "label": label(slug),
                "sources": [],
                "first": "",
                "last": "",
                **{k: 0 for k in _SUMS},
            },
        )
        agg["sources"].append(row)
        for key in _SUMS:
            agg[key] += row.get(key, 0)
        # Empty strings are "no date at all" and must not win a min().
        if row["first"] and (not agg["first"] or row["first"] < agg["first"]):
            agg["first"] = row["first"]
        if row["last"] and (not agg["last"] or row["last"] > agg["last"]):
            agg["last"] = row["last"]
    return sorted(by_slug.values(), key=lambda c: -c["count"])
