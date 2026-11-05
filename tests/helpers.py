"""Consumer-owned sample payloads used by serialization and resource tests."""

import json
from collections.abc import Mapping
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path

from hwcatalog._internal.validation import normalize_record
from hwcatalog.api.models.graph import GraphResult
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.api.protocols import Backend
from hwcatalog.api.types import JSON


class ProjectTypeId(StrEnum):
    RTL_PROJECT = "test.rtl-project"
    HARDMACRO = "test.hardmacro"


class RunTypeId(StrEnum):
    SYNTHESIS = "test.synthesis-run"
    LINT = "test.lint-run"


def release(
    name: str,
    version: str = "v1",
    *,
    team: str = "demo",
    project_type: str = "ip",
    manifest: Mapping[str, JSON] | None = None,
    type_id: str = ProjectTypeId.RTL_PROJECT,
) -> Record:
    return normalize_record(
        Record(
            Ref(name, version, "release", f"{team}-{project_type}"),
            {
                "resources": [
                    {
                        "provider": "git",
                        "repository_url": "https://example.invalid/rtl.git",
                        "requested_ref": "demo-tag",
                        "resolved_commit": "a" * 40,
                    }
                ],
                "type_id": type_id,
                "team_id": team,
                "manifest": manifest or {},
            },
        )
    )


def run(name: str, *, type_id: str = RunTypeId.SYNTHESIS) -> Record:
    return normalize_record(
        Record(
            Ref(name, "1.0", "run", "flow"),
            {"type_id": type_id, "purpose": "synthesis", "details": {"array": [3, 1, 2]}},
        )
    )


def content(record: Record) -> Mapping[str, JSON]:
    assert isinstance(record.payload, Mapping)
    return record.payload


def resources(record: Record) -> tuple[JSON, ...]:
    items = content(record)["resources"]
    assert isinstance(items, tuple)
    return items


def graph_json(result: GraphResult) -> str:
    return json.dumps(asdict(result), sort_keys=True, separators=(",", ":"))


def resolve_published(catalog: Backend, ref: Ref, *, lockfile: Path | None = None) -> GraphResult:
    """Inspect saved package data through the same resolver used for local inputs."""
    record = catalog.get(ref)
    dependencies = tuple(item.ref for item in catalog.dependencies(ref))
    return catalog.resolve_local(record, dependencies=dependencies, lockfile=lockfile)
