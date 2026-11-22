"""Resolve independent configurations of a Conan component for one project."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from shutil import copyfile
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from ..api.errors import InvalidPackage
from ..api.packages.records import ConfiguredPackage, Dependency, LocalRecord, PackageInput, Ref
from .recipe import _RECIPE_ENV
from .references import from_conan_reference
from .types import NativeGraph

if TYPE_CHECKING:
    from ..api.packages.catalog import Catalog


_OptionValue = str | int | bool
_GENERATED_TEMPLATE = _RECIPE_ENV.get_template("generated.py.j2")


class Variants:
    """Materialize independently configured inputs with a shared project lockfile."""

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._client = catalog._client

    def publish_recipe(self, directory: Path) -> Ref:
        """Publish a flow-authored Conan recipe with build logic and declared options."""
        remote = self._client.require_remote()
        ref = from_conan_reference(self._client.export(directory.absolute()))
        self._client.run("upload", str(ref), "--only-recipe", "--remote", remote, "--confirm")
        return ref

    def publish_generated_recipe(
        self,
        ref: Ref,
        *,
        options: Mapping[str, tuple[_OptionValue, ...]],
        inputs: Mapping[str, Path],
        runner: Path,
    ) -> Ref:
        """Publish a recipe that calls generate(options, source, output) from runner.

        The first value in each option domain is its default. The generator receives
        Conan option strings and paths to its named inputs and the binary directory.
        """
        if ref.revision is not None:
            raise InvalidPackage("A generated recipe requires a reference without revision")
        domains = {name: list(values) for name, values in sorted(options.items())}
        if any(not values for values in domains.values()):
            raise InvalidPackage("Each recipe option needs at least one allowed value")
        recipe = _GENERATED_TEMPLATE.render(
            name=ref.package_name,
            version=ref.version,
            options=domains,
            defaults={name: values[0] for name, values in domains.items()},
        )
        with TemporaryDirectory(prefix="coreref-generated-") as temporary:
            directory = Path(temporary)
            (directory / "conanfile.py").write_text(recipe, encoding="utf-8")
            copyfile(runner, directory / "generator.py")
            _stage_recipe_inputs(directory / "inputs", inputs)
            return self.publish_recipe(directory)

    def publish(self, package: ConfiguredPackage) -> None:
        """Upload the exact installed binary revision to the configured registry."""
        self._client.upload(package.binary_reference)

    def create_lockfile(
        self,
        record: LocalRecord,
        *,
        requirements: tuple[Dependency, ...],
        inputs: Iterable[PackageInput],
        path: Path,
    ) -> Path:
        """Resolve plain and configured requirements afresh, then replace the project lock."""
        path = path.expanduser().absolute()
        path.parent.mkdir(parents=True, exist_ok=True)
        from ..api.projects.graph import Graph

        graph = Graph(self._catalog, record, dependencies=requirements)
        with TemporaryDirectory(prefix=".coreref-lock-", dir=path.parent) as temporary:
            candidate = graph.create_lockfile(Path(temporary) / "conan.lock")
            self._lock_inputs(inputs, candidate)
            return candidate.replace(path)

    def _lock_inputs(self, inputs: Iterable[PackageInput], path: Path) -> None:
        for package in inputs:
            self._client.run(
                "lock",
                "create",
                *package._arguments(),
                "--lockfile",
                str(path) if path.is_file() else "",
                "--lockfile-out",
                str(path),
                *self._client.source_args,
            )

    def get_many(
        self,
        inputs: Mapping[str, PackageInput],
        *,
        lockfile: Path,
        build_missing: bool = False,
    ) -> dict[str, ConfiguredPackage]:
        """Install each named configuration independently, using the same project lock."""
        if inputs and not lockfile.is_file():
            raise FileNotFoundError(
                f"Create the project lockfile before installing variants: {lockfile}"
            )
        return {
            name: self._catalog.get_variant(package, lockfile=lockfile, build_missing=build_missing)
            for name, package in inputs.items()
        }

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


def _stage_recipe_inputs(directory: Path, inputs: Mapping[str, Path]) -> None:
    for name, source in sorted(inputs.items()):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise InvalidPackage(f"Invalid recipe input path: {name!r}")
        if relative.as_posix() != name or "\\" in name or ":" in name:
            raise InvalidPackage(f"Invalid recipe input path: {name!r}")
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, destination)


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
