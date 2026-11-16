"""Recursive-descent parser for the search query language.

Grammar (EBNF)::

    query     := or_expr
    or_expr   := and_expr ("||" and_expr)*
    and_expr  := unary ("&&" unary)*
    unary     := "!" unary | "(" query ")" | predicate | bareword
    predicate := field COMPARISON_OP value
    value     := word | quoted

Two rules are deliberately strict, because a search box that guesses is worse
than one that explains itself:

* Only a field comparison (a known field name or alias followed by an operator)
  or a boolean connector makes :func:`has_syntax` treat text as a query; once it
  does, the text is parsed strictly or rejected with a column -- it never quietly
  degrades into a keyword search that happens to return something. Parens,
  ``!`` and bare ``=`` in keyword text stay keywords (task-126).
* Adjacent expressions need an explicit ``&&``: inside a grammar query "a b" is a
  typo, not an implied AND, and the error says which ``&&`` is missing.
"""

from __future__ import annotations

import difflib
import re

from l03_domain.query_lang.ast import And, Bareword, Comparison, Node, Not, Or
from l03_domain.query_lang.errors import QueryError
from l03_domain.query_lang.fields import FIELD_ALIASES, FIELDS, field_names, operator_hint
from l03_domain.query_lang.lexer import (
    COMPARISON_OPS,
    OP,
    STRING,
    WORD,
    Token,
    tokenize,
)

#: A query longer than this is a runaway paste, not a search.
MAX_QUERY_LENGTH = 512
#: Each OR-ed branch multiplies the rows a scan has to touch.
MAX_PREDICATES = 20
#: Deep nesting only ever comes from a machine or a stuck key.
MAX_DEPTH = 8

def _syntax_trigger_re() -> re.Pattern[str]:
    """One regex matching "known field name (or alias) + comparison operator"."""
    names = sorted({*FIELDS, *FIELD_ALIASES}, key=len, reverse=True)
    ops = sorted(COMPARISON_OPS, key=len, reverse=True)
    # 字段名不区分大小写：MODEL=Gpt-4 与 model=Gpt-4 同判（操作符无大小写可言）。
    return re.compile(
        rf"(?<![\w])(?:{'|'.join(map(re.escape, names))})"
        rf"\s*(?:{'|'.join(map(re.escape, ops))})",
        re.IGNORECASE,
    )


_SYNTAX_TRIGGER = _syntax_trigger_re()


def _outside_quotes(text: str) -> str:
    """The text with every quoted region blanked, so scanners see literals only."""
    chars = list(text)
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in "\"'":
            quote = char
            chars[index] = " "
            index += 1
            while index < length:
                if text[index] == "\\" and index + 1 < length:
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    continue
                if text[index] == quote:
                    chars[index] = " "
                    index += 1
                    break
                chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


def has_syntax(text: str) -> bool:
    """True when ``text`` is trying to be a query rather than a keyword.

    v2 口径（task-126 收紧）：只有两种东西算语法——布尔连接符 ``&&`` / ``||``，
    或者已知字段名/别名（含 ``requested_model`` 这类真实列名，见
    :data:`~l03_domain.query_lang.fields.FIELD_ALIASES`）后面跟着比较操作符。
    其余一切照旧当关键词：括号、感叹号、裸 ``=``、未知字段（``a=b``），甚至整段
    ``429 (Too Many Requests)``。判定表::

        429 (Too Many Requests) -> 关键词
        upstream_model=x        -> 语法（别名命中）
        a=b                     -> 关键词（未知字段）
        status >= 400           -> 语法
        foo && bar              -> 语法
        (a)                     -> 关键词

    旧口径把含 ``= ( ) < > !`` 的文本整条判成语法再 400，用户从能搜到变成不能搜，
    属回归；而 ``NO_AVAILABLE_CHANNEL``、``minimax-m3.1-flash`` 这类裸词从未含
    「字段名+操作符」组合，收紧后仍是关键词。引号内一律不算（``"a=b"`` 仍关键词）。
    """
    masked = _outside_quotes(text)
    if "&&" in masked or "||" in masked:
        return True
    return _SYNTAX_TRIGGER.search(masked) is not None


def parse(text: str) -> Node:
    """Parse ``text``, picking the mode with :func:`has_syntax`."""
    return parse_query(text)


def parse_query(text: str, *, strict: bool | None = None) -> Node:
    """Parse ``text`` into a :data:`~l03_domain.query_lang.ast.Node`.

    ``strict=None`` (the default) decides per query: grammar punctuation means
    strict parsing, anything else is one keyword search term (spaces kept, which
    is what the existing search box matches on). ``strict=False`` is the escape
    hatch for a literal keyword search of text that looks like a query.
    """
    if len(text) > MAX_QUERY_LENGTH:
        raise QueryError(
            f"查询过长（{len(text)} 字符，上限 {MAX_QUERY_LENGTH}）",
            column=MAX_QUERY_LENGTH + 1,
            suggestion="把长的关键词换成更短的子串，或拆成几次搜索",
        )
    stripped = text.strip()
    if stripped == "":
        raise QueryError(
            "查询为空",
            column=1,
            suggestion="输入关键词，或使用 model=、status>= 这类字段语法",
        )
    if strict is None:
        strict = has_syntax(text)
    if not strict:
        return Bareword(_keyword_term(text, stripped), 1)
    return _Parser(text).parse()


def _keyword_term(text: str, stripped: str) -> str:
    """The text a keyword search should match, without wrapping quotes.

    A fully quoted term ("gpt 4", 'gpt 4') is still one keyword; searching for
    the quote characters themselves would return nothing.
    """
    try:
        tokens = tokenize(text)
    except QueryError:
        return stripped
    if len(tokens) == 1 and tokens[0].kind == STRING:
        return tokens[0].value
    return stripped


