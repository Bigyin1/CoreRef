"""Resource descriptors and the result of explicit local materialization."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from .errors import CatalogError
from .types import JSON


@dataclass(frozen=True)
class MaterializedResource:
    """An available path on the local or mounted filesystem."""

    path: Path


@dataclass(frozen=True)
class MaterializedPackage:
    """Artifact directory and optional SCM checkout, owned by the catalog cache."""

    path: Path
    source_path: Path | None = None


@dataclass(frozen=True)
class GitResource:
    """Immutable SCM coordinates; source retrieval belongs to the catalog."""

    repository_url: str
    requested_ref: str
    resolved_commit: str
    provider: Literal["git"] = field(default="git", kw_only=True)


@dataclass(frozen=True)
class LocatedResource:
    provider: str
    locator: str
    digest: str | None = None
    size: int | None = None
    media_type: str | None = None
    attributes: Mapping[str, JSON] = field(default_factory=dict)

    def materialize(self, cache_dir: str | Path | None = None) -> MaterializedResource:
        """Resolve a local or already mounted filesystem path."""
        if self.provider not in ("file", "local", "nfs"):
            raise CatalogError(f"Unsupported filesystem provider {self.provider!r}")
        try:
            parsed = urlsplit(self.locator)
            if (
                parsed.scheme == "file"
                and parsed.netloc in ("", "localhost")
                and not parsed.fragment
            ):
                path = Path(unquote(parsed.path))
            elif not parsed.scheme:
                path = Path(self.locator)
            else:
                raise CatalogError("A local or already mounted filesystem path is required")
            return MaterializedResource(path=path.expanduser().resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise CatalogError("Resource path is not available locally") from exc


__all__ = [
    "GitResource",
    "LocatedResource",
    "MaterializedPackage",
    "MaterializedResource",
]
