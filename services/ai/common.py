from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from services.eligibility import prereqs_met


def filter_courses_by_prereqs(
    courses: Sequence[Dict],
    completed_passing_courses: Iterable[str],
    *,
    prereq_field: str = "Prerequisite",
    major_name: str | None = None,
    minor_name: str | None = None,
) -> Tuple[List[Dict], List[str]]:
    """Return (eligible_courses, errors).

    - Courses with unmet prerequisites are excluded.
    - Courses whose prerequisite strings are invalid syntax are excluded and logged as errors.

    This ensures any generator/recommender/solver does not propose courses the
    student can't actually take yet.
    """

    eligible: List[Dict] = []
    errors: List[str] = []

    for c in courses:
        prereq = c.get(prereq_field)
        met, err = prereqs_met(
            prereq,
            completed_passing_courses,
            major_name=major_name,
            minor_name=minor_name,
        )
        if err:
            course_num = c.get("Course_Number") or c.get("course_number") or "(unknown)"
            errors.append(f"Invalid prerequisite format for {course_num}")
            continue
        if met:
            eligible.append(c)

    return eligible, errors
