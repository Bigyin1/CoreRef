"""Small fixture builders shared by CoreRef and downstream flow tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from io import BytesIO
from itertools import count
from pathlib import Path

import networkx as nx
from dulwich import porcelain
from dulwich.refs import Ref as GitRef
from dulwich.repo import Repo

from ..api.catalog import Catalog
from ..api.records import InstalledRecord, LocalRecord, Ref
from ..api.workspace import Workspace
from .server import LocalServer, local_server


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


def git_upstream(checkout: Path, directory: Path) -> Path:
    """Create a bare origin for an existing checkout, including tracking refs.

    The destination must not exist. Tests can commit and push subsequent changes
    with Dulwich or Git, just as they would with a remote repository.
    """
    directory = directory.resolve()
    directory.mkdir(parents=True)
    with porcelain.clone(str(checkout), directory, bare=True, errstream=BytesIO()):
        pass
    with Repo(checkout) as repo:
        config = repo.get_config()
        config.set((b"remote", b"origin"), b"url", str(directory).encode())
        config.write_to_path()
        branches, _ = repo.refs.follow(GitRef(b"HEAD"))
        branch = branches[-1].removeprefix(b"refs/heads/")
        repo.refs[GitRef(b"refs/remotes/origin/" + branch)] = repo.head()
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

        ``sources`` creates a checkout with a committed bare origin; ``files`` creates packaged
        artifacts. Repeated calls use different directories, including for one Ref.
        """
        directory = self.root / f"package-{next(self._sequence)}"
        source = git_repository(directory / "source", sources) if sources is not None else None
        if source is not None:
            git_upstream(source, directory / "upstream.git")
        paths = _write_files(directory / "artifacts", files or {})
        return LocalRecord(ref, source=source, files=paths)

    def publish(
        self,
        catalog: Catalog,
        records: Mapping[str, LocalRecord],
        *,
        dependencies: Mapping[str, tuple[str, ...]],
    ) -> dict[str, InstalledRecord]:
        """Publish a named package set, ordering dependencies automatically.

        Dependency names refer to entries in records. Missing entries and cycles
        are rejected before publication. Conan errors can leave earlier packages
        published; this helper does not provide a transaction or rollback.
        """
        graph: nx.DiGraph[str] = nx.DiGraph()
        graph.add_nodes_from(records)
        graph.add_nodes_from(dependencies)
        for name, requirements in dependencies.items():
            graph.add_edges_from((dependency, name) for dependency in requirements)
        unknown = set(graph) - records.keys()
        if unknown:
            raise ValueError(f"Unknown fixture packages: {', '.join(sorted(unknown))}")
        try:
            order = tuple(nx.topological_sort(graph))
        except nx.NetworkXUnfeasible as error:
            raise ValueError("Fixture package dependencies contain a cycle") from error
        published: dict[str, InstalledRecord] = {}
        for name in order:
            refs = tuple(published[dep].ref for dep in dependencies.get(name, ()))
            published[name] = catalog.publish(records[name], dependencies=refs)
        return published

    def workspace(self, catalog: Catalog) -> Workspace:
        """Initialize a native workspace; add records with Workspace.add().

        Resolve graphs from the workspace directory to enable Conan discovery.
        Tests can use monkeypatch.chdir(workspace.path.parent) for that scope.
        """
        directory = self.root / f"workspace-{next(self._sequence)}"
        workspace = Workspace(catalog, directory / "conanws.yml")
        workspace.init()
        return workspace


@contextmanager
def environment(root: Path) -> Iterator[Sandbox]:
    """Own a complete local package system for a flow test.

    Start/stop the Conan server, and provide builders for its isolated clients,
    Git-backed packages and editable workspaces. The caller supplies a disposable
    directory and owns its cleanup; logs and repositories remain there on exit.
    """
    root = root.resolve()
    with local_server(root / "server") as server:
        yield Sandbox(server, root / "fixtures")
