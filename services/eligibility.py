from __future__ import annotations

import re
from typing import Iterable, Optional, Set, Tuple

from services.prereqs import PrereqSyntaxError, evaluate, parse


_NONE_SENTINELS = {"", "none", "n/a", "na"}


def normalize_course_code_for_prereqs(code: str) -> str:
    """Normalize a course code for prerequisite checks.

    - Strips schedule tags like -S1, -Sp/-Fa, -V2 from stored course numbers
    - Removes non-alphanumeric characters (e.g., MATH-101 -> MATH101)
    - Uppercases
    """

    if code is None:
        return ""

    s = str(code).strip()
    # Strip the app's section/semester/version suffixes if present.
    s = re.sub(r"-(S\d+|SP|FA|V\d+).*", "", s, flags=re.IGNORECASE)
    # Remove separators so prereqs like "MATH-101" match "MATH101".
    s = re.sub(r"[^A-Za-z0-9]", "", s)
    return s.upper()


def clean_prereq_string(prereq: Optional[str]) -> str:
    if prereq is None:
        return ""
    s = str(prereq).strip()
    return "" if s.lower() in _NONE_SENTINELS else s


def prereqs_met(
    prereq: Optional[str],
    completed_passing_courses: Iterable[str],
) -> Tuple[bool, Optional[str]]:
    """Return (met, friendly_error).

    If the prereq string is invalid syntax, returns (False, <message>).
    """

    prereq_str = clean_prereq_string(prereq)
    if prereq_str == "":
        return True, None

    try:
        expr = parse(prereq_str)
    except PrereqSyntaxError:
        return False, "Invalid prerequisite format"

    completed_set: Set[str] = {
        normalize_course_code_for_prereqs(c) for c in completed_passing_courses
    }
    completed_set.discard("")

    met = evaluate(expr, completed_set, normalizer=normalize_course_code_for_prereqs)
    return (met, None)
