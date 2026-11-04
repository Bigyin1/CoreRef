"""Public entry point for the persistent Memgraph catalog backend."""

from .._internal.backends.memgraph import MemgraphBackend

__all__ = ["MemgraphBackend"]
