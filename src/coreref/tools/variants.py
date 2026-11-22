"""Resolve independent configurations of a Conan component for one project."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from .._internal.references import from_conan_reference
from .._internal.types import NativeGraph
from ..api.errors import InvalidPackage
from ..api.records import Dependency, Ref

if TYPE_CHECKING:
    from ..api.catalog import Catalog


@dataclass(frozen=True)
class PackageInput:
    """A recipe requirement and options for one named input of the flow."""

    requirement: Dependency
    options: Mapping[str, str | int | bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    def _arguments(self) -> tuple[str, ...]:
        options = (
            argument
            for name, value in sorted(self.options.items())
            for argument in ("--options:host", f"{self.requirement.package_name}/*:{name}={value}")
        )
        return ("--requires", str(self.requirement), *options)


@dataclass(frozen=True)
class ConfiguredPackage:
    """The selected Conan recipe and binary, with the installed package directory."""

    ref: Ref
    package_id: str
    package_revision: str
    directory: Path
    metadata: Mapping[str, object]

    @property
    def binary_reference(self) -> str:
        """Return the exact Conan address of the installed binary."""
        return f"{self.ref}:{self.package_id}#{self.package_revision}"


class Variants:
    """Materialize independently configured inputs with a shared project lockfile."""

    def __init__(self, catalog: Catalog) -> None:
        self._client = catalog._client

    def publish_recipe(self, directory: Path) -> Ref:
        """Publish a flow-authored Conan recipe with build logic and declared options."""
        remote = self._client.require_remote()
        ref = from_conan_reference(self._client.export(directory.absolute()))
        self._client.run("upload", str(ref), "--only-recipe", "--remote", remote, "--confirm")
        return ref

    def publish(self, package: ConfiguredPackage) -> None:
        """Upload the exact installed binary revision to the configured registry."""
        self._client.upload(package.binary_reference)

    def extend_lockfile(self, inputs: Iterable[PackageInput], path: Path) -> Path:
        """Add configurable inputs to a project lock using Conan's lock commands."""
        path = path.absolute()
        with TemporaryDirectory(prefix=".coreref-lock-", dir=path.parent) as temporary:
            candidate = Path(temporary) / "conan.lock"
            if path.is_file():
                candidate.write_bytes(path.read_bytes())
            for package in inputs:
                self._client.run(
                    "lock",
                    "create",
                    *package._arguments(),
                    "--lockfile",
                    str(candidate) if candidate.is_file() else "",
                    "--lockfile-out",
                    str(candidate),
                    *self._client.source_args,
                )
            if not candidate.is_file():
                raise InvalidPackage("A project lockfile requires at least one package input")
            candidate.replace(path)
        return path

    def get(
        self,
        package: PackageInput,
        *,
        lockfile: Path | None,
        build_missing: bool = False,
    ) -> ConfiguredPackage:
        """Install one variant, optionally building it when the binary is unavailable."""
        return self._get(package, lockfile=lockfile, build_missing=build_missing)

    def _get(
        self,
        package: PackageInput,
        *,
        lockfile: Path | None,
        build_missing: bool,
        build_prerequisites: tuple[Ref, ...] = (),
    ) -> ConfiguredPackage:
        """Install one variant and optionally build the root and specific prerequisites.

        Pass None as the lockfile for a graph whose recipes pin their own dependencies.
        """
        policy = _build_policy(package.requirement, build_missing, build_prerequisites)
        lock = str(lockfile.absolute()) if lockfile is not None else ""
        with TemporaryDirectory(prefix="coreref-variant-") as temporary:
            result = cast(
                Mapping[str, object],
                self._client.run(
                    "install",
                    *package._arguments(),
                    "--lockfile",
                    lock,
                    *policy,
                    "--output-folder",
                    temporary,
                    "--envs-generation=false",
                    *self._client.source_args,
                ),
            )
        graph = cast(NativeGraph, result["graph"])
        return _selected_package(graph, package.requirement.package_name)


def _build_policy(
    requirement: Dependency, build_missing: bool, prerequisites: tuple[Ref, ...]
) -> tuple[str, ...]:
    if any(ref.revision is None for ref in prerequisites):
        raise InvalidPackage("Build prerequisites require exact recipe revisions")
    if not build_missing:
        return ("--build=never",)
    root = (
        str(requirement)
        if isinstance(requirement, Ref) and requirement.revision is not None
        else f"{requirement.package_name}/*"
    )
    return (f"--build=missing:{root}", *(f"--build=missing:{ref}" for ref in prerequisites))


def _selected_package(graph: NativeGraph, name: str) -> ConfiguredPackage:
    for node in graph.nodes:
        ref = node.ref
        recipe = node.conanfile
        if ref is None or ref.name != name or recipe.context != "host":
            continue
        folder = recipe.package_folder
        package_id, revision = node.package_id, node.prev
        if folder and package_id and revision:
            return ConfiguredPackage(
                from_conan_reference(ref.repr_notime()),
                package_id,
                revision,
                Path(folder),
                MappingProxyType(dict(recipe.conan_data or {})),
            )
    raise InvalidPackage(f"Conan did not install a binary for {name}")
