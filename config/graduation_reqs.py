"""
Graduation requirements for iCLA, derived from official requirement documents.

Two cohorts are supported:
  - 2024AY or earlier  (entry_year <= 2024)
  - 2025AY or later    (entry_year >= 2025)

Each category dict contains:
  label          – display name
  credits        – required credits (0 means course-count-based)
  courses        – list of specific course titles; empty when any course in the area counts
  pick           – how many courses to pick when courses list is provided (0 = all)
  note           – human-readable explanation
  area_match     – if set, match courses whose subject_area starts with this string
"""

# ── Pathway offerings by area ──────────────────────────────────────────────
PATHWAY_COURSES = {
    'Global Business and Economics': [
        'Microeconomics',
        'Introduction to Global Business Management',
    ],
    'Political Science': [
        'Introduction to Political Science',
    ],
    'Psychology': [
        'Introduction to Psychology',
        'Introduction to Psychobiology',
    ],
    'Data Science': [
        'Introduction to Python Programming',
    ],
    'Interdisciplinary Arts': [
        'Introduction to Language Concepts',
        'Literature Appreciation',
        'Art Appreciation',
        'Introduction to Filmmaking',
        'Fundamentals of Sound and Music',
    ],
    'Japan Studies': [
        'Introduction to Japanese History',
        'The Anthropology of Japan',
    ],
}

# Flatten for quick lookup: title (lowered) → area
PATHWAY_TITLE_TO_AREA = {}
for _area, _titles in PATHWAY_COURSES.items():
    for _t in _titles:
        PATHWAY_TITLE_TO_AREA[_t.lower()] = _area

# ── Quantitative Reasoning ─────────────────────────────────────────────────
QR_COURSES = [
    'Math for Liberal Arts',
    'College Algebra',
    'Earth Science',
    'Statistics',
]

# ── Major-specific designated courses (2025AY — also used for 2024AY Psych) ──
MAJOR_DESIGNATED = {
    'Psychology': {
        'designated': [
            'Introduction to Psychology',
            'Introduction to Psychobiology',
            'Research Design',
            'Social Psychology',
            'Cognitive Psychology',
            'Developmental Psychology',
            'Individual Differences',
        ],
        'designated_credits': 21,
        'other_credits_2024_opt1': 9,
        'other_credits_2024_opt2': 12,
        'other_credits_2025': 15,
        'other_note': "'Statistics' can be counted",
    },
    'Data Science for Political Economy': {
        'designated': [
            'Introduction to Python Programming',
            'Introduction to Political Science',
            'Microeconomics',
            'Research Design',
            'Statistics',
            'Data Visualization Techniques in Python',
            'Mathematics for Data Science',
            'Machine Learning',
            'Econometrics',
        ],
        'designated_credits': 27,
        'other_credits_2025': 9,
        'other_pick_from': [
            'Political Economy of Development',
            'International Trade: Theory and Policy',
        ],
        'other_note': 'At least 1 course from designated elective list',
    },
    'Data Science for Psychology': {
        'designated': [
            'Introduction to Python Programming',
            'Introduction to Psychology',
            'Research Design',
            'Statistics',
            'Social Psychology',
            'Data Visualization Techniques in Python',
            'Mathematics for Data Science',
            'Machine Learning',
        ],
        'designated_credits': 24,
        'other_credits_2025': 12,
        'other_pick_from': [
            'Cyberpsychology',
            'Consumer Psychology',
            'Economic Psychology',
            'Individual Differences',
        ],
        'other_note': 'At least 2 courses from designated elective list',
    },
    'Data Science for Finance': {
        'designated': [
            'Introduction to Python Programming',
            'Accounting',
            'Financial Management',
            'Research Design',
            'Statistics',
            'Data Visualization Techniques in Python',
            'Mathematics for Data Science',
            'Machine Learning',
            'Econometrics',
        ],
        'designated_credits': 27,
        'other_credits_2025': 9,
        'other_pick_from': [
            'Investment',
            'Risk Management',
            'Portfolio Management',
        ],
        'other_note': 'At least 1 course from designated elective list',
    },
}

# ── Minor requirements ─────────────────────────────────────────────────────
# Structure derived from official PDF table layout (positional parsing).
# Each minor has:
#   total_courses        – total courses required
#   level_distribution   – {level: count} courses needed per level
#   grade_b_by_group     – [{levels: [int...], needed: int}] per level-group
#   eligible_course_numbers – ordered list from the PDF
MINOR_REQUIREMENTS = {
    'Psychology': {
        'total_courses': 6,
        'level_distribution': {
            100: 2,   # 2 courses from 100-level (Pathway)
            200: 2,   # 2 courses from 200-level
            300: 2,   # 2 courses from 300-level
        },
        'grade_b_by_group': [
            {'levels': [100], 'needed': 1, 'label': '100-level'},
            {'levels': [200, 300], 'needed': 3, 'label': '200/300-level'},
        ],
        'eligible_course_numbers': [
            'PSYC100', 'PSYC150', 'PSYC201', 'PSYC210', 'PSYC220',
            'PSYC230', 'PSYC240', 'PSYC250', 'PSYC260', 'PSYC290',
            'PSYC/DATA311', 'PSYC330', 'PSYC340', 'PSYC/ECON349',
            'PSYC/ECON/DATA352',
        ],
    },
}


