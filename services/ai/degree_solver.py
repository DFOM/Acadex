from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from services.ai.common import filter_courses_by_prereqs


def eligible_courses_for_area(
    catalog_courses: Sequence[Dict],
    completed_passing_courses: Iterable[str],
    *,
    area_of_study: str,
) -> Tuple[List[Dict], List[str]]:
    """Filter courses for a given area that the student is eligible to take."""

    area = (area_of_study or "").strip()
    in_area = [c for c in catalog_courses if (c.get("Area_of_Study") or "").strip() == area]
    return filter_courses_by_prereqs(in_area, completed_passing_courses)
