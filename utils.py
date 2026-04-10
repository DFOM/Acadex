# utils.py
from models import db, Setting, ScrapedDataset, Course, CatalogStatus, DatasetRegistry, Faculty, CourseMetadataField
import re
import json
from typing import Any
from sqlalchemy import func
from difflib import get_close_matches
from config.data_mapping import DATA_MAPPING


COURSE_CANONICAL_FIELDS = (
    'course_number',
    'title',
    'credits',
    'faculty',
    'day_period',
    'semester',
    'year',
    'year_level',
    'prerequisite',
    'prerequisites',
    'subject_area',
)

COURSE_RESERVED_INPUT_FIELDS = {
    'id',
    'ref',
    'source',
    'dataset_name',
    'faculty_ids',
    'faculty_names',
    'metadata',
    'dynamic_metadata',
    'additional_attributes',
    'additional_attributes_json',
}

COURSE_RESERVED_INPUT_FIELDS_NORMALIZED = {
    re.sub(r'\s+', ' ', str(v or '').strip().lower().replace('_', ' ')).strip()
    for v in COURSE_RESERVED_INPUT_FIELDS
}


def safe_json_loads(value, default):
    try:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        s = str(value).strip()
        if not s:
            return default
        return json.loads(s)
    except Exception:
        return default


