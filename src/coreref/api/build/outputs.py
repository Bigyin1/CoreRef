"""Resolve named build results from published project revisions.

A published project can point to individual artifacts through an
output index. Consumers can fetch the required files without installing
unrelated outputs. The catalog also supports packages with files
stored directly in their binary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ..._internal.artifacts import ArtifactStore, OutputIndex, Producer
from ..errors import InvalidPackage, NotFound
from ..packages.catalog import Catalog
from ..packages.query import SearchQuery
from ..packages.records import JSON, InstalledRecord, LocalRecord, PackageInput, Record, Ref

if TYPE_CHECKING:
    from ..._internal.snapshot import SliceInputs
    from ..projects.graph import Graph


def _merge_producers(
    older: Mapping[str, Producer], newer: Mapping[str, Producer]
) -> dict[str, Producer]:
    if any(older[group] != newer[group] for group in older.keys() & newer.keys()):
        raise ValueError("Producer recipes changed for the same project slice")
    return dict(older) | dict(newer)


def _metadata(
    index: OutputIndex, payload: Mapping[str, JSON], producers: Mapping[str, Producer]
) -> dict[str, JSON]:
    if payload.keys() & {"output_index", "producers"}:
        raise InvalidPackage("Output metadata is managed by Outputs; use the producers argument")
    result: dict[str, JSON] = {**payload, "output_index": index.document()}
    if producers:
        result["producers"] = {name: producer._document() for name, producer in producers.items()}
    return result


class Outputs:
    """Find and retrieve artifacts for the revision described by a record.

    Metadata inspection uses the supplied record. Retrieval downloads
    the selected artifacts and can install or rebuild configured output
    groups when a published recipe provides them.
    """

    def __init__(
        self,
        catalog: Catalog,
        *,
        groups: Mapping[str, tuple[str, ...]] | None = None,
        build_missing: bool = False,
    ) -> None:
        """Bind artifact operations to a catalog.

        ``groups`` maps recipe group names to the filenames they produce.
        Set ``build_missing=True`` to let Conan build a missing binary for a
        requested group.
        """
        self._catalog = catalog
        self._store = ArtifactStore(catalog)
        self._variants = catalog._variants
        self._build_missing = build_missing
        self._groups = dict(groups or {})

    def _pack(
        self,
        inputs: SliceInputs,
        files: Mapping[str, Path],
        payload: Mapping[str, JSON],
        *,
        producers: Mapping[str, Producer] | None = None,
    ) -> InstalledRecord:
        """Pack files and an index into the local cache."""
        index = self._store.pack_index(inputs.key(), files)
        record = LocalRecord(
            Ref(f"coreref-snapshot.index/{index.key}"),
            _metadata(index, payload, producers or {}),
        )
        return self._catalog._pack(record)

    def _publish(
        self,
        graph: Graph,
        inputs: SliceInputs,
        files: Mapping[str, Path],
        payload: Mapping[str, JSON],
        *,
        producers: Mapping[str, Producer] | None = None,
    ) -> InstalledRecord:
        """Publish files before exposing the index, extending previous outputs of that slice."""
        if graph.catalog is not self._catalog or not isinstance(graph.record, LocalRecord):
            raise InvalidPackage("Publishing outputs requires a local graph for this catalog")
        if graph.record.ref != inputs.project or inputs.commit is None:
            raise InvalidPackage("Publishing outputs requires a matching, committed project")
        self._catalog._client.require_remote()
        current = self._store.pack_index(inputs.key(), files)
        previous, registered = self._previous(inputs.project, current.key)
        index = current.extend(previous) if previous is not None else current
        merged = _merge_producers(registered, producers or {})
        record = replace(graph.record, payload=_metadata(index, payload, merged), files={})
        self._store.upload(current.outputs)
        return self._catalog._publish_graph(graph, record)

    def _previous(self, root: Ref, key: str) -> tuple[OutputIndex | None, dict[str, Producer]]:
        try:
            latest = self._catalog.info(root)
        except NotFound:
            return None, {}
        index = OutputIndex.from_record(latest)
        if index is not None and index.key == key:
            return index, self._producers(latest)
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
                producers = _merge_producers(producers, self._producers(record))
        return merged, producers

    @staticmethod
    def _producers(record: Record) -> dict[str, Producer]:
        """Read the registered producers of an exact project revision."""
        payload = record.payload
        if not isinstance(payload, Mapping) or "producers" not in payload:
            return {}
        return TypeAdapter(dict[str, Producer]).validate_json(json.dumps(payload["producers"]))

    def _producer_inputs(
        self, external: Mapping[str, tuple[Record, str]]
    ) -> dict[str, tuple[Producer, str]]:
        bindings = {}
        for target, (record, name) in external.items():
            available = self._producers(record)
            for group, files in self._groups.items():
                if name in files and group in available:
                    bindings[target] = available[group], name
                    break
        return bindings

    @staticmethod
    def links(record: Record) -> dict[str, Ref] | None:
        """Read exact references for the ready outputs without fetching them.

        Return a mapping from output names to artifact references. ``None``
        indicates that the package has no output index, while an empty mapping
        represents an index with no ready outputs.
        """
        index = OutputIndex.from_record(record)
        if index is None:
            return None
        return {name: Ref(artifact.ref) for name, artifact in index.outputs.items()}

    def available(self, record: Record, *, producer_only: bool = False) -> set[str]:
        """List discoverable output names without installing packages.

        The default includes published files, plus declared recipe outputs
        when ``build_missing`` is enabled. For an ordinary package it uses
        the file names in the recipe metadata. Set ``producer_only=True``
        to check only what registered recipes promise, regardless of the
        build policy.
        """
        available: set[str] = set()
        if not producer_only:
            links = self.links(record)
            if links is None:
                return set(record.file_names)
            available.update(links)
            if not self._build_missing:
                return available
        for group in self._producers(record):
            available.update(self._groups.get(group, ()))
        return available

    def get(self, record: Record, names: tuple[str, ...]) -> dict[str, Path]:
        """Return local paths for the requested output names.

        Download only those indexed files that are requested. Missing indexed
        files can come from a registered recipe group. For an ordinary package,
        install the package binary and select the requested files from it.
        An empty request returns an empty mapping.
        """
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
            ready.update(self._resolve(record, index, missing))
        return {name: ready[name] for name in names}

    def _resolve(
        self,
        record: Record,
        index: OutputIndex,
        missing: set[str],
    ) -> dict[str, Path]:
        declared = self._producers(record)
        selected = {
            group: missing.intersection(names)
            for group, names in self._groups.items()
            if group in declared and missing.intersection(names)
        }
        unavailable = missing - set().union(*selected.values())
        if unavailable:
            raise ValueError(f"{record.ref} is missing outputs: {', '.join(sorted(unavailable))}")
        files: dict[str, Path] = {}
        for group, names in selected.items():
            files.update(self._get_group(declared[group], index, names, self._groups[group]))
        return files

    def _get_group(
        self, producer: Producer, index: OutputIndex, names: set[str], group: tuple[str, ...]
    ) -> dict[str, Path]:
        ref = Ref(producer.ref)
        try:
            package = self._variants._get(
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
