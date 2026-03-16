from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from services.ai.common import filter_courses_by_prereqs
from services.conflicts import has_conflict, parse_day_period_blocks


def generate_schedule_courses(
    catalog_courses: Sequence[Dict],
    completed_passing_courses: Iterable[str],
    *,
    target_count: int = 5,
    existing_day_and_period: Iterable[str] = (),
) -> Tuple[List[Dict], List[str]]:
    """Generate a prereq-eligible set of courses.

    Conflict detection will be layered in via a separate engine; this function
    already guarantees prerequisites are met.
    """

    eligible, errors = filter_courses_by_prereqs(catalog_courses, completed_passing_courses)
    target = max(int(target_count), 0)

    occupied = set()
    for dp in existing_day_and_period:
        occupied |= parse_day_period_blocks(dp)

    picked: List[Dict] = []
    for c in eligible:
        if len(picked) >= target:
            break

        dp = c.get('Day_and_Period')
        blocks = parse_day_period_blocks(dp)
        if has_conflict(occupied, blocks):
            continue
        occupied |= blocks
        picked.append(c)

    return picked, errors
