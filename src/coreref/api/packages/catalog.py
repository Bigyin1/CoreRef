"""Search, fetch and publish packages through a configured Conan registry.

Start with ``search_refs`` when you only need coordinates. Use ``info``
for recipe metadata and ``get`` for installed files. To fetch several
packages with the same resolution, use a project ``Graph``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ..._internal.references import from_conan_reference
from ..errors import InvalidPackage
from .query import SearchQuery
from .records import (
    ConfiguredPackage,
    Dependency,
    InstalledRecord,
    LocalRecord,
    PackageInput,
    Record,
    Ref,
)

if TYPE_CHECKING:
    from ..._internal.package import LocalPackage, StoredPackage
    from ..projects.graph import Graph


class Catalog:
    """Connect package operations to a Conan home and registry.

    A catalog can work with the local cache when no remote is configured.
    Publishing requires a remote. Importing this class leaves Conan unloaded
    until a catalog is constructed.
    """

    def __init__(
        self, home: str | Path | None = None, *, remote: str | None = None, timeout: float = 60
    ) -> None:
        """Open an existing Conan configuration.

        ``home`` points to the Conan home. If omitted, use Conan's default.
        ``remote`` selects a registry by name. When the home lists multiple
        remotes, provide a name explicitly. ``timeout`` is the command timeout
        in seconds.
        """
        from ..._internal.client import ConanClient
        from ..._internal.variants import Variants

        self._client = ConanClient(Path(home) if home is not None else None, remote, timeout)
        self._variants = Variants(self)

    def search_refs(self, query: SearchQuery, *, all_revisions: bool = False) -> tuple[Ref, ...]:
        """Return matching recipe references with concrete revisions.

        The default is Conan's latest revision for each matching name and
        version. Set ``all_revisions=True`` to include history. A revision in
        the query selects that exact revision in either mode. The returned
        tuple is sorted by coordinates. Search lists references without
        downloading recipe metadata or package binaries.
        """
        pattern = query._conan_pattern(all_revisions=all_revisions)
        refs = []
        for reference, revisions in self._client.index(pattern).items():
            for revision in revisions.revisions:
                try:
                    ref = from_conan_reference(f"{reference}#{revision}")
                except InvalidPackage:
                    # A search can include Conan names outside the CoreRef grammar.
                    continue
                if query.matches_ref(ref):
                    refs.append(ref)
        return tuple(sorted(refs))

    def info(self, ref: Ref) -> Record:
        """Read a package's recipe metadata as a ``Record``.

        Conan may fetch the recipe from the configured remote. File names and
        source availability come from metadata. To get paths, call ``get``.
        """
        return self._package(ref).info()

    def get(self, ref: Ref, *, with_sources: bool = False) -> InstalledRecord:
        """Install packaged files and return their local paths.

        Conan resolves a reference without a revision. Set
        ``with_sources=True`` to also obtain Git sources if the recipe declares
        them. When Conan selects an editable package, returned paths can point
        into its working checkout.
        """
        return self._package(ref).get(with_sources=with_sources)

    def get_variant(
        self, package: PackageInput, *, lockfile: Path | None = None, build_missing: bool = False
    ) -> ConfiguredPackage:
        """Install the binary for a requirement and its options.

        Pass a lockfile to use its recipe selection. With ``lockfile=None``,
        Conan resolves the requirement without that lock. Set
        ``build_missing=True`` to permit building the requested configuration
        when its binary is unavailable. The result includes the exact recipe
        and binary identities and the local installation directory.
        """
        return self._variants._get(package, lockfile=lockfile, build_missing=build_missing)

    def publish_variant_recipe(
        self,
        ref: Ref,
        *,
        options: Mapping[str, tuple[str | int | bool, ...]],
        inputs: Mapping[str, Path],
        runner: Path,
    ) -> Ref:
        """Publish a recipe for an option-dependent binary.

        ``ref`` must omit the recipe revision. The first value of each option
        domain is its default. ``inputs`` maps names in the recipe's source
        directory to local files. ``runner`` is a Python file defining
        ``generate(options, source, output)``. Conan passes selected option
        values as strings and paths for the inputs and output directory.
        Return the exact reference of the published recipe.
        """
        return self._variants.publish_generated_recipe(
            ref, options=options, inputs=inputs, runner=runner
        )

    def publish_variant(self, package: ConfiguredPackage) -> None:
        """Upload the exact installed binary to the configured registry.

        Use the result of ``get_variant`` from a catalog with access to the
        same Conan cache.
        """
        self._variants.publish(package)

    def publish(
        self,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path | None = None,
    ) -> InstalledRecord:
        """Package local inputs and publish them to the configured registry.

        Pass a ``LocalRecord`` with optional ``dependencies`` and ``lockfile``,
        or pass a local ``Graph`` resolved by this catalog. A graph supplies
        its own dependency selection, so leave those arguments unset in that
        case. The returned record identifies the published recipe revision
        and provides paths to the installed files.
        """
        self._client.require_remote()
        installed = self._pack(record, dependencies=dependencies, lockfile=lockfile)
        self._client.upload(str(installed.ref))
        return installed

    def _pack(
        self,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path | None = None,
    ) -> InstalledRecord:
        """Export a package to the local cache and return its exact reference."""
        from ..projects.graph import Graph

        if isinstance(record, Graph):
            if dependencies or lockfile is not None:
                raise InvalidPackage(
                    "A resolved graph already defines its dependencies and lockfile"
                )
            if record.catalog is not self:
                raise InvalidPackage("Pack the graph through its own catalog")
            return record._pack()
        return self._local_package(record, dependencies, lockfile).pack()

    def _upload(self, ref: Ref) -> None:
        """Upload an exact recipe and its locally packed binaries."""
        self._client.require_remote()
        if ref.revision is None:
            raise InvalidPackage("Upload requires an exact recipe revision")
        self._client.cache_path(str(ref))
        self._client.upload(str(ref))

    def _publish_graph(self, graph: Graph, record: LocalRecord) -> InstalledRecord:
        self._client.require_remote()
        if graph.catalog is not self:
            raise InvalidPackage("Publish the graph through its own catalog")
        installed = graph._pack(record)
        self._client.upload(str(installed.ref))
        return installed

    def _package(self, ref: Ref) -> StoredPackage:
        from ..._internal.package import StoredPackage

        return StoredPackage(self, ref)

    def _local_package(
        self,
        record: LocalRecord,
        dependencies: tuple[Dependency, ...],
        lockfile: Path | None,
    ) -> LocalPackage:
        from ..._internal.package import LocalPackage

        return LocalPackage(self, record, dependencies=dependencies, lockfile=lockfile)


__all__ = ["Catalog"]
