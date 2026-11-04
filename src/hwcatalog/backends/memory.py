"""Public entry point for the in-memory catalog backend."""

from .._internal.backends.memory import MemoryBackend

__all__ = ["MemoryBackend"]
