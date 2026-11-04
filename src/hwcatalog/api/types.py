"""Public portable JSON value contract shared by domain models and adapters."""

from collections.abc import Mapping

type JSON = bool | int | float | str | Mapping[str, JSON] | list[JSON] | tuple[JSON, ...] | None


__all__ = ["JSON"]
