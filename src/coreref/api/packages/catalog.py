"""Locate project sources and fetch released artifacts through Nix."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, cast

from frozendict import deepfreeze, frozendict
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..._internal.nix_store import NixStore, store_entry
from ..._internal.references import parse_package_name, version_range
from ..errors import CatalogError, InvalidPackage, NotFound
from .records import JSON, Record, Ref

if TYPE_CHECKING:
    from ..projects.manifest import Manifest

_SOURCE_NAME = "coreref-source"


class _Source(BaseModel):
    """The source and optional release index associated with one locked dependency."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    source: Path
    origin: dict[str, JsonValue] | None = None
    dependencies: tuple[str, ...] = ()
    kind: str | None = None
    payload: JsonValue = None
    outputs: dict[str, Path] = Field(default_factory=dict)
    commit: str | None = None

    def record(self, ref: Ref) -> Record:
        return Record(
            ref,
            self.source,
            kind=self.kind,
            outputs=frozendict(self.outputs),
            payload=deepfreeze(cast(JSON, self.payload)),
            commit=self.commit,
        )


def _matching_version(text: str, constraint: SpecifierSet) -> Version | None:
    try:
        version = Version(text)
    except InvalidVersion:
        return None
    return version if constraint.contains(version) else None


class Catalog:
    """Bind project coordinates to sources and an optional Nix binary cache.

    Plain project directories can be discovered below ``directory``.
    ``sources`` overrides individual versions with paths or Nix flake
    references. ``releases`` can point at immutable release indexes returned
    by publish. Locking records exact store paths, so later builds use the
    same source trees and artifacts.
    """

    def __init__(  # noqa: PLR0913 - independent source and Nix configuration.
        self,
        directory: Path | None = None,
        *,
        sources: Mapping[Ref, Path | str] | None = None,
        cache: str | None = None,
        python: Path | None = None,
        host_launcher: tuple[str, ...] = (),
        releases: Mapping[Ref, Path] | None = None,
        editables: Mapping[Ref, Path] | None = None,
    ) -> None:
        """Configure source lookup and stage execution.

        ``python`` optionally selects a Nix-provided Python environment with
        the flow's extra libraries. ``host_launcher`` is an optional argv
        prefix for host stages, for example a script that loads a tool
        module and then executes its arguments.
        """
        self.directory = directory
        self.sources = dict(sources or {})
        self.releases = dict(releases or {})
        self.editables = dict(editables or {})
        self._nix = NixStore(cache, python, host_launcher)

    @cached_property
    def _locations(self) -> dict[Ref, Path | str]:
        from ..projects.manifest import Manifest

        locations: dict[Ref, Path | str] = {}
        if self.directory is not None:
            for manifest in sorted(self.directory.glob("*/manifest.yml")):
                ref = Ref(Manifest.load(manifest).ref)
                if ref in locations:
                    raise InvalidPackage(f"Multiple project directories declare {ref}")
                locations[ref] = manifest.parent
        locations.update(self.sources)
        return locations

    def _select(self, requirement: str) -> Ref:
        name, _, selection = requirement.partition("/")
        if not selection.startswith("["):
            return Ref(requirement)
        parse_package_name(name)
        constraint = version_range(selection)
        candidates = self._locations.keys() | self.releases.keys()
        matching = {
            ref: version
            for ref in candidates
            if ref.package_name == name
            and (version := _matching_version(ref.version, constraint)) is not None
        }
        if not matching:
            raise NotFound(f"No available source matches {requirement}")
        return max(matching, key=lambda ref: (matching[ref], str(ref)))

    def _import_source(
        self, ref: Ref, *, name: str = _SOURCE_NAME
    ) -> tuple[Path, dict[str, JSON] | None]:
        location = self._locations.get(ref)
        if isinstance(location, Path):
            return self._nix.source(location, name, tracked=True), None
        if isinstance(location, str):
            return self._nix.prefetch(location)
        raise NotFound(f"No source configured for {ref}")

    def _published(self, ref: Ref) -> _Source | None:
        index = self.releases.get(ref)
        if index is None:
            return None
        self._nix.restore((index,))
        document = json.loads(index.read_bytes())
        published = Ref(document["ref"])
        if ref != published:
            raise InvalidPackage(f"{index} identifies {published}, expected {ref}")
        return _Source.model_validate(document["source"])

    def info(self, ref: Ref) -> Record:
        """Read a registered release index without downloading its artifacts."""
        result = self._published(ref)
        if result is None:
            raise NotFound(f"No release index registered for {ref}")
        return result.record(ref)

    def get_outputs(self, record: Record, names: tuple[str, ...]) -> dict[str, Path]:
        """Retrieve exactly the named files from the selected release."""
        if missing := set(names) - record.outputs.keys():
            raise NotFound(f"{record.ref} has no outputs {', '.join(sorted(missing))}")
        selected = {name: record.outputs[name] for name in names}
        self._nix.restore(tuple(selected.values()))
        if absent := [name for name, path in selected.items() if not path.is_file()]:
            raise NotFound(f"Missing files for {record.ref}: {', '.join(absent)}")
        return selected

    def _source_for(self, ref: Ref, source: _Source) -> Path:
        editable = self.editables.get(ref)
        if editable is not None:
            return editable.resolve(strict=True)
        locked = store_entry(source.source)
        if locked.is_dir():
            return locked
        try:
            self._nix.restore((locked,))
        except CatalogError:
            # The recorded name is part of the store path even when the contents match.
            restored = (
                self._nix.fetch(cast(Mapping[str, JSON], source.origin))
                if source.origin is not None
                else self._import_source(ref, name=locked.name.split("-", 1)[1])[0]
            )
            if restored != locked:
                message = f"Source for {ref} changed. Restore {locked} or run lock."
                raise InvalidPackage(message) from None
        return locked

    def _lock_entry(self, ref: Ref) -> _Source:
        from ..projects.manifest import Manifest

        published = self._published(ref)
        if published is not None:
            return published
        path, origin = self._import_source(ref)
        manifest = Manifest.load(path / "manifest.yml")
        if Ref(manifest.ref) != ref:
            raise InvalidPackage(f"{path} declares {manifest.ref}, expected {ref}")
        return _Source(
            source=path,
            origin=cast("dict[str, JsonValue] | None", origin),
            dependencies=manifest.requirements,
            kind=manifest.type,
            payload=manifest.payload,
        )

    def _publish(
        self, checkout: Path, manifest: Manifest, files: Mapping[str, Path], *, commit: str
    ) -> Path:
        ref = Ref(manifest.ref)
        source = self._nix.source(checkout, _SOURCE_NAME, tracked=True)
        outputs = {
            name: self._store_file(path.resolve(strict=True)) for name, path in files.items()
        }
        metadata = _Source(
            source=source,
            dependencies=manifest.requirements,
            kind=manifest.type,
            payload=manifest.payload,
            outputs=outputs,
            commit=commit,
        )
        document = {
            "ref": str(ref),
            "source": metadata.model_dump(mode="json", exclude_defaults=True),
        }
        index_path = self._nix.add_text(
            "coreref-release.json", json.dumps(document, sort_keys=True)
        )
        self._nix.upload((source, *outputs.values()))
        self._nix.upload((index_path,))
        return index_path

    def _store_file(self, path: Path) -> Path:
        try:
            store_entry(path)
        except InvalidPackage:
            return self._nix.add(path, "coreref-artifact")
        return path
