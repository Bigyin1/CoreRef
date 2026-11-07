"""Optional component parts retain exact identity through Conan and project snapshots."""

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
def test_ref_and_record_roundtrip(catalog: Catalog, subcomponent: str | None) -> None:
    ref = Ref.from_fields(
        "soc", "cpu", "be", "lib-lef", "1.0", subcomponent=subcomponent, catalog=catalog
    )
    component = "cpu" if subcomponent is None else f"cpu_{subcomponent}"
    assert str(ref) == f"soc.{component}.be.lib-lef/1.0"
    assert (
        from_conan_reference(
            to_conan_reference(ref) + "#revision", catalog=catalog
        ).without_revision()
        == ref
    )
    assert Ref.from_fields(**ref.to_dict(), catalog=catalog) == ref
    assert ("subcomponent" in ref.to_dict()) == (subcomponent is not None)
    record = Record(ref, {"area": 10})
    assert Record.from_json(json.dumps(record.to_dict()), catalog=catalog) == record
    assert json.loads(LocalRecord(ref).to_json())["ref"] == ref.to_dict()


def test_parts_have_distinct_hashes_and_a_stable_order(catalog: Catalog) -> None:
    root = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=catalog)
    alu = Ref.from_fields(**(root.to_dict() | {"subcomponent": "alu"}), catalog=catalog)
    cache = Ref.from_fields(**(root.to_dict() | {"subcomponent": "cache"}), catalog=catalog)
    assert len({root, alu, cache}) == 3
    assert tuple(sorted((cache, root, alu))) == (root, alu, cache)
    assert tuple(
        record.ref for record in SearchQuery().select(map(Record, (cache, alu, root)))
    ) == (root, alu, cache)


@pytest.mark.parametrize("subcomponent", ["", "ALU", "a.b", "a/b", "a*", "a" * 102])
def test_parts_reject_lossy_conan_names(catalog: Catalog, subcomponent: str) -> None:
    with pytest.raises(InvalidPackage):
        ref = Ref.from_fields(
            "soc", "cpu", "be", "lib-lef", "1.0", subcomponent=subcomponent, catalog=catalog
        )
        to_conan_reference(ref)


def test_underscore_is_reserved_as_the_component_separator(catalog: Catalog) -> None:
    with pytest.raises(InvalidPackage, match="Underscore"):
        to_conan_reference(
            Ref.from_fields("soc", "cpu_alu", "be", "lib-lef", "1.0", catalog=catalog)
        )
    for value in ("soc.cpu_.be.lib-lef/1.0", "soc._alu.be.lib-lef/1.0"):
        with pytest.raises(InvalidPackage):
            from_conan_reference(value, catalog=catalog)


@pytest.mark.parametrize(
    "query,pattern",
    [
        (SearchQuery(component="cpu", subcomponent="alu"), "*.cpu_alu.*.*/*#*"),
        (SearchQuery(subcomponent="alu"), "*.*_alu.*.*/*#*"),
        (SearchQuery(component="cpu"), "*.cpu*.*.*/*#*"),
        (SearchQuery(component="cpu", subcomponent=None), "*.cpu.*.*/*#*"),
        (SearchQuery(subcomponent=None), "*.*.*.*/*#*"),
        (SearchQuery(component="cpu_alu"), None),
        (SearchQuery(subcomponent=""), None),
        (SearchQuery(subcomponent="alu*"), None),
    ],
)
def test_search_patterns(query: SearchQuery, pattern: str | None) -> None:
    assert query.conan_pattern(all_revisions=True) == pattern


def test_component_search_includes_parts_but_excludes_prefix_neighbours(catalog: Catalog) -> None:
    base = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=catalog)
    records = tuple(
        catalog.publish(LocalRecord(ref))
        for ref in (
            base,
            Ref.from_fields(**(base.to_dict() | {"subcomponent": "alu"}), catalog=catalog),
            Ref.from_fields(**(base.to_dict() | {"subcomponent": "cache"}), catalog=catalog),
            Ref.from_fields(**(base.to_dict() | {"component": "cpu-extra"}), catalog=catalog),
        )
    )
    with patch.object(
        catalog._client, "download_recipe", wraps=catalog._client.download_recipe
    ) as download:
        assert catalog.search(SearchQuery(component="cpu")) == records[:3]
        assert download.call_count == 3
    assert catalog.search(SearchQuery(component="cpu", subcomponent="alu")) == (records[1],)
    assert catalog.search(SearchQuery(subcomponent="cache")) == (records[2],)
    assert SearchQuery(subcomponent="alu").select(records) == (records[1],)
    with patch.object(
        catalog._client, "download_recipe", wraps=catalog._client.download_recipe
    ) as download:
        assert catalog.search(SearchQuery(component="cpu", subcomponent=None)) == (records[0],)
        assert download.call_count == 1
    assert SearchQuery(subcomponent=None).select(records) == (records[0], records[3])


def test_graph_lockfile_and_batch_files_keep_parts_distinct(
    catalog: Catalog, tmp_path: Path
) -> None:
    artifact = tmp_path / "macro.lib"
    artifact.write_text("library")
    records = tuple(
        catalog.publish(
            LocalRecord(
                Ref.from_fields(
                    "soc", "cpu", "be", "lib-lef", "1.0", subcomponent=part, catalog=catalog
                ),
                files={"lib": artifact},
            )
        )
        for part in ("alu", "cache")
    )
    root = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    graph = Graph(catalog, root, dependencies=tuple(record.ref for record in records))
    lockfile = graph.create_lockfile(tmp_path / "project.lock")
    locked = Graph(catalog, root, dependencies=graph.requirements, lockfile=lockfile)
    assert locked.edges == graph.edges
    fetched = locked.get(graph.requirements)
    assert tuple(record.ref.subcomponent for record in fetched) == ("alu", "cache")
    assert all(record.files["lib"].read_text() == "library" for record in fetched)
