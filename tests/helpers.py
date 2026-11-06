"""Consumer-owned sample payloads used by serialization and resource tests."""

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import LocalRecord, Record, Ref
from hwcatalog.api.types import JSON


class ProjectTypeId(StrEnum):
    RTL_PROJECT = "test.rtl-project"
    HARDMACRO = "test.hardmacro"


class RunTypeId(StrEnum):
    SYNTHESIS = "test.synthesis-run"
    LINT = "test.lint-run"


def release(
    name: str, version: str = "v1", *, manifest: Mapping[str, JSON] | None = None, catalog: Catalog
) -> Record:
    return Record(
        Ref("demo-ip", name, "release", "default", version, catalog=catalog),
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
        Ref("flow", name, "run", "default", "1.0", catalog=catalog),
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
            {"source": edge.source.to_dict(), "target": edge.target.to_dict()}
            for edge in result.dependencies
        ],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def resolve_published(catalog: Catalog, ref: Ref, *, lockfile: Path | None = None) -> Graph:
    """Inspect saved package data through the same resolver used for local inputs."""
    ref = Ref(**ref.to_dict(), catalog=catalog)
    record = ref.get()
    dependencies = tuple(item.ref for item in ref.dependencies())
    return Graph(catalog, local_record(record), dependencies=dependencies, lockfile=lockfile)


def local_record(
    record: Record, *, source: Path | None = None, files: dict[str, Path] | None = None
) -> LocalRecord:
    """Use fixture metadata as explicit local publication inputs."""
    return LocalRecord(
        record.ref,
        record.payload,
        source,
        record.files if files is None else files,
    )
