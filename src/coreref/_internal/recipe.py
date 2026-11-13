"""Prepared local recipes and validated recipes obtained from Conan."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from jinja2 import Environment, PackageLoader, StrictUndefined

from ..api.errors import CatalogError, InvalidPackage
from ..api.records import InstalledRecord, LocalRecord, Record, Ref
from .references import from_conan_reference, to_conan_reference
from .types import NativePackage

_RECIPE_ENV = Environment(
    loader=PackageLoader("coreref._internal"),
    autoescape=False,  # noqa: S701 - Python literals use repr escaping.
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_RECIPE_ENV.filters["pyrepr"] = repr
_RECIPE_TEMPLATE = _RECIPE_ENV.get_template("conanfile.py.j2")
_CONANDATA_TEMPLATE = _RECIPE_ENV.get_template("conandata.yml.j2")


def _recipe_text(ref: Ref) -> str:
    name, version = to_conan_reference(ref).split("/")
    return _RECIPE_TEMPLATE.render(name=name, version=version)


class ConsumerRecipe:
    """An anonymous consumer for resolving or fetching published references."""

    def __init__(self, requirements: tuple[str, ...]) -> None:
        self.requirements = tuple(sorted(set(requirements)))

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        template = _RECIPE_ENV.get_template("consumer.py.j2")
        (directory / "conanfile.py").write_text(template.render(requirements=self.requirements))


class LocalRecipe:
    """A local package description ready to be written into a staging directory."""

    def __init__(self, record: LocalRecord, requirements: tuple[str, ...] = ()) -> None:
        self.record = record
        self.requirements = tuple(sorted(set(requirements)))

    def write_for_resolution(self, directory: Path) -> None:
        """Resolve requirements without serializing unfinished local payload or artifacts."""
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "conanfile.py").write_text(_recipe_text(self.record.ref.without_revision()))
        (directory / "conandata.yml").write_text(
            _CONANDATA_TEMPLATE.render(requirements=self.requirements)
        )

    def write(self, directory: Path) -> None:
        self.write_for_resolution(directory)
        (directory / "record.json").write_text(self.record.to_json())
        if self.record.source is not None:
            (directory / "source-path.txt").write_text(str(self.record.source))

    def write_editable(self, directory: Path, *, manifest: str | None = None) -> None:
        """Write only a Conan recipe; all editable state belongs to Conan."""
        path = directory / "conanfile.py"
        if path.exists():
            return
        data = {
            "record": json.loads(self.record.to_json()),
            "requires": list(self.requirements),
            "source": os.path.relpath(self.record.source or directory, directory),
            "files": {
                name: os.path.relpath(file, directory) for name, file in self.record.files.items()
            },
        }
        template = _RECIPE_ENV.get_template("editable.py.j2")
        path.write_text(
            template.render(
                name=self.record.ref.package_name,
                version=self.record.ref.version,
                data=data,
                manifest=manifest,
                format=LocalRecord._format,
            )
        )

    def copy_files(self, directory: Path) -> None:
        """Stage publication files and record their SHA-256 without exporting the bytes."""
        digests = {}
        for name, path in self.record.files.items():
            destination = directory / "artifacts" / name
            if not destination.resolve().is_relative_to((directory / "artifacts").resolve()):
                raise InvalidPackage("Artifact would escape the package directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            digest = hashlib.sha256()
            with destination.open("rb") as artifact:
                for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                    digest.update(chunk)
            digests[name] = digest.hexdigest()
        if digests:
            metadata = directory / "record.json"
            value = json.loads(metadata.read_text())
            value["file_digests"] = digests
            metadata.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


class StoredRecipe:
    """Read catalog metadata from a trusted Conan recipe revision."""

    def __init__(
        self,
        directory: Path,
        *,
        reference: str,
        native: NativePackage | None = None,
    ) -> None:
        self.reference = reference
        expected = from_conan_reference(reference)
        self.directory = directory
        try:
            self.is_editable = native is not None
            data = (native.conan_data or {}) if native is not None else self._read_data()
            self.data = data
            self.requirements = tuple(cast(Iterable[str], data.get("requires", ())))
            self.record = self._read_record(
                expected, has_source=bool(data.get("scm")) or self.is_editable
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            InvalidPackage,
            yaml.YAMLError,
        ) as exc:
            raise InvalidPackage(f"Invalid catalog recipe for {expected}: {exc}") from exc

    @property
    def has_source(self) -> bool:
        return self.record.has_source

    def _read_record(self, expected: Ref, *, has_source: bool) -> Record:
        value = (
            self.data.get("record")
            if self.is_editable
            else json.loads((self.directory / "record.json").read_text())
        )
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        if value.get("format") != LocalRecord._format:
            raise ValueError("Unsupported record format")
        if any(name in value for name in ("source", "source_path", "scm")):
            raise ValueError("Git source must be stored in Conan SCM metadata")
        coordinates = value["ref"]
        ref = Ref(coordinates) if isinstance(coordinates, str) else Ref.from_fields(**coordinates)
        if ref != expected.without_revision():
            raise ValueError("Record coordinates disagree with the recipe")
        return Record(
            expected,
            value["payload"],
            file_names=self._read_file_names(value.get("files", [])),
            has_source=has_source,
        )

    def _read_file_names(self, names: object) -> tuple[str, ...]:
        if not isinstance(names, list):
            raise ValueError("Package files must be a list of stored names")
        folder = self.directory / "artifacts"
        files = {name: folder / name for name in names}
        if any(not path.resolve().is_relative_to(folder.resolve()) for path in files.values()):
            raise ValueError("Artifact would escape the package directory")
        return tuple(files)

    def installed(self, package: NativePackage, *, with_sources: bool) -> InstalledRecord:
        """Read paths from the native package, keeping editable paths outside the cache."""
        if self.is_editable:
            if package.recipe_folder is None:
                raise CatalogError("Working package directory is unavailable")
            directory = Path(package.recipe_folder)
            working_source = (
                Path(package.folders.source_folder)
                if package.folders.source_folder
                else directory / (package.folders.source or ".")
            ).resolve()
            paths = cast(Mapping[str, str], self.data.get("files", {}))
            return InstalledRecord(
                self.record,
                source=working_source,
                is_editable=True,
                files={
                    name: (
                        directory / Path(paths.get(name, f"artifacts/{name}")).expanduser()
                    ).resolve()
                    for name in self.record.file_names
                },
            )
        files = self._installed_files(package)
        source = None
        if with_sources:
            if package.folders.source_folder is None:
                raise CatalogError("Package source directory is unavailable")
            source = Path(package.folders.source_folder) / "source"
        return InstalledRecord(self.record, source=source, files=files)

    def _installed_files(self, package: NativePackage) -> dict[str, Path]:
        if not self.record.file_names:
            return {}
        if package.package_folder is None:
            raise CatalogError("Package files are unavailable")
        folder = Path(package.package_folder) / "artifacts"
        files = {name: folder / name for name in self.record.file_names}
        if any(not path.is_file() for path in files.values()):
            raise InvalidPackage("Installed package is missing a declared file")
        return files

    def _read_data(self) -> Mapping[str, object]:
        data = yaml.safe_load((self.directory / "conandata.yml").read_text())
        if not isinstance(data, Mapping):
            raise ValueError("Conan data must be a YAML mapping")
        return cast(Mapping[str, object], data)