def safe_json_dumps(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return '{}'


def normalize_column_name(value: Any) -> str:
    text = str(value or '').strip().lower()
    text = text.replace('_', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def get_data_mapping() -> dict[str, list[str]]:
    base = {
        key: [str(v).strip() for v in (values or []) if str(v).strip()]
        for key, values in (DATA_MAPPING or {}).items()
    }
    try:
        stored = Setting.query.get('data_mapping_json')
        overrides = safe_json_loads(getattr(stored, 'value', None), {})
        if not isinstance(overrides, dict):
            return base

        merged = dict(base)
        for key, values in overrides.items():
            field_key = str(key or '').strip()
            if not field_key:
                continue
            if isinstance(values, list):
                clean_values = [str(v).strip() for v in values if str(v).strip()]
            else:
                clean_values = [str(values).strip()] if str(values or '').strip() else []
            if clean_values:
                merged[field_key] = clean_values
        return merged
    except Exception:
        return base


def get_data_mapping_normalized() -> dict[str, list[str]]:
    mapping = get_data_mapping()
    normalized: dict[str, list[str]] = {}
    for key, values in mapping.items():
        normalized[key] = []
        seen: set[str] = set()
        for name in values:
            norm = normalize_column_name(name)
            if norm and norm not in seen:
                seen.add(norm)
                normalized[key].append(norm)
    return normalized


def mapped_value_from_row(row: dict[str, Any], field: str, mapping: dict[str, list[str]] | None = None) -> str:
    normalized_mapping = mapping or get_data_mapping_normalized()
    normalized_row = {
        normalize_column_name(k): str(v or '').strip()
        for k, v in (row or {}).items()
    }

    aliases = normalized_mapping.get(field, [])
    for alias in aliases:
        value = normalized_row.get(alias, '')
        if value:
            return value

    available_headers = [h for h in normalized_row.keys() if h]
    for alias in aliases:
        if not alias:
            continue
        matches = get_close_matches(alias, available_headers, n=1, cutoff=0.88)
        if not matches:
            continue
        value = normalized_row.get(matches[0], '')
        if value:
            return value
    return ''


def mapped_course_payload_from_row(row: dict[str, Any], mapping: dict[str, list[str]] | None = None) -> dict[str, str]:
    normalized_mapping = mapping or get_data_mapping_normalized()
    prerequisites = mapped_value_from_row(row, 'prerequisites', normalized_mapping)
    return {
        'course_number': mapped_value_from_row(row, 'course_number', normalized_mapping),
        'title': mapped_value_from_row(row, 'title', normalized_mapping),
        'credits': mapped_value_from_row(row, 'credits', normalized_mapping),
        'faculty': mapped_value_from_row(row, 'faculty', normalized_mapping),
        'day_period': mapped_value_from_row(row, 'day_period', normalized_mapping),
        'semester': mapped_value_from_row(row, 'semester', normalized_mapping),
        'year': mapped_value_from_row(row, 'year', normalized_mapping),
        'year_level': mapped_value_from_row(row, 'year_level', normalized_mapping),
        'prerequisite': prerequisites,
        'prerequisites': prerequisites,
        'subject_area': mapped_value_from_row(row, 'subject_area', normalized_mapping),
    }


def detected_mapping_fields(headers: list[str], mapping: dict[str, list[str]] | None = None) -> dict[str, str]:
    normalized_mapping = mapping or get_data_mapping_normalized()
    normalized_headers = {normalize_column_name(h): str(h or '').strip() for h in (headers or [])}
    result: dict[str, str] = {}
    for field, aliases in normalized_mapping.items():
        for alias in aliases:
            if alias in normalized_headers:
                result[field] = normalized_headers[alias]
                break
    return result


def normalize_metadata_field_key(value: Any) -> str:
    text = normalize_column_name(value)
    if not text:
        return ''
    text = re.sub(r'[^a-z0-9]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    if not text:
        return ''
    if text[0].isdigit():
        text = f'field_{text}'
    return text[:120]


def _coerce_metadata_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value

    text = str(value).strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in {'true', 'false'}:
        return lowered == 'true'

    if re.fullmatch(r'-?\d+', text):
        try:
            return int(text)
        except Exception:
            pass

    if re.fullmatch(r'-?\d+\.\d+', text):
        try:
            return float(text)
        except Exception:
            pass

    if text.startswith('{') or text.startswith('['):
        try:
            return json.loads(text)
        except Exception:
            return text

    return text


def normalize_course_metadata_for_storage(metadata: dict[str, Any] | None) -> dict[str, Any]:
    source = metadata or {}
    normalized: dict[str, Any] = {}

    for raw_key, raw_value in source.items():
        field_key = normalize_metadata_field_key(raw_key)
        if not field_key or field_key in COURSE_CANONICAL_FIELDS:
            continue
        coerced = _coerce_metadata_value(raw_value)
        if coerced is None:
            continue
        normalized[field_key] = coerced
    return normalized


def merge_course_metadata(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = normalize_course_metadata_for_storage(existing)
    merged.update(normalize_course_metadata_for_storage(incoming))
    return merged


def _canonical_aliases(mapping: dict[str, list[str]] | None) -> set[str]:
    aliases = {normalize_column_name(v) for v in COURSE_CANONICAL_FIELDS}
    normalized_mapping = mapping or get_data_mapping_normalized()
    for field in COURSE_CANONICAL_FIELDS:
        for alias in normalized_mapping.get(field, []):
            aliases.add(normalize_column_name(alias))
    return {a for a in aliases if a}


def _extract_nested_metadata(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    metadata: dict[str, Any] = {}
    schema: dict[str, dict[str, str]] = {}
    for key in ('metadata', 'dynamic_metadata', 'additional_attributes', 'additional_attributes_json'):
        candidate = payload.get(key)
        parsed = safe_json_loads(candidate, {}) if isinstance(candidate, str) else candidate
        if not isinstance(parsed, dict):
            continue
        for nested_key, nested_value in parsed.items():
            field_key = normalize_metadata_field_key(nested_key)
            if not field_key or field_key in COURSE_CANONICAL_FIELDS:
                continue
            coerced = _coerce_metadata_value(nested_value)
            if coerced is None:
                continue
            metadata[field_key] = coerced
            schema[field_key] = {
                'field_key': field_key,
                'label': str(nested_key or '').strip() or field_key,
            }
    return metadata, schema


def dynamic_course_payload_from_row(row: dict[str, Any], mapping: dict[str, list[str]] | None = None) -> dict[str, Any]:
    raw = dict(row or {})
    normalized_mapping = mapping or get_data_mapping_normalized()

    canonical = mapped_course_payload_from_row(raw, normalized_mapping)
    for key in COURSE_CANONICAL_FIELDS:
        if canonical.get(key):
            continue
        if key in raw:
            canonical[key] = str(raw.get(key) or '').strip()

    if not canonical.get('prerequisite') and canonical.get('prerequisites'):
        canonical['prerequisite'] = canonical.get('prerequisites')

    excluded = _canonical_aliases(normalized_mapping)
    metadata: dict[str, Any] = {}
    schema: dict[str, dict[str, str]] = {}

    nested_metadata, nested_schema = _extract_nested_metadata(raw)
    metadata.update(nested_metadata)
    schema.update(nested_schema)

    for raw_key, raw_value in raw.items():
        normalized_key = normalize_column_name(raw_key)
        if not normalized_key:
            continue
        if normalized_key in excluded or normalized_key in COURSE_RESERVED_INPUT_FIELDS_NORMALIZED:
            continue

        field_key = normalize_metadata_field_key(raw_key)
        if not field_key or field_key in COURSE_CANONICAL_FIELDS:
            continue

        coerced = _coerce_metadata_value(raw_value)
        if coerced is None:
            continue

        metadata[field_key] = coerced
        if field_key not in schema:
            schema[field_key] = {
                'field_key': field_key,
                'label': str(raw_key or '').strip() or field_key,
            }

    return {
        'canonical': canonical,
        'metadata': normalize_course_metadata_for_storage(metadata),
        'schema': schema,
    }


def upsert_course_metadata_fields(schema_fields: dict[str, dict[str, str]], *, default_visibility: str = 'student') -> None:
    if not isinstance(schema_fields, dict) or not schema_fields:
        return

    visibility_default = str(default_visibility or 'student').strip().lower()
    if visibility_default not in {'student', 'admin_only'}:
        visibility_default = 'student'

    normalized: dict[str, dict[str, str]] = {}
    for key, value in schema_fields.items():
        field_key = normalize_metadata_field_key(key)
        if not field_key:
            continue
        label = str((value or {}).get('label') or field_key).strip() or field_key
        normalized[field_key] = {'label': label}

    if not normalized:
        return

    key_list = list(normalized.keys())
    existing = CourseMetadataField.query.filter(func.lower(CourseMetadataField.field_key).in_([k.lower() for k in key_list])).all()
    existing_map = {str(row.field_key or '').strip().lower(): row for row in existing}

    for field_key, value in normalized.items():
        row = existing_map.get(field_key.lower())
        if not row:
            db.session.add(
                CourseMetadataField(
                    field_key=field_key,
                    label=value['label'],
                    visibility=visibility_default,
                    is_active=True,
                )
            )
            continue

        if not row.label and value['label']:
            row.label = value['label']
        if str(row.visibility or '').strip().lower() not in {'student', 'admin_only'}:
            row.visibility = visibility_default


def merge_dataset_registry_metadata_schema(dataset_row: DatasetRegistry | None, schema_fields: dict[str, dict[str, str]]) -> None:
    if dataset_row is None or not isinstance(schema_fields, dict) or not schema_fields:
        return

    existing_schema = safe_json_loads(getattr(dataset_row, 'metadata_schema_json', None), default=[])
    by_key: dict[str, dict[str, Any]] = {}

    if isinstance(existing_schema, list):
        for item in existing_schema:
            if not isinstance(item, dict):
                continue
            field_key = normalize_metadata_field_key(item.get('field_key') or item.get('key'))
            if not field_key:
                continue
            by_key[field_key] = {
                'field_key': field_key,
                'label': str(item.get('label') or field_key).strip() or field_key,
                'visibility': str(item.get('visibility') or 'student').strip().lower() or 'student',
            }

    visibility_map = {
        str(row.field_key or '').strip().lower(): str(row.visibility or 'student').strip().lower()
        for row in CourseMetadataField.query.all()
        if str(row.field_key or '').strip()
    }

    for raw_key, raw_value in schema_fields.items():
        field_key = normalize_metadata_field_key(raw_key)
        if not field_key:
            continue
        label = str((raw_value or {}).get('label') or field_key).strip() or field_key
        by_key[field_key] = {
            'field_key': field_key,
            'label': label,
            'visibility': visibility_map.get(field_key.lower(), by_key.get(field_key, {}).get('visibility', 'student')),
        }

    dataset_row.metadata_schema_json = sorted(by_key.values(), key=lambda item: str(item.get('field_key') or ''))


def course_metadata_visibility_map(*, include_inactive: bool = False) -> dict[str, dict[str, Any]]:
    query = CourseMetadataField.query
    if not include_inactive:
        query = query.filter(CourseMetadataField.is_active.is_(True))

    rows = query.order_by(CourseMetadataField.field_key.asc()).all()
    return {
        str(row.field_key or '').strip().lower(): {
            'field_key': str(row.field_key or '').strip(),
            'label': str(row.label or row.field_key or '').strip(),
            'visibility': str(row.visibility or 'student').strip().lower() or 'student',
            'is_active': bool(row.is_active),
        }
        for row in rows
        if str(row.field_key or '').strip()
    }


def project_course_metadata_for_role(metadata: dict[str, Any] | None, *, role: str = 'student') -> dict[str, Any]:
    payload = normalize_course_metadata_for_storage(metadata)
    if not payload:
        return {}

    role_name = str(role or 'student').strip().lower()
    if role_name in {'admin', 'moderator'}:
        return payload

    visibility = course_metadata_visibility_map(include_inactive=False)
    projected: dict[str, Any] = {}
    for key, value in payload.items():
        rule = visibility.get(key.lower())
        if rule and str(rule.get('visibility') or 'student').strip().lower() == 'admin_only':
            continue
        projected[key] = value
    return projected


def normalize_dataset_semester(value: Any) -> str:
    text = str(value or '').strip().lower()
    if text in {'spring', 'spr', 'sp'}:
        return 'spring'
    if text in {'fall', 'autumn', 'fa'}:
        return 'fall'
    if text in {'full', 'fullyear', 'full_year', 'full-year', 'full year', 'year', 'annual', 'all'}:
        return 'full_year'
    return text


def infer_dataset_type(semester: Any) -> str:
    sem = normalize_dataset_semester(semester)
    if sem == 'full_year':
        return 'full_year'
    if sem in {'spring', 'fall'}:
        return sem
    return 'term'


def _registry_row_has_fields(row: DatasetRegistry | None, required_fields: list[str] | None) -> bool:
    if row is None or not required_fields:
        return True
    schema = safe_json_loads(getattr(row, 'metadata_schema_json', None), default=[])
    if not isinstance(schema, list):
        return False

    available = set()
    for item in schema:
        if not isinstance(item, dict):
            continue
        key = normalize_metadata_field_key(item.get('field_key') or item.get('key'))
        if key:
            available.add(key)

    for field in required_fields:
        norm = normalize_metadata_field_key(field)
        if norm and norm not in available:
            return False
    return True


def resolve_dataset(
    year: Any,
    semester: Any,
    *,
    source: str | None = None,
    required_metadata_fields: list[str] | None = None,
) -> DatasetRegistry | None:
    year_text = str(year or '').strip().lower()
    sem_text = normalize_dataset_semester(semester)
    source_text = str(source or '').strip().lower()

    try:
        query = DatasetRegistry.query
        if source_text:
            query = query.filter(func.lower(func.coalesce(DatasetRegistry.source, '')) == source_text)

        if year_text and sem_text:
            exact = (
                query.filter(
                    func.lower(func.coalesce(DatasetRegistry.year, '')) == year_text,
                    func.lower(func.coalesce(DatasetRegistry.semester, '')) == sem_text,
                )
                .order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc())
                .first()
            )
            if exact and _registry_row_has_fields(exact, required_metadata_fields):
                return exact

        if year_text:
            full_year = (
                query.filter(
                    func.lower(func.coalesce(DatasetRegistry.year, '')) == year_text,
                    func.lower(func.coalesce(DatasetRegistry.dataset_type, 'term')) == 'full_year',
                )
                .order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc())
                .first()
            )
            if full_year and _registry_row_has_fields(full_year, required_metadata_fields):
                return full_year

        rows = query.order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).all()
        for row in rows:
            if _registry_row_has_fields(row, required_metadata_fields):
                return row
        return None
    except Exception:
        db.session.rollback()
        return None


def resolve_dataset_name(year: Any, semester: Any, *, source: str | None = None) -> str:
    row = resolve_dataset(year, semester, source=source)
    return str(getattr(row, 'name', '') or '').strip()


def extract_dataset_context(*values: Any) -> tuple[str | None, str | None, str]:
    merged = ' '.join(str(v or '') for v in values if str(v or '').strip())
    merged_lower = merged.lower()

    year_value = None
    year_match = re.search(r'\b(20\d{2})\b', merged)
    if year_match:
        year_value = year_match.group(1)

    semester_value = ''
    if re.search(r'\b(spring|spr|sp)\b', merged_lower):
        semester_value = 'spring'
    elif re.search(r'\b(fall|autumn|fa)\b', merged_lower):
        semester_value = 'fall'
    elif re.search(r'\b(full\s*year|fullyear|full[_\- ]year|annual|all\s*courses|all)\b', merged_lower):
        semester_value = 'full_year'

    dataset_type = infer_dataset_type(semester_value)
    return year_value, (semester_value or None), dataset_type


def _strip_brackets_quotes(text: str) -> str:
    """Remove surrounding [], '', \"\" from stringified Python lists."""
    text = text.strip()
    # Handle Python list repr: "['Name']" or "['A', 'B']"
    if text.startswith('[') and text.endswith(']'):
        inner = text[1:-1].strip()
        # Split on comma but respect quoted strings
        parts = [p.strip().strip("'\"").strip() for p in re.split(r"'\s*,\s*'|\"\\s*,\\s*\"", inner)]
        text = ', '.join(p for p in parts if p)
    text = text.strip("'\"").strip()
    return text


def _titlecase_name(name: str) -> str:
    """Convert 'ASHMORE Darren' or 'ashmore darren' to 'Ashmore Darren'.

    Applies per-word: words that are fully uppercase or fully lowercase get
    capitalized. Words already in mixed case (e.g. 'McBride') are preserved.
    Leaves non-Latin names (e.g. Japanese) untouched.
    """
    if not name:
        return name
    # If the name contains non-Latin characters, return as-is
    if re.search(r'[^\x00-\x7F]', name):
        return name
    small_words = {'de', 'del', 'da', 'di', 'van', 'von', 'la', 'le', 'el', 'al'}
    words = name.split()
    result = []
    for i, w in enumerate(words):
        alpha = re.sub(r'[^a-zA-Z]', '', w)
        if not alpha:
            result.append(w)
        elif alpha.isupper() or alpha.islower():
            # All-upper or all-lower word → capitalize
            if i > 0 and w.lower() in small_words:
                result.append(w.lower())
            else:
                result.append(w.capitalize())
        else:
            # Mixed case (e.g. 'McBride') → preserve
            result.append(w)
    return ' '.join(result)


def clean_display_name(name: str) -> str:
    """Quick clean for display: strip brackets/quotes and title-case."""
    if not name:
        return name
    return _titlecase_name(_strip_brackets_quotes(name).strip())


def strip_section_suffix(title: str) -> str:
    """Remove trailing section identifiers for dedup comparison.

    'Japanese Language 2 Sec 1'  → 'Japanese Language 2'
    'Japanese Language 2 Sec 2'  → 'Japanese Language 2'
    'Intro to Law Section A'     → 'Intro to Law'
    """
    if not title:
        return title
    return re.sub(
        r'\s+(?:Sec(?:tion)?\.?\s*[A-Za-z0-9]+)$',
        '', title, flags=re.IGNORECASE
    ).strip()


def current_dataset_names() -> set[str]:
    """Return lowercased dataset names considered 'current'.

    Includes the most recent semester's datasets AND the most recent
    opposite-semester datasets, so Spring-only and Fall-only courses
    are both treated as active.
    """
    all_ds = DatasetRegistry.query.order_by(
        DatasetRegistry.created_at.desc()
    ).all()
    if not all_ds:
        return set()

    names: set[str] = set()
    seen_semesters: set[str] = set()   # track which semesters we've covered

    for ds in all_ds:
        sem = normalize_dataset_semester(ds.semester).lower() or 'unknown'
        if sem in seen_semesters:
            continue
        # First time seeing this semester → it's the most recent for that semester
        seen_semesters.add(sem)
        # Include all datasets sharing the same year+semester
        year = (ds.year or '').strip()
        if year:
            siblings = [
                d for d in all_ds
                if (d.year or '').strip() == year
                and normalize_dataset_semester(d.semester).lower() == sem
            ]
            for s in siblings:
                n = (s.name or '').strip().lower()
                if n:
                    names.add(n)
        else:
            n = (ds.name or '').strip().lower()
            if n:
                names.add(n)
        # Stop once we have both Fall and Spring (or two distinct semesters)
        if len(seen_semesters) >= 2:
            break

    return names


def normalize_faculty_name(name: str) -> str:
    cleaned = _strip_brackets_quotes(str(name or ''))
    cleaned = re.sub(r'\s+', ' ', cleaned.strip())
    cleaned = re.sub(r'^(dr|prof|professor|mr|ms|mrs)\.?\s+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+,', ',', cleaned)
    cleaned = cleaned.strip(' -;|/')
    cleaned = _titlecase_name(cleaned)
    return cleaned


def extract_faculty_names(raw_value: Any) -> list[str]:
    text = str(raw_value or '').strip()
    if not text:
        return []

    # Unwrap Python list repr before any splitting
    text = _strip_brackets_quotes(text)
    if not text:
        return []

    placeholder = {'tba', 'tbd', 'staff', 'n/a', 'na', 'none', 'unknown'}
    if text.lower() in placeholder:
        return []

    parts = re.split(r'\s*(?:,|;|\||/|&|\band\b)\s*', text, flags=re.IGNORECASE)
    names: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = normalize_faculty_name(part)
        if not name:
            continue
        key = name.lower()
        if key in placeholder or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def sync_course_faculty(course: Course, faculty_value: Any, *, fallback_text: Any = None) -> list[str]:
    names = extract_faculty_names(faculty_value)
    if not names:
        names = extract_faculty_names(fallback_text)

    if not names:
        course.faculty = []
        course.instructor = None
        return []

    lowered = [n.lower() for n in names]
    existing_rows = Faculty.query.filter(func.lower(Faculty.name).in_(lowered)).all()
    existing_map = {(r.name or '').strip().lower(): r for r in existing_rows}

    linked_rows: list[Faculty] = []
    for name in names:
        key = name.lower()
        row = existing_map.get(key)
        if not row:
            row = Faculty(name=name)
            db.session.add(row)
            existing_map[key] = row
        linked_rows.append(row)

    course.faculty = linked_rows
    course.instructor = '; '.join(names)
    return names


def ensure_default_landing_sections():
    """Ensure core landing page sections exist.

    Returns sections ordered by display_order.
    Safe to call on every request (does minimal work).
    """
    defaults = [
        {
            'name': 'hero',
            'enabled': True,
            'display_order': 10,
            'content': {
                'headline': 'Master Your Academic Journey',
                'subtext': (
                    'Build your perfect schedule, track your degree progress, and explore courses as a guest. '
                    'Ready for more? Create a free account to permanently save your plans, calculate GPA, and unlock analytics.'
                ),
                'primary_label': 'Start Planning',
                'secondary_label': 'Register for Premium',
            },
        },
        {
            'name': 'features',
            'enabled': True,
            'display_order': 20,
            'content': {
                'title': 'Why Acadex',
                'cards': [
                    {
                        'icon': 'calendar',
                        'title': 'Schedule Builder',
                        'description': 'Plan semesters quickly with a clean, modern grid editor.',
                    },
                    {
                        'icon': 'sparkles',
                        'title': 'Analytics-Ready',
                        'description': 'Understand your workload patterns with admin-grade insights.',
                    },
                    {
                        'icon': 'shield-check',
                        'title': 'Built for Students',
                        'description': 'Guest mode for quick exploration, accounts for permanence.',
                    },
                ],
            },
        },
        {'name': 'announcements', 'enabled': True, 'display_order': 30, 'content': None},
        {'name': 'info_grid', 'enabled': True, 'display_order': 40, 'content': None},
    ]

    try:
        # Local import avoids circulars during app init.
        from models import LandingPageSection

        existing = {s.name: s for s in LandingPageSection.query.all()}
        changed = False
        for item in defaults:
            if item['name'] in existing:
                continue
            section = LandingPageSection(
                name=item['name'],
                enabled=bool(item.get('enabled', True)),
                display_order=int(item.get('display_order', 0)),
                content_json=safe_json_dumps(item['content']) if item.get('content') is not None else None,
            )
            db.session.add(section)
            changed = True

        if changed:
            db.session.commit()

        return LandingPageSection.query.order_by(LandingPageSection.display_order.asc(), LandingPageSection.id.asc()).all()
    except Exception:
        # Pre-migration or DB not ready: return defaults as lightweight objects.
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                id=None,
                name=item['name'],
                enabled=item.get('enabled', True),
                display_order=item.get('display_order', 0),
                content_json=safe_json_dumps(item['content']) if item.get('content') is not None else None,
            )
            for item in defaults
        ]


