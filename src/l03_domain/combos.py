from __future__ import annotations

from collections.abc import Mapping, Sequence

MAX_COMBO_DEPTH = 3


class ComboGraphError(ValueError):
    """A nesting problem that must be rejected when a combo is saved."""

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def _depth_of(
    node: str,
    edges: Mapping[str, Sequence[str]],
    visiting: set[str],
    depths: dict[str, int],
) -> int:
    if node in depths:
        return depths[node]
    if node in visiting:
        raise ComboGraphError("组合存在循环引用，请移除形成环的子组合", kind="cycle")

    visiting.add(node)
    children = list(edges.get(node, ()))
    depth = 1
    for child in children:
        if child == node:
            raise ComboGraphError("组合不能引用自身", kind="cycle")
        depth = max(depth, 1 + _depth_of(child, edges, visiting, depths))
    visiting.discard(node)

    if depth > MAX_COMBO_DEPTH:
        raise ComboGraphError(
            f"组合嵌套最多 {MAX_COMBO_DEPTH} 层，当前保存后会达到 {depth} 层",
            kind="depth",
        )
    depths[node] = depth
    return depth


def validate_combo_graph(edges: Mapping[str, Sequence[str]]) -> dict[str, int]:
    """Validate the whole combo graph and return each combo's max depth.

    ``edges`` maps a combo id to the ids of child combos it references. Channel
    model members do not appear as nodes. A single combo is depth 1.
    """
    depths: dict[str, int] = {}
    for node in edges:
        _depth_of(node, edges, set(), depths)
    return depths


def combo_depth(edges: Mapping[str, Sequence[str]], combo_id: str) -> int:
    return validate_combo_graph(edges).get(combo_id, 1)