def _suggest_field(name: str) -> str:
    available = "、".join(field_names())
    matches = difflib.get_close_matches(name.lower(), field_names(), n=1, cutoff=0.6)
    if matches:
        return f"是否想写 {matches[0]}？可用字段：{available}"
    return f"可用字段：{available}"


class _Parser:
    """One-shot parser over the token stream of a single query."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._tokens = tokenize(text)
        self._index = 0
        self._predicates = 0

    def parse(self) -> Node:
        if not self._tokens:
            raise QueryError(
                "查询为空",
                column=1,
                suggestion="输入关键词，或使用 model=、status>= 这类字段语法",
            )
        node = self._parse_or(0)
        trailing = self._peek()
        if trailing is not None:
            raise self._unexpected(trailing)
        return node

    def _peek(self, offset: int = 0) -> Token | None:
        position = self._index + offset
        if position < len(self._tokens):
            return self._tokens[position]
        return None

    def _advance(self) -> Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _parse_or(self, depth: int) -> Node:
        items = [self._parse_and(depth)]
        while (token := self._peek()) is not None and token.is_op("||"):
            self._advance()
            items.append(self._parse_and(depth))
        if len(items) == 1:
            return items[0]
        return Or(tuple(items))

    def _parse_and(self, depth: int) -> Node:
        items = [self._parse_unary(depth)]
        while (token := self._peek()) is not None and token.is_op("&&"):
            self._advance()
            items.append(self._parse_unary(depth))
        if len(items) == 1:
            return items[0]
        return And(tuple(items))

    def _parse_unary(self, depth: int) -> Node:
        token = self._peek()
        if token is None:
            raise QueryError(
                "查询不完整",
                column=len(self._text) + 1,
                suggestion="补上字段值，例如 model=gpt-4",
            )
        if depth > MAX_DEPTH:
            raise QueryError(
                f"表达式嵌套超过 {MAX_DEPTH} 层",
                column=token.column,
                suggestion="把条件拆成几次搜索",
            )
        if token.kind == OP:
            if token.value == "!":
                self._advance()
                return Not(self._parse_unary(depth + 1))
            if token.value == "(":
                return self._parse_group(depth, token)
            if token.value == ")":
                raise QueryError(
                    "')' 前缺少表达式",
                    column=token.column,
                    suggestion="检查括号里的内容是否为空",
                )
            if token.value in COMPARISON_OPS:
                raise QueryError(
                    f"操作符 {token.value} 前缺少字段名",
                    column=token.column,
                    suggestion="例如 model=gpt-4、status>=400",
                )
            raise QueryError(
                f"操作符 {token.value} 前缺少表达式",
                column=token.column,
                suggestion="例如 model=a && status>=400",
            )
        if token.kind == WORD:
            following = self._peek(1)
            if following is not None and following.is_op(*COMPARISON_OPS):
                return self._parse_comparison()
        self._advance()
        self._note_predicate(token.column)
        return Bareword(token.value, token.column)

    def _parse_group(self, depth: int, opening: Token) -> Node:
        self._advance()
        inner = self._peek()
        if inner is None:
            raise QueryError(
                "括号没有闭合",
                column=opening.column,
                suggestion="在结尾补一个 ')'",
            )
        if inner.is_op(")"):
            raise QueryError(
                "括号内不能为空",
                column=inner.column,
                suggestion="例如 (model=a || model=b)",
            )
        node = self._parse_or(depth + 1)
        closing = self._peek()
        if closing is None or not closing.is_op(")"):
            raise QueryError(
                "括号没有闭合",
                column=opening.column,
                suggestion="在结尾补一个 ')'",
            )
        self._advance()
        return node

    def _parse_comparison(self) -> Comparison:
        field_token = self._advance()
        operator_token = self._advance()
        value_token = self._peek()
        if value_token is None or value_token.kind == OP:
            raise QueryError(
                f"操作符 {operator_token.value} 后缺少值",
                column=value_token.column if value_token is not None else len(self._text) + 1,
                suggestion=f"例如 {field_token.value}=gpt-4",
            )
        self._advance()
        name = field_token.value.lower()
        # 真实列名都是别名（task-126）：解析即归一到主名，谓词与帮助表只认主名。
        name = FIELD_ALIASES.get(name, name)
        field = FIELDS.get(name)
        if field is None:
            raise QueryError(
                f"未知字段 {field_token.value}",
                column=field_token.column,
                suggestion=_suggest_field(field_token.value),
            )
        if operator_token.value not in field.operators:
            raise QueryError(
                f"字段 {name} 不支持 {operator_token.value}",
                column=operator_token.column,
                suggestion=f"{field.hint}；{operator_hint(field)}",
            )
        self._note_predicate(field_token.column)
        return Comparison(
            field=name,
            op=operator_token.value,
            value=value_token.value,
            column=field_token.column,
            value_column=value_token.column,
        )

    def _note_predicate(self, column: int) -> None:
        self._predicates += 1
        if self._predicates > MAX_PREDICATES:
            raise QueryError(
                f"谓词超过 {MAX_PREDICATES} 个上限",
                column=column,
                suggestion="先按时间范围或字段缩小查询，再分多次搜索",
            )

    def _unexpected(self, token: Token) -> QueryError:
        if token.is_op(")"):
            return QueryError(
                "多了一个 ')'",
                column=token.column,
                suggestion="检查括号是否成对",
            )
        if token.kind == OP:
            return QueryError(
                f"操作符 {token.value} 前缺少表达式",
                column=token.column,
                suggestion="例如 model=a && status>=400",
            )
        return QueryError(
            "两个表达式之间缺少 && 或 ||",
            column=token.column,
            suggestion="例如 model=a && status>=400",
        )
