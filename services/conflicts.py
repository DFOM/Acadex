from __future__ import annotations

import re
from typing import Iterable, Optional, Set


def parse_day_period_blocks(day_and_period: Optional[str]) -> Set[str]:
    """Parse a Day_and_Period string into normalized time blocks.

    The app's datasets typically include values like:
      "Mon 1 Wed 1" or "Mon1 Wed1" or "Mon1,Wed1".

    We normalize to tokens like "MON1", "TUE3".

    "TBA" / empty means no blocks.
    """

    if not day_and_period:
        return set()

    s = str(day_and_period).strip()
    if s == "" or s.upper() == "TBA":
        return set()

    parts = re.split(r"[\s,]+", s)

    blocks: Set[str] = set()
    current_day: str = ""

    for p in parts:
        if not p:
            continue

        # Accept day words embedded in token.
        day_match = re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)", p, re.IGNORECASE)
        if day_match:
            current_day = day_match.group(1)[:3].upper()

        # Accept a period number embedded in token.
        num_match = re.search(r"(\d+)", p)
        if current_day and num_match:
            blocks.add(f"{current_day}{num_match.group(1)}")

    return blocks


def has_conflict(existing_blocks: Iterable[str], candidate_blocks: Iterable[str]) -> bool:
    existing = set(existing_blocks)
    candidate = set(candidate_blocks)
    if not existing or not candidate:
        return False
    return not existing.isdisjoint(candidate)