def format_display_course_number(raw: str) -> str:
    """Create a user-friendly course identifier.

    Examples:
    - EFAE010-S1-Fa -> EFAE010 (Sec 1)
    - PSYC/DATA190-Sp -> PSYC/DATA 190
    """
    s = str(raw or '').strip()
    if not s:
        return ''

    # Remove version suffixes.
    s = re.sub(r'-V\d+\b', '', s, flags=re.I)

    # Extract section if present.
    section = None
    m = re.search(r'-S(\d+)\b', s, flags=re.I)
    if m:
        section = m.group(1)

    # Drop trailing term markers like -Sp / -Fa / -Spring / -Fall.
    s = re.sub(r'-(Sp|Fa|Spring|Fall)\b.*$', '', s, flags=re.I)

    # Drop section markers and anything after.
    s = re.sub(r'-S\d+\b.*$', '', s, flags=re.I)

    # Insert a space between letters and digits (DATA190 -> DATA 190).
    def _space_alpha_num(token: str) -> str:
        return re.sub(r'([A-Za-z])(?=\d)', r'\1 ', token)

    parts = s.split('/')
    parts = [_space_alpha_num(p.strip()) for p in parts if p.strip()]
    s = '/'.join(parts)

    if section:
        return f"{s} (Sec {section})"
    return s

