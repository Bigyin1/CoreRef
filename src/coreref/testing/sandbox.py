"""Small fixture builders shared by CoreRef and downstream flow tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from itertools import count
from pathlib import Path
from typing import overload

import networkx as nx

from ..api.catalog import Catalog
from ..api.records import InstalledRecord, LocalRecord, Ref
from ..api.workspace import Workspace
from ..tools.deps import DepsFile
from .git import FixtureFile, git_repository, git_upstream, write_files
from .projects import copy_projects
from .server import LocalServer, local_server


class Sandbox:
    """Build independent caches, package inputs and editable workspaces below root.

    Use a disposable directory. The caller owns the server context and cleanup;
    this helper never changes cwd, environment variables or the user's Conan home.
    """

    def __init__(self, server: LocalServer, root: Path, *, namespace: str = "test") -> None:
        self.server = server
        self.root = root.resolve()
        self.namespace = namespace
        self.projects = self.root / "projects"
        self._sequence = count()

    def catalog(self) -> Catalog:
        """Return a fresh isolated client authenticated against the shared server."""
        home = self.root / f"client-{next(self._sequence)}"
        return Catalog(self.server.configure(home))

    def package(
        self,
        ref: Ref,
        *,
        source: Path | None = None,
        sources: Mapping[str, str | bytes | FixtureFile] | None = None,
        files: Mapping[str, str | bytes | FixtureFile] | None = None,
    ) -> LocalRecord:
        """Prepare one local record; publish it with normal SDK dependencies/payload.

        ``source`` wraps an existing directory without modifying its Git state.
        ``sources`` generates a checkout with a bare origin; ``files`` creates artifacts.
        Repeated generated inputs use separate directories, including for one Ref.
        """
        directory = self.root / f"package-{next(self._sequence)}"
        if source is not None and sources is not None:
            raise ValueError("Use source for an existing directory or sources for generated files")
        if source is not None:
            source = source.resolve()
            if not source.is_dir():
                raise FileNotFoundError(source)
        if sources is not None:
            source = git_repository(directory / "source", sources)
            git_upstream(source, directory / "upstream.git")
        paths = write_files(directory / "artifacts", files or {})
        return LocalRecord(ref, source=source, files=paths)

    def ref(
        self,
        component: str,
        version: str = "1.0",
        *,
        view: str = "rtl",
        type: str = "default",
    ) -> Ref:
        """Build fixture coordinates in this sandbox's namespace."""
        return Ref.from_fields(self.namespace, component, view, type, version)

    @overload
    def publish(
        self,
        catalog: Catalog,
        records: LocalRecord,
        *,
        dependencies: tuple[Ref, ...] = (),
    ) -> InstalledRecord: ...

    @overload
    def publish(
        self,
        catalog: Catalog,
        records: Mapping[str, LocalRecord],
        *,
        dependencies: Mapping[str, tuple[str | Ref, ...]],
    ) -> dict[str, InstalledRecord]: ...

    def publish(
        self,
        catalog: Catalog,
        records: LocalRecord | Mapping[str, LocalRecord],
        *,
        dependencies: tuple[Ref, ...] | Mapping[str, tuple[str | Ref, ...]] = (),
    ) -> InstalledRecord | dict[str, InstalledRecord]:
        """Publish one record, or a named set with aliases and previously published Refs."""
        if isinstance(records, LocalRecord):
            if not isinstance(dependencies, tuple):
                raise TypeError("A single package requires a tuple of Refs")
            return catalog.publish(records, dependencies=dependencies)
        if isinstance(dependencies, tuple):
            raise TypeError("A package set requires a dependency mapping")
        return self._publish_set(catalog, records, dependencies=dependencies)

    def _publish_set(
        self,
        catalog: Catalog,
        records: Mapping[str, LocalRecord],
        *,
        dependencies: Mapping[str, tuple[str | Ref, ...]],
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
            graph.add_edges_from((dep, name) for dep in requirements if isinstance(dep, str))
        unknown = set(graph) - records.keys()
        if unknown:
            raise ValueError(f"Unknown fixture packages: {', '.join(sorted(unknown))}")
        try:
            order = tuple(nx.topological_sort(graph))
        except nx.NetworkXUnfeasible as error:
            raise ValueError("Fixture package dependencies contain a cycle") from error
        published: dict[str, InstalledRecord] = {}
        for name in order:
            refs = tuple(
                published[dep].ref if isinstance(dep, str) else dep
                for dep in dependencies.get(name, ())
            )
            published[name] = catalog.publish(records[name], dependencies=refs)
        return published

    def load_projects(self, source: Path, *, repositories: tuple[str, ...] = ()) -> Path:
        """Copy an ordinary project tree and prepare selected Git checkouts/origins.

        Return self.projects. Names are relative project directories in the copied
        tree. Manifests are left to the flow; no packages are published implicitly.
        Existing destinations are preserved, and source .git metadata is not copied.
        """
        source = source.resolve()
        if self.projects.is_relative_to(source):
            raise ValueError("Project destination must not be inside the source tree")
        for name in repositories:
            project = source / name
            if Path(name).is_absolute() or not project.resolve().is_relative_to(source):
                raise ValueError(f"Repository must be inside the project tree: {name}")
            if not project.is_dir():
                raise FileNotFoundError(project)
        copy_projects(source, self.projects)
        self._prepare_repositories(repositories)
        return self.projects

    def _prepare_repositories(self, names: tuple[str, ...]) -> None:
        projects = tuple(self.projects / name for name in names)
        if any(not path.resolve().is_relative_to(self.projects) for path in projects):
            raise ValueError("Repository symlinks must stay inside the copied project tree")
        for project in projects:
            git_repository(project)
            origin = self.root / "upstreams" / f"{next(self._sequence)}-{project.name}.git"
            git_upstream(project, origin)

    def project(
        self,
        catalog: Catalog,
        ref: Ref,
        *,
        path: Path | None = None,
        dependencies: tuple[Ref, ...] = (),
    ) -> DepsFile:
        """Create a consumer deps document; add/save/resolve it through DepsFile."""
        path = path or self.root / f"project-{next(self._sequence)}" / "deps.yml"
        document = DepsFile.create(catalog, ref, path, source=".")
        for dependency in dependencies:
            document.add(dependency)
        document.save()
        return document

    def workspace(self, catalog: Catalog, *, directory: Path | None = None) -> Workspace:
        """Initialize a native workspace; add records with Workspace.add().

        Resolve graphs from the workspace directory to enable Conan discovery.
        Set directory to the project directory or an ancestor for normal discovery.
        Tests can use monkeypatch.chdir(project.path.parent) for that scope.
        """
        directory = directory or self.root / f"workspace-{next(self._sequence)}"
        workspace = Workspace(catalog, directory / "conanws.yml")
        workspace.init()
        return workspace


@contextmanager
def environment(
    root: Path,
    *,
    namespace: str = "test",
    projects: Path | None = None,
    repositories: tuple[str, ...] = (),
) -> Iterator[Sandbox]:
    """Own a complete local package system for a flow test.

    Start/stop the Conan server, and provide builders for its isolated clients,
    Git-backed packages and editable workspaces. Optional projects is a normal
    directory tree, copied to sandbox.projects; repositories selects its Git roots.
    The caller owns directory cleanup; logs and repositories remain there on exit.
    """
    if projects is None and repositories:
        raise ValueError("Repository names require a project tree")
    root = root.resolve()
    with local_server(root / "server") as server:
        sandbox = Sandbox(server, root / "fixtures", namespace=namespace)
        if projects is not None:
            sandbox.load_projects(projects, repositories=repositories)
        yield sandbox
