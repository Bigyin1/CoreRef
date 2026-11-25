"""Resolve input names using the graph in a native Nix lock file."""

from __future__ import annotations

from typing import TypedDict


class InputNode(TypedDict, total=False):
    inputs: dict[str, str | list[str]]
    locked: dict[str, str | int | bool]
    flake: bool


class FlakeLock(TypedDict):
    root: str
    nodes: dict[str, InputNode]


def root_node(lock: FlakeLock) -> InputNode:
    return lock["nodes"][lock["root"]]


def input_node(lock: FlakeLock, name: str) -> InputNode:
    """Look up a direct or nested input and resolve Nix follows edges."""
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Invalid flake input path {name!r}")
    try:
        return _find(lock, parts)
    except KeyError as exc:
        raise ValueError(f"Unknown flake input {name!r}") from exc


def _find(lock: FlakeLock, names: list[str]) -> InputNode:
    node = root_node(lock)
    for name in names:
        edge = node.get("inputs", {})[name]
        node = _find(lock, edge) if isinstance(edge, list) else lock["nodes"][edge]
    return node