def get_available_dataset_years() -> list[int]:
    years: set[int] = set()

    def _add_year(value) -> None:
        text = str(value or '').strip()
        if not text:
            return
        direct = re.fullmatch(r'\d{4}', text)
        if direct:
            year_val = int(text)
            if 2000 <= year_val <= 2199:
                years.add(year_val)
            return
        for match in re.findall(r'\b(20\d{2})\b', text):
            try:
                year_val = int(match)
            except Exception:
                continue
            if 2000 <= year_val <= 2199:
                years.add(year_val)

    try:
        rows = ScrapedDataset.query.filter_by(status='completed').all()
        for row in rows:
            _add_year(getattr(row, 'year', None))
            _add_year(getattr(row, 'name', None))
    except Exception:
        pass

    try:
        local_rows = Course.query.all()
        for row in local_rows:
            _add_year(getattr(row, 'year', None))
            _add_year(getattr(row, 'dataset_name', None))
    except Exception:
        pass

    try:
        status_rows = CatalogStatus.query.all()
        for row in status_rows:
            _add_year(getattr(row, 'term', None))
            _add_year(getattr(row, 'dataset', None))
    except Exception:
        pass

    try:
        reg_rows = DatasetRegistry.query.all()
        for row in reg_rows:
            _add_year(getattr(row, 'name', None))
            _add_year(getattr(row, 'table_name', None))
    except Exception:
        pass

    return sorted(years)

