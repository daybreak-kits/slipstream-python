"""The query tree produced by :mod:`l03_domain.query_lang.parser`.

Nodes are frozen value objects: the parser builds each node once and the
compiler only reads it, so a parsed query can be compiled repeatedly (per
request, per dialect) without defensive copies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True, slots=True)
class Bareword:
    """Free text with no field, e.g. ``NO_AVAILABLE_CHANNEL``.

    A bareword is not an unknown field: it asks for a substring search over
    every default column, which is what the search box has always done.
    """

    text: str
    column: int


@dataclass(frozen=True, slots=True)
class Comparison:
    """``field op value``; ``value`` stays the user's text until compilation.

    Types are coerced by the compiler, not the parser, so ``status=abc`` is a
    type error that still knows where in the query the value sits.
    """

    field: str
    op: str
    value: str
    column: int
    value_column: int = 0


@dataclass(frozen=True, slots=True)
class And:
    items: tuple["Node", ...]


@dataclass(frozen=True, slots=True)
class Or:
    items: tuple["Node", ...]


@dataclass(frozen=True, slots=True)
class Not:
    item: "Node"


Node = Union[Bareword, Comparison, And, Or, Not]

#: The node types that carry a predicate (and therefore count against the
#: per-query predicate budget).
PREDICATES: tuple[type, ...] = (Bareword, Comparison)


def iter_predicates(node: Node) -> list[Bareword | Comparison]:
    """Every leaf of ``node``, in source order."""
    if isinstance(node, PREDICATES):
        return [node]
    if isinstance(node, (And, Or)):
        found: list[Bareword | Comparison] = []
        for item in node.items:
            found.extend(iter_predicates(item))
        return found
    if isinstance(node, Not):
        return iter_predicates(node.item)
    raise TypeError(f"unknown query node: {node!r}")


def count_predicates(node: Node) -> int:
    return len(iter_predicates(node))


def depth(node: Node) -> int:
    """Nesting depth of the tree; a single predicate is depth 1."""
    if isinstance(node, PREDICATES):
        return 1
    if isinstance(node, (And, Or)):
        return 1 + max((depth(item) for item in node.items), default=0)
    if isinstance(node, Not):
        return 1 + depth(node.item)
    raise TypeError(f"unknown query node: {node!r}")
