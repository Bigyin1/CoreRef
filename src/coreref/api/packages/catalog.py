"""Locate project sources and fetch released artifacts through Nix."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path
from typing import cast

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..._internal.nix_store import NixStore, store_entry
from ..errors import CatalogError, InvalidPackage, NotFound
from .records import JSON, Dependency, Record, Ref


class _Source(BaseModel):
    """The source and optional release index associated with one locked dependency."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str
    origin: dict[str, JsonValue] | None = None
    dependencies: tuple[str, ...] = ()
    payload: JsonValue = None
    outputs: dict[str, str] = Field(default_factory=dict)

    def record(self, ref: Ref, *, file_names: tuple[str, ...] = ()) -> Record:
        data = cast(JSON, self.payload)
        metadata: dict[str, JSON] = dict(data) if isinstance(data, Mapping) else {"data": data}
        if self.outputs:
            metadata["output_index"] = self.outputs
        return Record(ref, metadata, file_names=file_names, has_source=True)


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
        self.editables = {ref.without_revision(): path for ref, path in (editables or {}).items()}
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

    def _select(self, requirement: Dependency) -> Ref:
        if isinstance(requirement, Ref):
            return requirement
        candidates = self._locations.keys() | self.releases.keys()
        matching = [ref for ref in candidates if requirement._matches(ref)]
        if not matching:
            raise NotFound(f"No available source matches {requirement}")
        return max(matching, key=lambda ref: (Version(ref.version), str(ref)))

    def _snapshot(self, ref: Ref) -> tuple[Path, dict[str, JSON] | None]:
        location = self._locations.get(ref.without_revision())
        if isinstance(location, Path):
            name = f"coreref-source-{ref.package_name}"
            return self._nix.source(location, name, tracked=True), None
        if isinstance(location, str):
            return self._nix.prefetch(location)
        raise NotFound(f"No source configured for {ref}")

    def _index(self, ref: Ref) -> Path | None:
        return self.releases.get(ref) or self.releases.get(ref.without_revision())

    def _published(self, ref: Ref) -> tuple[Ref, _Source] | None:
        index = self._index(ref)
        if index is None:
            return None
        self._nix.restore((index,))
        document = json.loads(index.read_bytes())
        published = Ref(document["ref"])
        if ref.without_revision() != published.without_revision() or (
            ref.revision is not None and ref != published
        ):
            raise InvalidPackage(f"{index} identifies {published}, expected {ref}")
        return published, _Source.model_validate(document["source"])

    def info(self, ref: Ref) -> Record:
        """Read a registered release index without downloading its artifacts."""
        result = self._published(ref)
        if result is None:
            raise NotFound(f"No release index registered for {ref}")
        published, source = result
        return source.record(published)

    def _is_editable(self, ref: Ref) -> bool:
        return ref.without_revision() in self.editables

    def _source_for(self, ref: Ref, source: _Source) -> Path:
        editable = self.editables.get(ref.without_revision())
        if editable is not None:
            return editable.resolve(strict=True)
        locked = store_entry(Path(source.source))
        if locked.is_dir():
            return locked
        try:
            self._nix.restore((locked,))
        except CatalogError:
            restored = (
                self._nix.fetch(cast(Mapping[str, JSON], source.origin))
                if source.origin is not None
                else self._snapshot(ref)[0]
            )
            if restored != locked:
                message = f"Source for {ref} changed. Restore {locked} or run lock."
                raise InvalidPackage(message) from None
        return locked

    def _lock_entry(self, requirement: Dependency) -> tuple[Ref, _Source]:
        from ..projects.manifest import Manifest

        ref = self._select(requirement)
        published = self._published(ref)
        if published is not None:
            return published
        path, origin = self._snapshot(ref)
        manifest = Manifest.load(path / "manifest.yml")
        if Ref(manifest.ref) != ref.without_revision():
            raise InvalidPackage(f"{path} declares {manifest.ref}, expected {ref}")
        revision = path.name.partition("-")[0]
        locked = Ref(f"{ref.without_revision()}#{revision}")
        if ref.revision is not None and ref != locked:
            raise InvalidPackage(f"Source {ref} does not match {locked}")
        return locked, _Source(
            source=str(path),
            origin=cast("dict[str, JsonValue] | None", origin),
            dependencies=tuple(map(str, manifest.requirements)),
            payload={"type": manifest.type, "data": manifest.payload},
        )
