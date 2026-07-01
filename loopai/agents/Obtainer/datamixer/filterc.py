"""Safe filter compiler (#15).

Recipe/query predicates were validated by a regex *denylist* and then
concatenated into SQL — easy to bypass and easy to over-block. This replaces it
with a small recursive-descent parser that accepts only a controlled grammar
(whitelisted columns, a fixed operator set, and the single ``json_extract`` form
used for custom tags) and **re-emits canonical SQL from the parsed AST**. Any
input outside the grammar is rejected at parse time, so injection is impossible
regardless of what the caller does with the returned string.

Grammar (case-insensitive keywords)::

    expr   := or
    or     := and (OR and)*
    and    := not (AND not)*
    not    := NOT not | cmp
    cmp    := operand (( = | != | <> | < | <= | > | >= ) operand
                       | IS [NOT] NULL
                       | [NOT] IN '(' literal (',' literal)* ')'
                       | [NOT] LIKE string)
            | '(' expr ')'
    operand:= column | literal | json_extract '(' tags_json ',' string ')'
"""
from __future__ import annotations

import re

# whitelisted identifiers: schema core columns + the structural columns
_STRUCTURAL = ("sample_id", "dataset_id", "cid", "created_at", "version",
               "tags_json")


def _columns() -> set[str]:
    from . import schema
    return set(schema.CORE_FIELD_NAMES) | set(_STRUCTURAL)


_TOKEN_RE = re.compile(r"""
    \s*(?:
      (?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<str>'(?:[^']|'')*')
    | (?P<op><=|>=|<>|!=|=|<|>)
    | (?P<punc>[(),])
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    )""", re.VERBOSE)

_KEYWORDS = {"and", "or", "not", "is", "null", "in", "like", "between"}


def _tokenize(expr: str):
    pos, n = 0, len(expr)
    toks = []
    while pos < n:
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos:].strip() == "":
                break
            raise ValueError(f"bad token near: {expr[pos:pos+20]!r}")
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        toks.append((kind, val))
    toks.append(("end", ""))
    return toks


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0
        self.cols = _columns()

    def peek(self):
        return self.t[self.i]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def _kw(self, word):
        k, v = self.peek()
        return k == "word" and v.lower() == word

    def expect_punc(self, p):
        k, v = self.next()
        if not (k == "punc" and v == p):
            raise ValueError(f"expected '{p}'")

    # -- grammar -----------------------------------------------------------
    def parse(self) -> str:
        out = self.parse_or()
        if self.peek()[0] != "end":
            raise ValueError("trailing tokens in filter")
        return out

    def parse_or(self) -> str:
        left = self.parse_and()
        while self._kw("or"):
            self.next()
            left = f"({left} OR {self.parse_and()})"
        return left

    def parse_and(self) -> str:
        left = self.parse_not()
        while self._kw("and"):
            self.next()
            left = f"({left} AND {self.parse_not()})"
        return left

    def parse_not(self) -> str:
        if self._kw("not"):
            self.next()
            return f"(NOT {self.parse_not()})"
        return self.parse_cmp()

    def parse_cmp(self) -> str:
        if self.peek() == ("punc", "("):
            self.next()
            inner = self.parse_or()
            self.expect_punc(")")
            return f"({inner})"
        left = self.parse_operand()
        k, v = self.peek()
        if k == "op":
            self.next()
            right = self.parse_operand()
            return f"{left} {v} {right}"
        if self._kw("is"):
            self.next()
            neg = ""
            if self._kw("not"):
                self.next()
                neg = "NOT "
            if not self._kw("null"):
                raise ValueError("expected NULL after IS [NOT]")
            self.next()
            return f"{left} IS {neg}NULL"
        neg = ""
        if self._kw("not"):
            self.next()
            neg = "NOT "
        if self._kw("in"):
            self.next()
            self.expect_punc("(")
            items = [self.parse_literal()]
            while self.peek() == ("punc", ","):
                self.next()
                items.append(self.parse_literal())
            self.expect_punc(")")
            return f"{left} {neg}IN ({', '.join(items)})"
        if self._kw("like"):
            self.next()
            return f"{left} {neg}LIKE {self.parse_string()}"
        raise ValueError(f"expected an operator after operand, got {v!r}")

    def parse_operand(self) -> str:
        k, v = self.peek()
        if k in ("num", "str"):
            return self.parse_literal()
        if k == "word":
            low = v.lower()
            if low in _KEYWORDS:
                raise ValueError(f"unexpected keyword {v!r}")
            if low == "json_extract":
                return self.parse_json_extract()
            if low == "null":
                return self.parse_literal()
            if v not in self.cols:
                raise ValueError(f"unknown/!whitelisted column: {v!r}")
            self.next()
            return v
        raise ValueError(f"unexpected token {v!r}")

    def parse_json_extract(self) -> str:
        self.next()  # json_extract
        self.expect_punc("(")
        k, v = self.next()
        if not (k == "word" and v == "tags_json"):
            raise ValueError("json_extract only allowed on tags_json")
        self.expect_punc(",")
        path = self.parse_string()
        self.expect_punc(")")
        return f"json_extract(tags_json, {path})"

    def parse_literal(self) -> str:
        k, v = self.peek()
        if k == "num":
            self.next()
            return v
        if k == "str":
            return self.parse_string()
        if k == "word" and v.lower() == "null":
            self.next()
            return "NULL"
        raise ValueError(f"expected a literal, got {v!r}")

    def parse_string(self) -> str:
        k, v = self.next()
        if k != "str":
            raise ValueError("expected a string literal")
        return v  # already single-quoted with '' escaping per the token regex


def compile_filter(expr: str | None) -> str:
    """Validate and canonicalize a filter; raise ValueError if not in grammar."""
    if not expr or not expr.strip():
        return ""
    return _Parser(_tokenize(expr)).parse()
