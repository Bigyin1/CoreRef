"""Small fixture builders shared by CoreRef and downstream flow tests."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import count
from pathlib import Path

from dulwich import porcelain
from dulwich.refs import Ref as GitRef

from ..api.catalog import Catalog
from ..api.records import LocalRecord, Ref
from ..api.workspace import Workspace
from .server import LocalServer


def _write_files(directory: Path, files: Mapping[str, str | bytes]) -> dict[str, Path]:
    paths = {}
    for name, content in files.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"Fixture file must stay inside its directory: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        paths[name] = path
    return paths


def git_repository(directory: Path, files: Mapping[str, str | bytes]) -> Path:
    """Create a new committed Git repository suitable for Conan SCM tests.

    The destination must not exist. Local SCM URLs require a test home configured
    with ``local_sources=True``. No user Git identity or signing settings are used.
    """
    directory = directory.resolve()
    directory.mkdir(parents=True)
    with porcelain.init(directory) as repo:
        repo.refs.set_symbolic_ref(GitRef(b"HEAD"), GitRef(b"refs/heads/main"))
        _write_files(directory, files)
        porcelain.add(directory, paths=list(files))
        porcelain.commit(
            directory,
            message=b"Test sources",
            author=b"Test <test@example.invalid>",
            committer=b"Test <test@example.invalid>",
            sign=False,
        )
    return directory


class Sandbox:
    """Build independent caches, package inputs and editable workspaces below root.

    Use a disposable directory. The caller owns the server context and cleanup;
    this helper never changes cwd, environment variables or the user's Conan home.
    """

    def __init__(self, server: LocalServer, root: Path) -> None:
        self.server = server
        self.root = root.resolve()
        self._sequence = count()

    def catalog(self) -> Catalog:
        """Return a fresh isolated client authenticated against the shared server."""
        home = self.root / f"client-{next(self._sequence)}"
        return Catalog(self.server.configure(home, local_sources=True))

    def package(
        self,
        ref: Ref,
        *,
        sources: Mapping[str, str | bytes] | None = None,
        files: Mapping[str, str | bytes] | None = None,
    ) -> LocalRecord:
        """Prepare one local record; publish it with normal SDK dependencies/payload.

        ``sources`` creates a committed Git repository; ``files`` creates packaged
        artifacts. Repeated calls use different directories, including for one Ref.
        """
        directory = self.root / f"package-{next(self._sequence)}"
        source = git_repository(directory / "source", sources) if sources is not None else None
        paths = _write_files(directory / "artifacts", files or {})
        return LocalRecord(ref, source=source, files=paths)

    def workspace(self, catalog: Catalog) -> Workspace:
        """Initialize a native workspace; add records with Workspace.add().

        Resolve graphs from the workspace directory to enable Conan discovery.
        Tests can use monkeypatch.chdir(workspace.path.parent) for that scope.
        """
        directory = self.root / f"workspace-{next(self._sequence)}"
        workspace = Workspace(catalog, directory / "conanws.yml")
        workspace.init()
        return workspace
