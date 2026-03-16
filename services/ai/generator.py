from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from services.ai.common import filter_courses_by_prereqs


def generate_schedule_courses(
    catalog_courses: Sequence[Dict],
    completed_passing_courses: Iterable[str],
    *,
    target_count: int = 5,
) -> Tuple[List[Dict], List[str]]:
    """Generate a prereq-eligible set of courses.

    Conflict detection will be layered in via a separate engine; this function
    already guarantees prerequisites are met.
    """

    eligible, errors = filter_courses_by_prereqs(catalog_courses, completed_passing_courses)
    target = max(int(target_count), 0)
    return eligible[:target], errors
