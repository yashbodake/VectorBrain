"""Tests for the chunk boilerplate filter (chunking._is_boilerplate).

Eval-driven: the Ikigai book's front/back-matter (copyright page, TOC,
bibliography) embedded semantically close to topical queries and crowded out
real content in top-k retrieval — copyright/TOC/biblio ranked #1-3, pushing
the actual answer (Ogimi/Okinawa) to rank #8, outside top_k=6. See
backend/eval/EVALUATION.md.

These tests lock the filter: noise IS filtered, real content is NEVER filtered
(no false positives that would silently drop real answers).
"""

from __future__ import annotations

from app.services.chunking import _is_boilerplate


# ---------------------------------------------------------------------------
# NOISE — must be filtered (these ranked in the top-3 for an Ikigai query)
# ---------------------------------------------------------------------------
NOISE_CHUNKS = [
    # p.3 — copyright page
    "New York, New York 10014 penguin.com\nCopyright © 2016 by Héctor García "
    "and Francesc Miralles\nTranslation copyright © 2017 by Penguin Random House LLC",
    # p.6 — table of contents. NOTE: Docling emits this with irregular
    # whitespace ("Title   Page" with multiple spaces), so plain substring
    # markers miss it. The structural _looks_like_toc detector catches it.
    "Title   Page\nCopyright\nDedication\nEpigraph\nPrologue\nIkigai: A "
    "mysterious word\nI. Ikigai\nThe art of staying young while growing old\n"
    "II. Antiaging Secrets",
    # p.119 — bibliography / references page
    "The authors of Ikigai were greatly inspired by:\n"
    "- Breznitz, Shlomo, and Collins Hemingway. Maximum Brainpower. Ballantine Books, 2012.\n"
    "- Buettner, Dan. The Blue Zones. National Geographic, 2012.\n"
    "- Csikszentmihalyi, Mihaly. Flow. Harper, 2008.",
    # legal disclaimer
    "Neither the publisher nor the author is engaged in rendering professional "
    "advice or services. No part of this book may be reproduced without the prior "
    "written permission of the publisher.",
    # tiny header chunk (under 40 real chars)
    "Chapter 1",
    "— Japanese proverb",
]


# ---------------------------------------------------------------------------
# REAL CONTENT — must NEVER be filtered (false positives would hide answers)
# ---------------------------------------------------------------------------
REAL_CHUNKS = [
    # The actual Ogimi/Okinawa answer chunks (the ones the filter must NOT drop)
    "As we explored the matter further, we discovered that one place in particular, "
    "Ogimi, a rural town on the north end of the island of Okinawa, had a remarkable "
    "concentration of centenarians.",
    "Having a clearly defined ikigai brings satisfaction, happiness, and meaning "
    "to our lives. The purpose of this book is to help you find yours.",
    "Looking back, our days in Ogimi were intense but relaxed, full of walks and "
    "talks with the centenarians about their daily habits.",
    "the joie de vivre that inspires these centenarians to keep celebrating birthdays",
    # Edge case: real prose containing "inspired by" — must NOT trip the filter
    "I was greatly inspired by my grandfather, who lived to be 102 and attributed "
    "his longevity to daily walks and a simple diet.",
    # Edge case: real prose mentioning a year — must NOT be flagged as citation list
    "He visited Japan in 2017 and again in 2019, studying the habits of long-lived "
    "communities in Okinawa and the Ryukyu islands.",
]


def test_filters_all_boilerplate_noise():
    """Every known noise chunk must be filtered out."""
    for chunk in NOISE_CHUNKS:
        assert _is_boilerplate(chunk), f"FAIL: noise chunk not filtered: {chunk[:50]!r}"


def test_keeps_all_real_content():
    """No real-content chunk may be filtered (false positive = hidden answer)."""
    for chunk in REAL_CHUNKS:
        assert not _is_boilerplate(chunk), (
            f"FAIL: real content wrongly filtered: {chunk[:50]!r}"
        )


def test_citation_list_detector_requires_multiple_citations():
    """A single year mention in body text must NOT trigger the citation-list
    detector (it needs >=3 citation-shaped lines)."""
    from app.services.chunking import _looks_like_citation_list

    # One year, body prose — NOT a citation list.
    assert not _looks_like_citation_list(
        "She moved to Tokyo in 1995 and began researching longevity."
    )
    # Three+ author-title-publisher-year lines — IS a citation list.
    assert _looks_like_citation_list(
        "- Smith, J. Title One. Publisher A, 2010.\n"
        "- Doe, A. Title Two. Publisher B, 2015.\n"
        "- Lee, K. Title Three. Publisher C, 2018."
    )


def test_toc_detector_catches_stacked_short_titles_and_keeps_real_bullets():
    """The TOC detector fires on a stack of short title-like lines (>=70% of
    lines <40 chars) but NOT on a real bulleted list with longer item text."""
    from app.services.chunking import _looks_like_toc

    # TOC: many short lines (section titles). Docling whitespace is irregular.
    toc = (
        "Title   Page\nCopyright\nDedication\nEpigraph\nPrologue\n"
        "Ikigai\n:   A   mysterious   word\nI.\nIkigai\n"
        "The art of staying young while growing old"
    )
    assert _looks_like_toc(toc)

    # Real bulleted list: items are full sentences (>40 chars each).
    real_bullets = (
        "The key practices of the Okinawan centenarians include:\n"
        "- Eating a mostly plant-based diet with plenty of vegetables and tofu\n"
        "- Maintaining strong social connections through community groups called moai\n"
        "- Walking every day, often in the hills around their village\n"
        "- Practicing moderation, stopping at eighty percent full"
    )
    assert not _looks_like_toc(real_bullets)
    assert not _is_boilerplate(real_bullets)
