"""Publish project outputs and retrieve only the named files a consumer needs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from ..api.catalog import Catalog
from ..api.errors import InvalidPackage, NotFound
from ..api.graph import Graph
from ..api.query import SearchQuery
from ..api.records import JSON, InstalledRecord, LocalRecord, Record, Ref
from .artifacts import ArtifactStore, OutputIndex, SliceInputs
from .variants import PackageInput, Variants

PRODUCER_NAMESPACE = "coreref-producer"


class Producer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    prerequisites: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_refs(self) -> Producer:
        for value in (self.ref, *self.prerequisites):
            ref = Ref(value)
            if ref.namespace != PRODUCER_NAMESPACE or ref.revision is None:
                raise ValueError(f"Expected an exact producer reference, got {value}")
        return self

    def document(self) -> JSON:
        return {"ref": self.ref, "prerequisites": self.prerequisites}


def read_producers(record: Record) -> dict[str, Producer]:
    return _parse_producers(record.payload)


def _parse_producers(payload: JSON) -> dict[str, Producer]:
    if not isinstance(payload, Mapping) or "producers" not in payload:
        return {}
    return TypeAdapter(dict[str, Producer]).validate_json(json.dumps(payload["producers"]))


def _merge_producers(
    older: Mapping[str, Producer], newer: Mapping[str, Producer]
) -> dict[str, Producer]:
    if any(older[group] != newer[group] for group in older.keys() & newer.keys()):
        raise ValueError("Producer recipes changed for the same project slice")
    return dict(older) | dict(newer)


class Outputs:
    """Store named files under the resolved project and recover missing outputs on demand."""

    def __init__(self, catalog: Catalog, *, build_missing: bool = False) -> None:
        self._catalog = catalog
        self._store = ArtifactStore(catalog)
        self._variants = Variants(catalog)
        self._build_missing = build_missing

    def pack(
        self, inputs: SliceInputs, files: Mapping[str, Path], payload: Mapping[str, JSON]
    ) -> InstalledRecord:
        """Pack files and an index into the local cache."""
        index = self._store.pack_index(inputs.key(), files)
        record = LocalRecord(
            Ref(f"coreref-snapshot.index/{index.key}"),
            {**payload, "output_index": index.document()},
        )
        return self._catalog._pack(record)

    def publish(
        self,
        graph: Graph,
        inputs: SliceInputs,
        files: Mapping[str, Path],
        payload: Mapping[str, JSON],
    ) -> InstalledRecord:
        """Publish files before exposing the index, extending previous outputs of that slice."""
        if graph.catalog is not self._catalog or not isinstance(graph.record, LocalRecord):
            raise InvalidPackage("Publishing outputs requires a local graph for this catalog")
        if graph.record.ref != inputs.project or inputs.commit is None:
            raise InvalidPackage("Publishing outputs requires a matching, committed project")
        self._catalog._client.require_remote()
        current = self._store.pack_index(inputs.key(), files)
        previous, producers = self._previous(inputs.project, current.key)
        index = current.extend(previous) if previous is not None else current
        producers = _merge_producers(producers, _parse_producers(payload))
        metadata: dict[str, JSON] = {**payload, "output_index": index.document()}
        if producers:
            metadata["producers"] = {
                name: producer.document() for name, producer in producers.items()
            }
        record = replace(graph.record, payload=metadata, files={})
        self._store.upload(current.outputs)
        return self._catalog._publish_graph(graph, record)

    def _previous(self, root: Ref, key: str) -> tuple[OutputIndex | None, dict[str, Producer]]:
        try:
            latest = self._catalog.info(root)
        except NotFound:
            return None, {}
        index = OutputIndex.from_record(latest)
        if index is not None and index.key == key:
            return index, read_producers(latest)
        query = SearchQuery(
            namespace=root.namespace, component=root.component, version=root.version
        )
        merged = None
        producers: dict[str, Producer] = {}
        for ref in self._catalog.search_refs(query, all_revisions=True):
            if ref == latest.ref:
                continue
            record = self._catalog.info(ref)
            previous = OutputIndex.from_record(record)
            if previous is not None and previous.key == key:
                merged = previous if merged is None else merged.extend(previous)
                producers = _merge_producers(producers, read_producers(record))
        return merged, producers

    def get(
        self,
        record: Record,
        names: tuple[str, ...],
        *,
        groups: Mapping[str, tuple[str, ...]] | None = None,
    ) -> dict[str, Path]:
        """Retrieve selected files from this revision, optionally using declared producers."""
        if not names:
            return {}
        index = OutputIndex.from_record(record)
        if index is None:
            files = self._catalog.get(record.ref).files
            return {name: files[name] for name in names}
        ready = {
            name: self._store.get(index.outputs[name]) for name in names if name in index.outputs
        }
        missing = set(names) - ready.keys()
        if missing:
            ready.update(self._resolve(record, index, missing, groups or {}))
        return {name: ready[name] for name in names}

    def _resolve(
        self,
        record: Record,
        index: OutputIndex,
        missing: set[str],
        groups: Mapping[str, tuple[str, ...]],
    ) -> dict[str, Path]:
        declared = read_producers(record)
        selected = {
            group: missing.intersection(names)
            for group, names in groups.items()
            if group in declared and missing.intersection(names)
        }
        unavailable = missing - set().union(*selected.values())
        if unavailable:
            raise ValueError(f"{record.ref} is missing outputs: {', '.join(sorted(unavailable))}")
        files: dict[str, Path] = {}
        for group, names in selected.items():
            files.update(self._get_group(declared[group], index, names, groups[group]))
        return files

    def _get_group(
        self, producer: Producer, index: OutputIndex, names: set[str], group: tuple[str, ...]
    ) -> dict[str, Path]:
        ref = Ref(producer.ref)
        try:
            package = self._variants.get(
                PackageInput(ref, {}),
                lockfile=None,
                build_missing=self._build_missing,
                build_prerequisites=tuple(Ref(value) for value in producer.prerequisites),
            )
        except NotFound as error:
            if self._build_missing:
                raise
            raise NotFound(
                f"Producer binary unavailable for {ref}. Enable builds of missing outputs."
            ) from error
        if package.ref != ref:
            raise ValueError(f"Conan resolved a different producer than {ref}")
        for name in index.outputs.keys() & set(group):
            index.outputs[name].verify(package.directory / name)
        files = {name: package.directory / name for name in names}
        if any(not path.is_file() for path in files.values()):
            raise ValueError(f"{ref} did not produce the requested outputs")
        return files
