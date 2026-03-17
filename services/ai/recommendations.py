from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from services.ai.common import filter_courses_by_prereqs


def recommend_courses(
    catalog_courses: Sequence[Dict],
    completed_passing_courses: Iterable[str],
    *,
    limit: int = 5,
    major_area: str | None = None,
    minor_area: str | None = None,
    missing_credits_by_area: Dict[str, float] | None = None,
    historical_area_counts: Dict[str, int] | None = None,
    remaining_credits_to_graduate: float | None = None,
    peer_popularity_by_course: Dict[str, float] | None = None,
    behavior_affinity_by_course: Dict[str, float] | None = None,
    ai_affinity_by_course: Dict[str, float] | None = None,
    faculty_success_by_name: Dict[str, float] | None = None,
    course_avg_grade_points: Dict[str, float] | None = None,
) -> Tuple[List[Dict], List[str]]:
    """Return prerequisite-eligible recommendations ranked by relevance.

    Ranking is dynamic and metadata-driven:
    - major/minor area alignment
    - missing credits by area (degree progress)
    - historical area affinity (past schedules)
    - peer popularity and behavior affinity
    - faculty/course grade outcomes (easier to succeed)
    """

    eligible, errors = filter_courses_by_prereqs(
        catalog_courses,
        completed_passing_courses,
        major_name=major_area,
        minor_name=minor_area,
    )
    major_norm = (major_area or '').strip().lower()
    minor_norm = (minor_area or '').strip().lower()
    missing = {str(k).strip().lower(): float(v or 0.0) for k, v in (missing_credits_by_area or {}).items()}
    history = {str(k).strip().lower(): int(v or 0) for k, v in (historical_area_counts or {}).items()}
    remaining_total = max(0.0, float(remaining_credits_to_graduate or 0.0))
    peer_popularity = {str(k).strip().upper(): float(v or 0.0) for k, v in (peer_popularity_by_course or {}).items()}
    behavior_affinity = {str(k).strip().upper(): float(v or 0.0) for k, v in (behavior_affinity_by_course or {}).items()}
    ai_affinity = {str(k).strip().upper(): float(v or 0.0) for k, v in (ai_affinity_by_course or {}).items()}
    faculty_success = {str(k).strip().lower(): float(v or 0.0) for k, v in (faculty_success_by_name or {}).items()}
    course_avg = {str(k).strip().upper(): float(v or 0.0) for k, v in (course_avg_grade_points or {}).items()}

    def _score(course: Dict) -> tuple[float, List[str]]:
        area = str(course.get('Area_of_Study') or '').strip().lower()
        course_num = str(course.get('Course_Number') or course.get('course_number') or '').strip().upper()
        instructor = str(course.get('Instructor') or '').strip().lower()
        score = 0.0
        reasons: List[str] = []

        if area and area == major_norm:
            score += 5.0
            reasons.append('Aligns with your declared major area.')
        if area and area == minor_norm:
            score += 3.0
            reasons.append('Aligns with your declared minor area.')

        missing_credits = max(0.0, float(missing.get(area, 0.0))) if area else 0.0
        if missing_credits > 0:
            score += min(4.0, missing_credits / 3.0)
            if missing_credits >= 3:
                reasons.append('Helps close your remaining graduation credit requirements.')

        history_count = max(0, int(history.get(area, 0))) if area else 0
        if history_count > 0:
            score += min(2.0, history_count * 0.2)

        if course_num:
            peer_pop = max(0.0, float(peer_popularity.get(course_num, 0.0)))
            if peer_pop > 0:
                score += min(2.5, peer_pop * 0.3)
                if peer_pop >= 3:
                    reasons.append('Popular among students with similar major/minor selections.')

            behavior_bias = max(0.0, float(behavior_affinity.get(course_num, 0.0)))
            if behavior_bias > 0:
                score += min(1.5, behavior_bias * 0.25)

            ai_bias = max(0.0, float(ai_affinity.get(course_num, 0.0)))
            if ai_bias > 0:
                score += min(1.2, ai_bias * 0.2)

            avg_course_points = course_avg.get(course_num)
            if avg_course_points is not None and avg_course_points > 0:
                score += (avg_course_points - 2.0) * 0.6
                if avg_course_points >= 3.0:
                    reasons.append('Historically associated with stronger student grade outcomes.')

        if instructor:
            inst_points = faculty_success.get(instructor)
            if inst_points is not None and inst_points > 0:
                score += (inst_points - 2.0) * 0.5
                if inst_points >= 3.0:
                    reasons.append('Taught by faculty with strong historical performance outcomes.')

        try:
            course_credits = max(0.0, float(course.get('Credits') or 0.0))
            score += min(1.0, course_credits * 0.1)

            if remaining_total > 0:
                # If far from graduation, favor higher-credit options.
                if remaining_total >= 18:
                    score += min(1.2, course_credits * 0.25)
                # If close to graduation, avoid overshooting with only high-credit picks.
                elif remaining_total <= 6:
                    score -= max(0.0, course_credits - remaining_total) * 0.15
        except Exception:
            pass

        return score, reasons

    scored: List[Tuple[float, Dict, List[str]]] = []
    for c in eligible:
        course_score, reasons = _score(c)
        scored.append((course_score, c, reasons))

    scored.sort(
        key=lambda row: (
            -row[0],
            str(row[1].get('Course_Title') or ''),
            str(row[1].get('Course_Number') or ''),
        )
    )

    selected: List[Dict] = []
    seen_course_numbers = set()
    limit = max(0, min(int(limit or 0), 5))

    for score, item, reasons in scored:
        course_number = str(item.get('Course_Number') or item.get('course_number') or '').strip().upper()
        if not course_number or course_number in seen_course_numbers:
            continue
        seen_course_numbers.add(course_number)

        row = dict(item)
        unique_reasons = []
        for r in reasons:
            if r and r not in unique_reasons:
                unique_reasons.append(r)
        if unique_reasons:
            row['suggestion_reason'] = unique_reasons[0]
        else:
            row['suggestion_reason'] = 'Recommended based on your planning context, schedule patterns, and historical student success signals.'
        row['suggestion_score'] = round(float(score), 3)
        selected.append(row)
        if len(selected) >= limit:
            break

    return selected, errors
