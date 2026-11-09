"""Consumer-owned sample payloads used by serialization and resource tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from pathlib import Path

from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.records import JSON, InstalledRecord, LocalRecord, Record, Ref


class ProjectTypeId(str, Enum):
    RTL_PROJECT = "test.rtl-project"
    HARDMACRO = "test.hardmacro"


class RunTypeId(str, Enum):
    SYNTHESIS = "test.synthesis-run"
    LINT = "test.lint-run"


def release(
    name: str, version: str = "v1", *, manifest: Mapping[str, JSON] | None = None, catalog: Catalog
) -> Record:
    return Record(
        Ref.from_fields("demo-ip", name, "release", "default", version, catalog=catalog),
        {
            "resources": [
                {
                    "provider": "git",
                    "repository_url": "https://example.invalid/rtl.git",
                    "requested_ref": "demo-tag",
                    "resolved_commit": "a" * 40,
                }
            ],
            "type_id": ProjectTypeId.RTL_PROJECT,
            "team_id": "demo",
            "manifest": manifest or {},
        },
    )


def run(name: str, *, type_id: str = RunTypeId.SYNTHESIS, catalog: Catalog) -> Record:
    return Record(
        Ref.from_fields("flow", name, "run", "default", "1.0", catalog=catalog),
        {"type_id": type_id, "purpose": "synthesis", "details": {"array": [3, 1, 2]}},
    )


def content(record: Record) -> Mapping[str, JSON]:
    assert isinstance(record.payload, Mapping)
    return record.payload


def resources(record: Record) -> tuple[JSON, ...]:
    items = content(record)["resources"]
    assert isinstance(items, tuple)
    return items


def graph_json(result: Graph) -> str:
    value = {
        "records": [
            (json.loads(record.to_json()) if isinstance(record, LocalRecord) else record.to_dict())
            for record in result.records
        ],
        "dependencies": [
            {"source": source.to_dict(), "target": target.to_dict()}
            for source, target in sorted(result.networkx.edges)
        ],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def resolve_published(catalog: Catalog, ref: Ref, *, lockfile: Path | None = None) -> Graph:
    """Resolve the published root itself through the public API."""
    return Ref.from_fields(**ref.to_dict(), catalog=catalog).graph(lockfile=lockfile)


def local_record(
    record: Record, *, source: Path | None = None, files: dict[str, Path] | None = None
) -> LocalRecord:
    """Use fixture metadata as explicit local publication inputs."""
    return LocalRecord(
        record.ref.without_revision(),
        record.payload,
        source,
        (record.files if isinstance(record, InstalledRecord) else {}) if files is None else files,
    )
