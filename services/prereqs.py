from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


class PrereqSyntaxError(ValueError):
    pass


# --- AST Nodes ---


@dataclass(frozen=True)
class Course:
    code: str


@dataclass(frozen=True)
class And:
    items: Tuple["Expr", ...]


@dataclass(frozen=True)
class Or:
    items: Tuple["Expr", ...]


Expr = Union[Course, And, Or]


# --- Tokenization ---


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    pos: int


_TOKEN_LPAREN = "LPAREN"
_TOKEN_RPAREN = "RPAREN"
_TOKEN_AND = "AND"
_TOKEN_OR = "OR"
_TOKEN_COURSE = "COURSE"
_TOKEN_COMMA = "COMMA"
_TOKEN_EOF = "EOF"


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in {"_", "-"}


def tokenize(prereq: str) -> List[Token]:
    if prereq is None:
        prereq = ""
    s = str(prereq)
    tokens: List[Token] = []
    i = 0

    while i < len(s):
        ch = s[i]

        if ch.isspace():
            i += 1
            continue

        if ch == "(":
            tokens.append(Token(_TOKEN_LPAREN, ch, i))
            i += 1
            continue

        if ch == ")":
            tokens.append(Token(_TOKEN_RPAREN, ch, i))
            i += 1
            continue

        if ch == ",":
            tokens.append(Token(_TOKEN_COMMA, ch, i))
            i += 1
            continue

        if _is_word_char(ch):
            start = i
            while i < len(s) and _is_word_char(s[i]):
                i += 1
            raw = s[start:i]
            upper = raw.upper()

            # Operators are case-insensitive, but must be standalone words.
            if upper == "AND":
                tokens.append(Token(_TOKEN_AND, "AND", start))
            elif upper == "OR":
                tokens.append(Token(_TOKEN_OR, "OR", start))
            else:
                tokens.append(Token(_TOKEN_COURSE, upper, start))
            continue

        raise PrereqSyntaxError(f"Invalid character '{ch}' at position {i}")

    tokens.append(Token(_TOKEN_EOF, "", len(s)))
    return tokens


# --- Parsing (AND precedence over OR, parentheses for grouping) ---


class _Parser:
    def __init__(self, tokens: Sequence[Token]):
        self._tokens = tokens
        self._idx = 0

    def _peek(self) -> Token:
        return self._tokens[self._idx]

    def _advance(self) -> Token:
        tok = self._tokens[self._idx]
        self._idx += 1
        return tok

    def _expect(self, kind: str) -> Token:
        tok = self._peek()
        if tok.kind != kind:
            raise PrereqSyntaxError(
                f"Expected {kind} at position {tok.pos}, found {tok.kind}"
            )
        return self._advance()

    def parse(self) -> Optional[Expr]:
        # Empty prereq is treated as no requirement.
        if self._peek().kind == _TOKEN_EOF:
            return None

        expr = self._parse_or()

        if self._peek().kind != _TOKEN_EOF:
            tok = self._peek()
            raise PrereqSyntaxError(
                f"Unexpected token {tok.kind} ('{tok.value}') at position {tok.pos}"
            )
        return expr

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        items: List[Expr] = [left]

        while self._peek().kind == _TOKEN_OR:
            self._advance()
            right = self._parse_and()
            items.append(right)

        if len(items) == 1:
            return items[0]
        return Or(tuple(_flatten_or(items)))

    def _parse_and(self) -> Expr:
        left = self._parse_atom()
        items: List[Expr] = [left]

        while self._peek().kind == _TOKEN_AND:
            self._advance()
            right = self._parse_atom()
            items.append(right)

        # If we have two atoms adjacent without an operator, that is invalid.
        next_kind = self._peek().kind
        if next_kind in {_TOKEN_COURSE, _TOKEN_LPAREN}:
            tok = self._peek()
            raise PrereqSyntaxError(
                f"Missing operator before token at position {tok.pos}"
            )

        if len(items) == 1:
            return items[0]
        return And(tuple(_flatten_and(items)))

    def _parse_atom(self) -> Expr:
        tok = self._peek()

        if tok.kind == _TOKEN_COMMA:
            raise PrereqSyntaxError(
                f"Comma is not allowed (ambiguous) at position {tok.pos}"
            )

        if tok.kind == _TOKEN_COURSE:
            return Course(self._advance().value)

        if tok.kind == _TOKEN_LPAREN:
            self._advance()
            if self._peek().kind == _TOKEN_RPAREN:
                # () is not meaningful.
                raise PrereqSyntaxError(
                    f"Empty parentheses are not allowed at position {tok.pos}"
                )
            inner = self._parse_or()
            self._expect(_TOKEN_RPAREN)
            return inner

        if tok.kind in {_TOKEN_AND, _TOKEN_OR}:
            raise PrereqSyntaxError(
                f"Operator '{tok.value}' cannot appear here at position {tok.pos}"
            )

        if tok.kind == _TOKEN_RPAREN:
            raise PrereqSyntaxError(
                f"Unexpected ')' at position {tok.pos}"
            )

        if tok.kind == _TOKEN_EOF:
            raise PrereqSyntaxError("Unexpected end of input")

        raise PrereqSyntaxError(
            f"Unexpected token {tok.kind} ('{tok.value}') at position {tok.pos}"
        )


