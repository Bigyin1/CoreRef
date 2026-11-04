import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict
from enum import StrEnum
from textwrap import dedent

from hwcatalog._internal.validation import normalize_record
from hwcatalog.api.enums import RelationKind
from hwcatalog.api.models.graph import GraphResult, Relation
from hwcatalog.api.models.records import (
    Record,
    Ref,
)
from hwcatalog.api.types import JSON


class ProjectTypeId(StrEnum):
    """Consumer-defined contracts for test fixtures."""

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
            Ref(name, version, "release", f"{team}/{project_type}"),
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
            Ref(name, "1", "run", "flow"),
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


def resolution_graph() -> tuple[Record, tuple[Record, ...], tuple[Relation, ...]]:
    """An opaque version chain with a proof-only intermediate and an obsolete branch."""
    root = release("resolver-root")
    old = release("resolver-lib", "z-old", manifest={"revision": "old"})
    middle = release("resolver-lib", "bridge")
    selected = release("resolver-lib", "a-new", manifest={"revision": "selected"})
    obsolete, current = release("obsolete"), release("current")
    edges = (
        Relation(root.ref, old.ref, RelationKind.REQUIRES, "input", 0, {"ports": [1]}),
        Relation(root.ref, selected.ref, RelationKind.REQUIRES, "input", 1, {"ports": [2]}),
        Relation(selected.ref, middle.ref, RelationKind.BACKWARD_COMPATIBLE_WITH),
        Relation(middle.ref, old.ref, RelationKind.BACKWARD_COMPATIBLE_WITH),
        Relation(old.ref, obsolete.ref, RelationKind.REQUIRES),
        Relation(selected.ref, current.ref, RelationKind.REQUIRES),
    )
    return root, (root, old, middle, selected, obsolete, current), edges


def greedy_resolution_graph() -> tuple[Record, tuple[Record, ...], tuple[Relation, ...]]:
    """Resolve the near parent before its alphabetically earlier, obsolete child."""
    root = release("greedy-root")
    parent_old, parent_new = release("z-parent", "old"), release("z-parent", "new")
    child_old, child_new = release("a-child", "old"), release("a-child", "new")
    leaf_old, leaf_new = release("leaf", "old"), release("leaf", "new")
    relations = (
        Relation(root.ref, parent_old.ref, RelationKind.REQUIRES),
        Relation(root.ref, parent_new.ref, RelationKind.REQUIRES),
        Relation(root.ref, leaf_old.ref, RelationKind.REQUIRES),
        Relation(parent_old.ref, child_new.ref, RelationKind.REQUIRES),
        Relation(parent_new.ref, child_old.ref, RelationKind.REQUIRES),
        Relation(child_new.ref, leaf_new.ref, RelationKind.REQUIRES),
        Relation(parent_new.ref, parent_old.ref, RelationKind.BACKWARD_COMPATIBLE_WITH),
        Relation(child_new.ref, child_old.ref, RelationKind.BACKWARD_COMPATIBLE_WITH),
    )
    return root, (root, parent_old, parent_new, child_old, child_new, leaf_old, leaf_new), relations


def graph_json(result: GraphResult) -> str:
    """Compare graph results byte for byte, including record and relation order."""
    return json.dumps(
        asdict(result),
        sort_keys=True,
        separators=(",", ":"),
    )


_RESOLVE_SCRIPT = dedent("""
    import json
    import sys
    from dataclasses import asdict

    from hwcatalog.api.catalog import Catalog
    from hwcatalog.api.errors import VersionConflict
    from hwcatalog.api.models.graph import Relation
    from hwcatalog.api.models.records import Record, Ref
    from hwcatalog.backends.memory import MemoryBackend

    if sys.argv[1] == "memory":
        backend = MemoryBackend()
    else:
        from hwcatalog.backends.memgraph import MemgraphBackend
        backend = MemgraphBackend(sys.argv[1])
    with Catalog(backend) as catalog:
        payload = sys.stdin.read()
        if payload:
            value = json.loads(payload)
            records = []
            for record in value["records"]:
                records.append(Record.from_json(json.dumps(record)))
            catalog.publish(
                tuple(records),
                relations=tuple(
                    Relation.from_json(json.dumps(edge)) for edge in value["relations"]
                ),
            )
        root = Ref(**json.loads(sys.argv[2]))
        try:
            result = catalog.graph.dependency_closure(root, resolve_versions=True)
            print(json.dumps(
                asdict(result),
                sort_keys=True, separators=(",", ":"),
            ))
        except VersionConflict as exc:
            print(f"VersionConflict: {exc}")
""")


def resolve_in_process(
    root: Ref,
    *,
    records: tuple[Record, ...] | None = None,
    relations: tuple[Relation, ...] = (),
    uri: str = "memory",
    hash_seed: str = "0",
) -> str:
    payload = ""
    if records is not None:
        payload = json.dumps(
            {
                "records": [asdict(record) for record in records],
                "relations": [asdict(edge) for edge in relations],
            }
        )
    result = subprocess.run(  # noqa: S603 - fixed isolated Python script and explicit test inputs
        [sys.executable, "-P", "-c", _RESOLVE_SCRIPT, uri, json.dumps(asdict(root))],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()
