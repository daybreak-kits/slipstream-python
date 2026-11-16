"""Tokenizer for the search query language.

A value may be delimited by either ``"`` or ``'``, and the closing quote must match
the opening one. Both styles exist because operators paste values from SQL, curl
and JSON: with double quotes alone, ``model='basis'`` would quietly search for the
literal text ``'basis'`` instead of ``basis``, which is the worst kind of failure --
zero results with no error. An unmatched quote is an error, never a literal.

Every token records the 1-based column of its first character, because that is
the only way a syntax error can point at the typo instead of at the whole query.
"""

from __future__ import annotations

from dataclasses import dataclass

from l03_domain.query_lang.errors import QueryError

#: Token kinds. Words and strings differ only in whether the user quoted them;
#: the parser keeps the distinction for error messages.
WORD = "word"
STRING = "string"
OP = "op"

#: Two-character operators are matched before one-character ones, so ``==`` is
#: never read as two ``=``.
TWO_CHAR_OPS = ("&&", "||", "==", "!=", "*=", ">=", "<=")
ONE_CHAR_OPS = ("!", "(", ")", "=", ">", "<")

#: Operators that make a word a field reference (the rest are structural).
COMPARISON_OPS = frozenset({"==", "!=", "=", "*=", ">", ">=", "<", "<="})

_WHITESPACE = " \t\r\n"
_WORD_BREAK = frozenset('()"!=<>&|')
#: A quote only starts a value at the beginning of a token, so an apostrophe
#: inside a word ("don't") stays part of that word.
_QUOTES = frozenset("\"'")


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    value: str
    column: int

    def is_op(self, *values: str) -> bool:
        return self.kind == OP and self.value in values


def tokenize(text: str) -> list[Token]:
    """Split ``text`` into tokens, or raise :class:``QueryError`` at the bad char."""
    tokens: list[Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in _WHITESPACE:
            index += 1
            continue
        if char in _QUOTES:
            value, index_after = _read_string(text, index)
            tokens.append(Token(STRING, value, index + 1))
            index = index_after
            continue
        operator = _match_operator(text, index)
        if operator is not None:
            tokens.append(Token(OP, operator, index + 1))
            index += len(operator)
            continue
        start = index
        while index < length:
            char = text[index]
            if char in _WHITESPACE or char in _WORD_BREAK:
                break
            # A "*=" right after a word is the wildcard operator, not part of
            # the word: "model*=gpt*" must read as word/op/word.
            if char == "*" and index + 1 < length and text[index + 1] == "=":
                break
            index += 1
        if index == start:
            raise QueryError(
                f"无法识别的字符 {text[start]!r}",
                column=start + 1,
                suggestion="只支持 && || ! ( ) 与 == != = *= > >= < <= 这些语法符号，其余字符请放进引号里",
            )
        tokens.append(Token(WORD, text[start:index], start + 1))
    return tokens


def _match_operator(text: str, index: int) -> str | None:
    pair = text[index : index + 2]
    if pair in TWO_CHAR_OPS:
        return pair
    single = text[index]
    if single in ONE_CHAR_OPS:
        return single
    return None


def _read_string(text: str, start: int) -> tuple[str, int]:
    """Read a quoted value; the closing quote must match the opening one."""
    quote = text[start]
    out: list[str] = []
    index = start + 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length and text[index + 1] in (quote, "\\"):
            out.append(text[index + 1])
            index += 2
            continue
        if char == quote:
            return "".join(out), index + 1
        out.append(char)
        index += 1
    raise QueryError(
        "字符串没有闭合的引号",
        column=start + 1,
        suggestion=f"值需要引号闭合：在结尾补一个 {quote}",
    )