def get_general_requirements(entry_year=2025):
    """Return a list of general graduation requirement category dicts."""
    reqs = []

    # 1. English Language
    reqs.append({
        'key': 'english',
        'label': 'English Language',
        'credits': 15,
        'courses': ['English for Academic Excellence: B'],
        'pick': 0,  # all listed courses required
        'area_match': 'English for Academic Excellence',
        'note': 'EAE B (15 credits). Exempted for international degree-seeking students.',
    })

    # 2. Japanese Language
    reqs.append({
        'key': 'japanese',
        'label': 'Japanese Language',
        'credits': 3,
        'courses': ['Professional Writing in Japanese', 'Japanese Language 2'],
        'pick': 1,
        'area_match': '',
        'note': 'One of: Professional Writing in Japanese (native speakers) or Japanese Language 2 (non-native speakers).',
    })

    # 3. Foundation
    if entry_year >= 2025:
        reqs.append({
            'key': 'foundation',
            'label': 'Foundation',
            'credits': 0,
            'courses': [
                'Foundation for College Success',
                'Composition 1', 'Composition 2', 'Academic Reading Across Disciplines',
                'Expository Research Writing',
            ],
            'pick': 0,
            'area_match': 'Foundation',
            'note': 'Foundation for College Success (1cr) + writing: either Composition 1 + 2 + Academic Reading (9cr) or Expository Research Writing (3cr).',
        })
    else:
        reqs.append({
            'key': 'foundation',
            'label': 'Foundation',
            'credits': 0,
            'courses': [
                'Foundation for College Success',
                'Composition 1', 'Composition 2', 'Academic Reading Across Disciplines',
                'Expository Research Writing',
            ],
            'pick': 0,
            'area_match': 'Foundation',
            'note': 'Foundation for College Success (1cr) + writing: either Composition 1 + 2 + Academic Reading (9cr) or Expository Research Writing (3cr).',
        })

    # 4. Pathway
    reqs.append({
        'key': 'pathway',
        'label': 'Pathway',
        'credits': 9,
        'courses': [],  # dynamic — from PATHWAY_COURSES
        'pick': 3,
        'area_match': '',
        'note': 'At least 3 Pathway courses, each from a different subject area (9 credits total).',
    })

    # 5. Quantitative Reasoning & Natural Sciences
    reqs.append({
        'key': 'quantitative',
        'label': 'Quantitative Reasoning & Natural Sciences',
        'credits': 3,
        'courses': QR_COURSES,
        'pick': 1,
        'area_match': '',
        'note': 'One course from: Math for Liberal Arts, College Algebra, Earth Science, or Statistics.',
    })

    # 6. Major (placeholder — details set dynamically)
    if entry_year >= 2025:
        major_credits = 36
    else:
        major_credits = 30  # default for Option 1; Option 2 is 33 lectures only
    reqs.append({
        'key': 'major',
        'label': 'Major Area',
        'credits': major_credits,
        'courses': [],
        'pick': 0,
        'area_match': '',
        'note': f'{major_credits} credits in major area courses.',
    })

    # 7. Total credits
    reqs.append({
        'key': 'total_credits',
        'label': 'Total Credits',
        'credits': 124,
        'courses': [],
        'pick': 0,
        'area_match': '',
        'note': '124 credits minimum to graduate.',
    })

    return reqs


def get_major_requirements(major_name, entry_year=2025):
    """Return major-specific requirement details or None."""
    if not major_name:
        return None

    # Check Data Science sub-majors
    major_lower = major_name.strip().lower()
    ds_match = None
    for ds_key in MAJOR_DESIGNATED:
        if ds_key.lower() == major_lower:
            ds_match = ds_key
            break
    # Also check if the major is one of the areas with designated courses
    if not ds_match:
        for ds_key in MAJOR_DESIGNATED:
            if ds_key.lower() in major_lower or major_lower in ds_key.lower():
                ds_match = ds_key
                break

    if entry_year >= 2025:
        total = 36
    else:
        total = 30  # Option 1 default

    result = {
        'total_credits': total,
        'designated': [],
        'designated_credits': 0,
        'other_credits': total,
        'other_note': '',
        'other_pick_from': [],
    }

    if ds_match and ds_match in MAJOR_DESIGNATED:
        info = MAJOR_DESIGNATED[ds_match]
        result['designated'] = info['designated']
        result['designated_credits'] = info['designated_credits']
        if entry_year >= 2025:
            result['other_credits'] = info.get('other_credits_2025', total - info['designated_credits'])
        else:
            result['other_credits'] = info.get('other_credits_2024_opt1', total - info['designated_credits'])
        result['other_note'] = info.get('other_note', '')
        result['other_pick_from'] = info.get('other_pick_from', [])

    return result


def get_minor_requirements(minor_name):
    """Return minor-specific requirement details or None."""
    if not minor_name:
        return None
    for key, val in MINOR_REQUIREMENTS.items():
        if key.lower() == minor_name.strip().lower():
            return val
    return None
