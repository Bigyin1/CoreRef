"""Resource descriptors and the result of explicit local materialization."""

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Self
from urllib.parse import unquote, urlsplit

from ..errors import GitResolutionError, MaterializationError
from ..types import JSON


@dataclass(frozen=True)
class MaterializedResource:
    """Local content path; for Git, the root of its cached working repository."""

    path: Path


class Resource(ABC):
    """Common materialization contract for resource descriptors."""

    @abstractmethod
    def materialize(self, cache_dir: str | Path) -> MaterializedResource:
        """Provide local content using the supplied cache directory when needed."""
        ...


@dataclass(frozen=True)
class GitResource(Resource):
    repository_url: str
    requested_ref: str
    resolved_commit: str
    provider: Literal["git"] = field(default="git", kw_only=True)

    @classmethod
    def from_repository(
        cls,
        repository_path: str | Path,
        *,
        repository_url: str,
        requested_ref: str = "HEAD",
    ) -> Self:
        """Resolve a local branch, tag or commit without fetching or changing the repo.

        Requires ``hwcatalog[git]``. The path must point to a repository root or
        bare repository. The URL is explicit; remote configuration and credentials
        are never copied.
        """
        return cls(
            repository_url=repository_url,
            requested_ref=requested_ref,
            resolved_commit=cls._resolve_commit(repository_path, requested_ref),
        )

    def materialize(self, cache_dir: str | Path) -> MaterializedResource:
        """Reuse or clone the exact commit into cache_dir; return the repository root.

        Requires hwcatalog[git]. A cache at the requested commit is reused without
        network access or checkout. Local file changes are not inspected.
        """
        try:
            from dulwich.errors import GitProtocolError, NotGitRepository
            from dulwich.repo import Repo
        except ModuleNotFoundError as exc:
            raise MaterializationError(
                "Git materialization requires installing hwcatalog[git]"
            ) from exc

        try:
            root = Path(cache_dir).expanduser().resolve() / "git"
            root.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(
                (self.repository_url + "\0" + self.resolved_commit).encode("utf-8")
            ).hexdigest()
            repository_path = root / key
            if repository_path.exists():
                with Repo(repository_path) as repo:
                    if repo.head().decode("ascii") != self.resolved_commit:
                        raise MaterializationError("Git cache contains a different commit")
            else:
                with TemporaryDirectory(prefix=f".{key}-", dir=root) as staging:
                    checkout = Path(staging) / "repository"
                    self._clone_commit(checkout)
                    checkout.rename(repository_path)
            return MaterializedResource(path=repository_path)
        except (GitProtocolError, NotGitRepository, KeyError, ValueError, OSError) as exc:
            raise MaterializationError(
                f"Cannot materialize Git resource at commit {self.resolved_commit}"
            ) from exc

    def _clone_commit(self, destination: Path) -> None:
        from dulwich.objects import ObjectID
        from dulwich.objectspec import parse_commit
        from dulwich.porcelain import clone
        from dulwich.refs import Ref

        with clone(self.repository_url, destination, checkout=False, errstream=BytesIO()) as repo:
            pinned = parse_commit(repo, self.resolved_commit)
            # porcelain.checkout/update_head use Commit.id (SHA-1); preserve SHA-256 too.
            del repo.refs[Ref(b"HEAD")]
            repo.refs[Ref(b"HEAD")] = ObjectID(pinned.get_id(repo.object_format))
            repo.get_worktree().reset_index(pinned.tree)

    @staticmethod
    def _resolve_commit(repository_path: str | Path, requested_ref: str) -> str:
        try:
            from dulwich.errors import NotGitRepository
            from dulwich.objectspec import parse_commit
            from dulwich.repo import Repo
        except ModuleNotFoundError as exc:
            raise GitResolutionError("Git support requires installing hwcatalog[git]") from exc

        try:
            with Repo(repository_path) as repo:
                commit = parse_commit(repo, requested_ref)
                return commit.get_id(repo.object_store.object_format).decode("ascii")
        except (NotGitRepository, KeyError, ValueError, OSError) as exc:
            raise GitResolutionError(
                f"Cannot resolve local Git reference {requested_ref!r}"
            ) from exc


@dataclass(frozen=True)
class LocatedResource(Resource):
    provider: str
    locator: str
    digest: str | None = None
    size: int | None = None
    media_type: str | None = None
    attributes: Mapping[str, JSON] = field(default_factory=dict)

    def materialize(self, cache_dir: str | Path | None = None) -> MaterializedResource:
        """Resolve a local or already mounted filesystem path."""
        return self._materialize()

    def _materialize(self) -> MaterializedResource:
        if self.provider not in ("file", "local", "nfs"):
            raise MaterializationError(f"Unsupported filesystem provider {self.provider!r}")
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
                raise MaterializationError("A local or already mounted filesystem path is required")
            return MaterializedResource(path=path.expanduser().resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise MaterializationError("Resource path is not available locally") from exc


__all__ = ["GitResource", "LocatedResource", "MaterializedResource", "Resource"]
