from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from services.ai.common import filter_courses_by_prereqs


def recommend_courses(
    catalog_courses: Sequence[Dict],
    completed_passing_courses: Iterable[str],
    *,
    limit: int = 10,
) -> Tuple[List[Dict], List[str]]:
    """Return prerequisite-eligible course recommendations.

    This is intentionally minimal for now: it only enforces prerequisite
    eligibility and returns the first N eligible courses.
    """

    eligible, errors = filter_courses_by_prereqs(catalog_courses, completed_passing_courses)
    return eligible[: max(limit, 0)], errors
