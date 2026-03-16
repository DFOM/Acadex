from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple, Union


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
            self._advance()
            return Course(tok.value)

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
