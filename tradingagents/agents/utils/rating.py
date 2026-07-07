"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re
from typing import Tuple


# Canonical, ordered 5-tier scale (most bullish to most bearish).
# English canonical + Chinese aliases (so the same parser handles both the
# English markdown path and the Chinese output path). Keep positions aligned
# so callers that need an index still get a consistent ordinal.
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)
# Chinese aliases mapped 1:1 to the English canonical (same ordinal).
_RATINGS_5_TIER_CN: Tuple[str, ...] = (
    "买入", "增持", "持有", "减持", "卖出",
)
# Lookup set used by parse_rating. Both languages live here so the parser
# does not have to know which language the agent wrote in.
_RATING_SET_EN = {r.lower() for r in RATINGS_5_TIER}
_RATING_SET_CN = set(_RATINGS_5_TIER_CN)
_RATING_SET = _RATING_SET_EN | _RATING_SET_CN

# Matches "Rating: X" / "rating - X" / "Rating: **X**" / "评级：卖出" —
# tolerates markdown bold wrappers, either a colon or hyphen separator,
# and either ASCII or CJK label/value. \w alone does not match CJK, so we
# union an explicit CJK range (U+4E00..U+9FA5, CJK Unified Ideographs base
# block 一..龥) via a non-capturing alternation. Without this fix, Chinese
# labels like "评级：卖出" silently fell through to the default ("Hold"),
# mislabelling the actual decision.
_RATING_LABEL_RE = re.compile(
    r"(?:rating|评级)[s*]*[:：\-][\s*]*(?:\*+)?([A-Za-z]+|[一-龥]+)(?:\*+)?",
    re.IGNORECASE,
)


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Two-pass strategy:
    1. Look for an explicit "Rating: X" label (tolerant of markdown bold).
    2. Fall back to the first 5-tier rating word found anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    for line in text.splitlines():
        if "评级" in line:
            for part in line.split("："):
                part = part.strip("* ")
                if part.lower() in _RATING_SET:
                    return part.capitalize()
        for word in line.lower().split():
            clean = word.strip("*:.,：")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default