def is_valid_table_name(name: str) -> bool:
    """Basic validation for SQL table identifiers used by the app.

    Only allow ASCII letters, digits and underscores to avoid SQL injection
    risks when identifiers are interpolated into SQL statements.
    """
    if not name or not isinstance(name, str):
        return False
    return bool(re.match(r'^[A-Za-z0-9_]+$', name))

def get_app_setting(key, default=""):
    """Fetches a setting from the database using SQLAlchemy ORM."""
    try:
        setting = Setting.query.get(key)
        return setting.value if setting else default
    except Exception:
        return default

def update_app_setting(key, value):
    """Updates or creates a setting using SQLAlchemy ORM."""
    setting = Setting.query.get(key)
    if not setting:
        setting = Setting(key=key, value=value)
        db.session.add(setting)
    else:
        setting.value = value
    db.session.commit()

def calculate_gpa(courses):
    """Calculates GPA based on the standard 4.0 scale."""
    if not courses:
        return 0.0

    grade_points = {'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0}
    total_points = 0.0
    total_credits = 0.0

    for course in courses:
        grade = (course.get('grade') or '').strip().upper()
        try:
            credits = float(course.get('Credits') or 0)
        except (ValueError, TypeError):
            credits = 0.0

        if grade in grade_points and credits > 0:
            total_points += grade_points[grade] * credits
            total_credits += credits

    return total_points / total_credits if total_credits > 0 else 0.0