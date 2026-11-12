"""Optional component parts retain exact identity through Conan and project snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from coreref._internal.references import from_conan_reference, to_conan_reference
from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Record, Ref


@pytest.mark.parametrize("subcomponent", [None, "alu", "cluster_0"])
def test_ref_and_record_roundtrip(subcomponent: str | None) -> None:
    ref = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", subcomponent=subcomponent)
    component = "cpu" if subcomponent is None else f"cpu.{subcomponent}"
    assert str(ref) == f"soc.{component}.be.lib-lef/1.0"
    assert from_conan_reference(to_conan_reference(ref) + "#revision").without_revision() == ref
    assert Ref.from_fields(**ref.to_dict()) == ref
    assert ("subcomponent" in ref.to_dict()) == (subcomponent is not None)
    record = Record(ref, {"area": 10})
    assert json.loads(json.dumps(record.to_dict()))["ref"] == ref.to_dict()
    assert json.loads(LocalRecord(ref).to_json())["ref"] == ref.to_dict()


def test_parts_have_distinct_hashes_and_a_stable_order() -> None:
    root = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0")
    alu = Ref.from_fields(**(root.to_dict() | {"subcomponent": "alu"}))
    cache = Ref.from_fields(**(root.to_dict() | {"subcomponent": "cache"}))
    assert len({root, alu, cache}) == 3
    assert tuple(sorted((cache, root, alu))) == (root, alu, cache)


@pytest.mark.parametrize("subcomponent", ["", "ALU", "a.b", "a/b", "a*", "a" * 102])
def test_parts_reject_lossy_conan_names(subcomponent: str) -> None:
    with pytest.raises(InvalidPackage):
        ref = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", subcomponent=subcomponent)
        to_conan_reference(ref)


@pytest.mark.parametrize("component", ["cpu_core", "cpu_alu_cluster", "cpu__core", "cpu_"])
def test_underscores_are_literal_component_characters(component: str) -> None:
    ref = Ref(f"soc.{component}.be.lib-lef/1.0")
    assert ref.component == component
    assert ref.subcomponent is None
    part = Ref.from_fields(**(ref.to_dict() | {"subcomponent": "cluster_0"}))
    assert str(part) == f"soc.{component}.cluster_0.be.lib-lef/1.0"
    assert Ref(str(part)) == part


@pytest.mark.parametrize(
    "query,pattern",
    [
        (SearchQuery(component="cpu", subcomponent="alu"), "*.cpu.alu.*.*/*#*"),
        (SearchQuery(subcomponent="alu"), "*.*.alu.*.*/*#*"),
        (SearchQuery(component="cpu"), "*.cpu*.*.*/*#*"),
        (SearchQuery(component="cpu", subcomponent=None), "*.cpu.*.*/*#*"),
        (SearchQuery(subcomponent=None), "*.*.*.*/*#*"),
        (SearchQuery(component="cpu_alu"), "*.cpu_alu*.*.*/*#*"),
    ],
)
def test_search_patterns(query: SearchQuery, pattern: str) -> None:
    assert query._conan_pattern(all_revisions=True) == pattern


@pytest.mark.parametrize("component", ["cpu", "cpu_core"])
def test_component_search_includes_parts_but_excludes_prefix_neighbours(
    catalog: Catalog, component: str
) -> None:
    base = Ref.from_fields("soc", component, "be", "lib-lef", "1.0")
    records = tuple(
        catalog.publish(LocalRecord(ref))
        for ref in (
            base,
            Ref.from_fields(**(base.to_dict() | {"subcomponent": "alu"})),
            Ref.from_fields(**(base.to_dict() | {"subcomponent": "cache"})),
            Ref.from_fields(**(base.to_dict() | {"component": f"{component}-extra"})),
            Ref.from_fields(**(base.to_dict() | {"component": f"{component}_alu"})),
        )
    )
    with patch.object(
        catalog._client, "download_recipe", wraps=catalog._client.download_recipe
    ) as download:
        assert catalog.search(SearchQuery(component=component)) == records[:3]
        assert download.call_count == 3
    assert catalog.search(SearchQuery(component=component, subcomponent="alu")) == (records[1],)
    assert catalog.search(SearchQuery(subcomponent="cache")) == (records[2],)
    assert tuple(
        record for record in records if SearchQuery(subcomponent="alu").matches(record)
    ) == (records[1],)
    with patch.object(
        catalog._client, "download_recipe", wraps=catalog._client.download_recipe
    ) as download:
        assert catalog.search(SearchQuery(component=component, subcomponent=None)) == (records[0],)
        assert download.call_count == 1
    assert tuple(
        record for record in records if SearchQuery(subcomponent=None).matches(record)
    ) == (records[0], records[3], records[4])


def test_graph_lockfile_and_batch_files_keep_parts_distinct(
    catalog: Catalog, tmp_path: Path
) -> None:
    artifact = tmp_path / "macro.lib"
    artifact.write_text("library")
    records = tuple(
        catalog.publish(
            LocalRecord(
                Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", subcomponent=part),
                files={"lib": artifact},
            )
        )
        for part in ("alu", "cache")
    )
    root = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0"))
    graph = Graph(catalog, root, dependencies=tuple(record.ref for record in records))
    lockfile = graph.create_lockfile(tmp_path / "project.lock")
    locked = Graph(catalog, root, dependencies=graph.requirements, lockfile=lockfile)
    assert tuple(sorted(locked.networkx.edges)) == tuple(sorted(graph.networkx.edges))
    fetched = locked.get(graph.requirements)
    assert tuple(record.ref.subcomponent for record in fetched) == ("alu", "cache")
    assert all(record.files["lib"].read_text() == "library" for record in fetched)