def _flatten_and(items: Iterable[Expr]) -> List[Expr]:
    flattened: List[Expr] = []
    for item in items:
        if isinstance(item, And):
            flattened.extend(item.items)
        else:
            flattened.append(item)
    return flattened


def _flatten_or(items: Iterable[Expr]) -> List[Expr]:
    flattened: List[Expr] = []
    for item in items:
        if isinstance(item, Or):
            flattened.extend(item.items)
        else:
            flattened.append(item)
    return flattened


# --- Public API ---


def parse(prereq: Optional[str]) -> Optional[Expr]:
    """Parse a prerequisite expression.

    Grammar:
    - Operators: AND, OR (case-insensitive)
    - Parentheses for grouping
    - AND has higher precedence than OR
    - Commas are rejected
    - Adjacent course tokens without an operator are rejected

    Returns None if prereq is empty/None.
    Raises PrereqSyntaxError on invalid input.
    """

    if prereq is None:
        prereq = ""
    prereq_str = str(prereq).strip()
    if prereq_str == "":
        return None

    tokens = tokenize(prereq_str)
    parser = _Parser(tokens)
    return parser.parse()


def normalize_course_code(code: str) -> str:
    return str(code).strip().upper()


def evaluate(
    expr: Optional[Expr],
    completed_courses: Iterable[str],
    *,
    normalizer: Callable[[str], str] = normalize_course_code,
) -> bool:
    """Evaluate the parsed prerequisite expression.

    completed_courses should contain only courses completed with passing grades.
    """

    if expr is None:
        return True

    completed_set: Set[str] = {normalizer(c) for c in completed_courses if c is not None}

    def _eval(node: Expr) -> bool:
        if isinstance(node, Course):
            return normalizer(node.code) in completed_set
        if isinstance(node, And):
            return all(_eval(child) for child in node.items)
        if isinstance(node, Or):
            return any(_eval(child) for child in node.items)
        raise TypeError(f"Unknown node type: {type(node)}")

    return _eval(expr)


def parse_and_evaluate(
    prereq: Optional[str],
    completed_courses: Iterable[str],
    *,
    normalizer: Callable[[str], str] = normalize_course_code,
) -> bool:
    """Convenience wrapper: parse prereq string then evaluate."""

    expr = parse(prereq)
    return evaluate(expr, completed_courses, normalizer=normalizer)


def to_normalized_string(expr: Optional[Expr]) -> str:
    """Render an AST back into a canonical string with uppercase operators."""

    if expr is None:
        return ""

    def _render(node: Expr, parent_prec: int) -> str:
        # Precedence: AND (2) > OR (1) > atom (3)
        if isinstance(node, Course):
            return node.code
        if isinstance(node, And):
            prec = 2
            s = " AND ".join(_render(child, prec) for child in node.items)
            return f"({s})" if prec < parent_prec else s
        if isinstance(node, Or):
            prec = 1
            s = " OR ".join(_render(child, prec) for child in node.items)
            return f"({s})" if prec < parent_prec else s
        raise TypeError(f"Unknown node type: {type(node)}")

    return _render(expr, parent_prec=0)


_NONE_SENTINELS = {'', 'none', 'n/a', 'na'}


def _clean_prereq_text(prereq: Optional[str]) -> str:
    if prereq is None:
        return ''
    s = str(prereq).strip()
    s = re.sub(r'\bAND/OR\b', ' OR ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*&\s*', ' AND ', s)
    s = re.sub(r'\s*\|\s*', ' OR ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return '' if s.lower() in _NONE_SENTINELS else s


def _row_value(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _row_course_number(row: Dict[str, Any]) -> str:
    return str(_row_value(row, 'course_number', 'Course_Number') or '').strip()


def _row_course_title(row: Dict[str, Any]) -> str:
    return str(_row_value(row, 'course_title', 'Course_Title', 'title') or '').strip()


def _row_course_prerequisite(row: Dict[str, Any]) -> str:
    return str(_row_value(row, 'prerequisite', 'Prerequisite') or '').strip()


def _row_day_period(row: Dict[str, Any]) -> str:
    return str(_row_value(row, 'day_period', 'Day_and_Period') or '').strip()


def _row_course_id(row: Dict[str, Any]) -> int | None:
    value = _row_value(row, 'course_id', 'id')
    try:
        return int(value)
    except Exception:
        return None


def _iter_courses(node: Expr) -> Iterable[Course]:
    if isinstance(node, Course):
        yield node
        return
    if isinstance(node, And):
        for item in node.items:
            yield from _iter_courses(item)
        return
    if isinstance(node, Or):
        for item in node.items:
            yield from _iter_courses(item)


def _iter_or_nodes(node: Expr) -> Iterable[Or]:
    if isinstance(node, Or):
        yield node
        for item in node.items:
            yield from _iter_or_nodes(item)
        return
    if isinstance(node, And):
        for item in node.items:
            yield from _iter_or_nodes(item)


def _node_contains_target(node: Expr, target_token: str, *, normalizer: Callable[[str], str]) -> bool:
    if isinstance(node, Course):
        return normalizer(node.code) == target_token
    if isinstance(node, And):
        return any(_node_contains_target(item, target_token, normalizer=normalizer) for item in node.items)
    if isinstance(node, Or):
        return any(_node_contains_target(item, target_token, normalizer=normalizer) for item in node.items)
    return False


def _build_title_lookup(catalog_courses: Sequence[Dict[str, Any]], *, normalizer: Callable[[str], str]) -> Dict[str, str]:
    rows = sorted(
        [dict(r or {}) for r in catalog_courses],
        key=lambda row: (
            _row_course_number(row).upper(),
            _row_course_title(row).upper(),
        ),
    )

    lookup: Dict[str, str] = {}
    for row in rows:
        number = _row_course_number(row)
        title = _row_course_title(row)
        if not title:
            continue

        token_from_number = normalizer(number)
        if token_from_number and token_from_number not in lookup:
            lookup[token_from_number] = title

        token_from_title = normalizer(title)
        if token_from_title and token_from_title not in lookup:
            lookup[token_from_title] = title
    return lookup


def _text_contains_target(prereq_text: str, target_tokens: Set[str], *, normalizer: Callable[[str], str]) -> bool:
    """Check if any target token appears as a word boundary match in the text."""
    text_upper = prereq_text.upper()
    for token in target_tokens:
        if not token:
            continue
        if token.upper() in text_upper:
            return True
    return False


def _text_or_segments(prereq_text: str) -> List[str]:
    """Split raw prerequisite text on top-level OR boundaries."""
    return re.split(r'\bOR\b', prereq_text, flags=re.IGNORECASE)


def _text_target_in_or_group(prereq_text: str, target_tokens: Set[str], *, normalizer: Callable[[str], str]) -> Tuple[bool, List[str]]:
    """Determine if the target appears inside an OR group and find alternatives.

    Returns (is_conditional, alternative_branches).
    """
    or_segments = _text_or_segments(prereq_text)
    if len(or_segments) <= 1:
        return False, []

    matching_idx: List[int] = []
    for idx, segment in enumerate(or_segments):
        segment_upper = segment.upper()
        for token in target_tokens:
            if token and token.upper() in segment_upper:
                matching_idx.append(idx)
                break

    if not matching_idx:
        return False, []

    alternatives = [
        seg.strip() for idx, seg in enumerate(or_segments)
        if idx not in matching_idx and seg.strip()
    ]
    return True, alternatives


def dependency_graph_for_course(
    *,
    course_number: str,
    course_title: str | None,
    prerequisite_text: Optional[str],
    catalog_courses: Sequence[Dict[str, Any]],
    completed_courses: Iterable[str] = (),
    normalizer: Callable[[str], str] = normalize_course_code,
) -> Dict[str, Any]:
    """Build deterministic prerequisite dependency graph sets for one course.

    Returned sets:
    - direct_requirements: normalized leaf prerequisites for the target course
    - dependent_unlockables: courses that reference the target in prerequisites
    - conditional_paths: OR-branch paths where the target satisfies one branch
    """

    target_course_number = str(course_number or '').strip()
    target_course_title = str(course_title or '').strip()

    target_tokens = {
        normalizer(target_course_number),
        normalizer(target_course_title),
    }
    target_tokens.discard('')

    completed_tokens = {normalizer(v) for v in completed_courses if str(v or '').strip()}
    title_lookup = _build_title_lookup(catalog_courses, normalizer=normalizer)

    parse_errors: List[Dict[str, str]] = []

    cleaned_prereq = _clean_prereq_text(prerequisite_text)
    direct_requirements: List[Dict[str, Any]] = []
    direct_expr = None
    direct_expr_text = ''
    direct_has_conditional = False
    direct_eligible_now = True

    if cleaned_prereq:
        try:
            direct_expr = parse(cleaned_prereq)
        except PrereqSyntaxError as exc:
            parse_errors.append(
                {
                    'scope': 'direct_requirements',
                    'course_number': target_course_number,
                    'message': str(exc),
                }
            )
            # Text-based fallback for direct requirements
            direct_expr_text = cleaned_prereq
            or_segments = _text_or_segments(cleaned_prereq)
            direct_has_conditional = len(or_segments) > 1
            terms = re.split(r'\bAND\b|\bOR\b|,|;|\(|\)', cleaned_prereq, flags=re.IGNORECASE)
            for term in terms:
                term = term.strip()
                if not term:
                    continue
                token = normalizer(term)
                if token:
                    direct_requirements.append(
                        {
                            'course_token': token,
                            'course_title': title_lookup.get(token, term),
                            'satisfied': token in completed_tokens,
                        }
                    )
        else:
            if direct_expr is not None:
                direct_expr_text = to_normalized_string(direct_expr)
                direct_has_conditional = any(True for _ in _iter_or_nodes(direct_expr))

                token_set = sorted({normalizer(node.code) for node in _iter_courses(direct_expr) if normalizer(node.code)})
                for token in token_set:
                    direct_requirements.append(
                        {
                            'course_token': token,
                            'course_title': title_lookup.get(token, token),
                            'satisfied': token in completed_tokens,
                        }
                    )

                direct_eligible_now = evaluate(direct_expr, completed_tokens, normalizer=normalizer)

    normalized_rows = sorted(
        [dict(r or {}) for r in catalog_courses],
        key=lambda row: (
            _row_course_number(row).upper(),
            _row_day_period(row).lower(),
            _row_course_title(row).upper(),
        ),
    )

    dependent_unlockables: List[Dict[str, Any]] = []
    conditional_paths: List[Dict[str, Any]] = []
    seen_unlockables: Set[Tuple[str, str]] = set()
    seen_paths: Set[Tuple[str, str, str]] = set()

    target_course_number_normalized = normalizer(target_course_number)

    for row in normalized_rows:
        row_number = _row_course_number(row)
        row_title = _row_course_title(row)
        row_day = _row_day_period(row)
        row_prereq_raw = _row_course_prerequisite(row)
        row_prereq = _clean_prereq_text(row_prereq_raw)
        if not row_prereq:
            continue

        row_number_token = normalizer(row_number)
        if row_number_token and row_number_token == target_course_number_normalized:
            continue

        try:
            row_expr = parse(row_prereq)
        except PrereqSyntaxError as exc:
            parse_errors.append(
                {
                    'scope': 'dependent_unlockables',
                    'course_number': row_number,
                    'message': str(exc),
                }
            )
            row_expr = None  # fall through to text-based matching

        if not target_tokens:
            continue

        # --- AST-based matching (preferred when parse succeeded) ---
        if row_expr is not None:
            row_has_target = any(_node_contains_target(row_expr, token, normalizer=normalizer) for token in target_tokens)
            if not row_has_target:
                continue

            unlock_key = (row_number.upper(), row_day.lower())
            if unlock_key not in seen_unlockables:
                seen_unlockables.add(unlock_key)
                dependent_unlockables.append(
                    {
                        'course_id': _row_course_id(row),
                        'course_number': row_number,
                        'course_title': row_title,
                        'day_period': row_day,
                        'prerequisite': row_prereq_raw,
                        'prerequisite_expression': to_normalized_string(row_expr),
                        'is_conditional': any(True for _ in _iter_or_nodes(row_expr)),
                    }
                )

            for or_node in _iter_or_nodes(row_expr):
                group_expression = to_normalized_string(or_node)
                child_renderings = [to_normalized_string(child) for child in or_node.items]

                for idx, child in enumerate(or_node.items):
                    if not any(_node_contains_target(child, token, normalizer=normalizer) for token in target_tokens):
                        continue

                    satisfying_branch = child_renderings[idx]
                    alternatives = [v for j, v in enumerate(child_renderings) if j != idx]
                    path_key = (row_number.upper(), group_expression, satisfying_branch)
                    if path_key in seen_paths:
                        continue
                    seen_paths.add(path_key)

                    conditional_paths.append(
                        {
                            'unlock_course_id': _row_course_id(row),
                            'unlock_course_number': row_number,
                            'unlock_course_title': row_title,
                            'group_expression': group_expression,
                            'satisfying_branch': satisfying_branch,
                            'alternative_branches': alternatives,
                        }
                    )
            continue

        # --- Text-based fallback (for unparseable free-text prerequisites) ---
        if not _text_contains_target(row_prereq, target_tokens, normalizer=normalizer):
            continue

        is_conditional, alternatives = _text_target_in_or_group(row_prereq, target_tokens, normalizer=normalizer)

        unlock_key = (row_number.upper(), row_day.lower())
        if unlock_key not in seen_unlockables:
            seen_unlockables.add(unlock_key)
            dependent_unlockables.append(
                {
                    'course_id': _row_course_id(row),
                    'course_number': row_number,
                    'course_title': row_title,
                    'day_period': row_day,
                    'prerequisite': row_prereq_raw,
                    'prerequisite_expression': row_prereq,
                    'is_conditional': is_conditional,
                }
            )

        if is_conditional and alternatives:
            path_key = (row_number.upper(), row_prereq, '')
            if path_key not in seen_paths:
                seen_paths.add(path_key)
                conditional_paths.append(
                    {
                        'unlock_course_id': _row_course_id(row),
                        'unlock_course_number': row_number,
                        'unlock_course_title': row_title,
                        'group_expression': row_prereq,
                        'satisfying_branch': '',
                        'alternative_branches': alternatives,
                    }
                )

    dependent_unlockables.sort(
        key=lambda row: (
            str(row.get('course_number') or '').upper(),
            str(row.get('day_period') or '').lower(),
            str(row.get('course_title') or '').upper(),
        )
    )
    conditional_paths.sort(
        key=lambda row: (
            str(row.get('unlock_course_number') or '').upper(),
            str(row.get('group_expression') or '').upper(),
            str(row.get('satisfying_branch') or '').upper(),
        )
    )

    return {
        'direct_requirements': direct_requirements,
        'direct_requirements_expression': direct_expr_text,
        'direct_requirements_is_conditional': direct_has_conditional,
        'direct_requirements_eligible_now': bool(direct_eligible_now),
        'dependent_unlockables': dependent_unlockables,
        'conditional_paths': conditional_paths,
        'parse_errors': parse_errors,
    }
