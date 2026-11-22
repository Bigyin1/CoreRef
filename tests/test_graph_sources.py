"""Batch installation uses the resolved snapshot and fetches only requested sources."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from coreref._internal.recipe import LocalRecipe
from coreref._internal.types import NativeGraph
from coreref.api.errors import CatalogError, Conflict, InvalidPackage
from coreref.api.packages.catalog import Catalog
from coreref.api.packages.records import LocalRecord, Record, Ref
from coreref.api.projects.graph import Graph
from coreref.testing import LocalServer, trace_operations


def test_sources_and_files_are_fetched_once_without_project_lockfile(
    catalog: Catalog,
    checkout: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    leaf = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "leaf", "1.0"),
            source=checkout,
        )
    )
    artifact = tmp_path / "report.txt"
    artifact.write_text("report")
    parent = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "parent", "1.0"),
            source=checkout,
            files={"report": artifact},
        ),
        dependencies=(leaf.ref,),
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(parent.ref,),
    )
    graph.create_lockfile(tmp_path / "project.lock").unlink()
    artifact.unlink()
    calls: list[tuple[str, ...]] = []
    run = catalog._client.run

    def track(*arguments: str) -> object:
        calls.append(arguments)
        return run(*arguments)

    monkeypatch.setattr(catalog._client, "run", track)
    fetched = graph.get((parent.ref, leaf.ref, parent.ref), with_sources=True)
    assert tuple(record.ref for record in fetched) == (parent.ref, leaf.ref)
    assert fetched[0].files["report"].read_text() == "report"
    assert all(
        record.source is not None and (record.source / "cpu.sv").is_file() for record in fetched
    )
    assert graph.get((leaf.ref,), with_sources=True)[0] is fetched[1]
    assert graph.get((), with_sources=True) == ()
    assert all(
        not hasattr(record, "source") for record in graph.records if isinstance(record, Record)
    )
    assert "coreref-batch-" not in capsys.readouterr().err
    assert len(calls) == 1 and calls[0][0] == "install"
    assert not Path(calls[0][calls[0].index("--lockfile") + 1]).exists()


def test_only_selected_git_sources_are_fetched_without_package_files(
    catalog: Catalog, conan_server: LocalServer, checkout: Path, tmp_path: Path
) -> None:
    artifact = tmp_path / "large-netlist.v"
    artifact.write_text("netlist")
    leaf = catalog.publish(LocalRecord(Ref("soc.leaf/1.0"), source=checkout))
    parent = catalog.publish(
        LocalRecord(Ref("soc.parent/1.0"), source=checkout, files={artifact.name: artifact}),
        dependencies=(leaf.ref,),
    )
    consumer = Catalog(conan_server.configure(tmp_path / "consumer", local_sources=True))
    graph = Graph(consumer, LocalRecord(Ref("soc.top/1.0")), dependencies=(parent.ref,))

    sources = graph.get_sources((parent.ref.without_revision(),))
    assert tuple(sources) == (parent.ref,)
    assert (sources[parent.ref] / "cpu.sv").is_file()
    assert tuple(consumer._client.cache_directory.rglob("cpu.sv")) == (
        sources[parent.ref] / "cpu.sv",
    )
    assert not tuple(consumer._client.cache_directory.rglob(artifact.name))
    assert graph.get_sources((parent.ref, parent.ref)) == sources
    assert graph.get((parent.ref,))[0].files[artifact.name].read_text() == "netlist"


def test_metadata_only_selection_does_not_run_conan(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = catalog.publish(LocalRecord(Ref.from_fields("soc", "meta", "1.0")))
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(record.ref,),
    )

    def reject(*_arguments: str) -> object:
        raise AssertionError("Metadata and invalid selections must not invoke Conan")

    monkeypatch.setattr(catalog._client, "run", reject)
    assert graph.get((record.ref,), with_sources=True) == (record,)
    assert graph.get((), with_sources=True) == ()
    for ref in (
        graph.record.ref,
        Ref.from_fields("soc", "absent", "1.0"),
    ):
        with pytest.raises(InvalidPackage, match=r"published dependencies|Unknown reference"):
            graph.get((ref,), with_sources=True)


def test_failed_batch_can_be_retried(
    catalog: Catalog, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = catalog.publish(LocalRecord(Ref.from_fields("soc", "cpu", "1.0"), source=checkout))
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(dependency.ref,),
    )

    def fail(_directory: Path, _sources: Mapping[str, bool]) -> NativeGraph:
        raise CatalogError("temporary failure")

    with monkeypatch.context() as patch:
        patch.setattr(catalog._client, "install_project", fail)
        with pytest.raises(CatalogError, match="temporary failure"):
            graph.get((dependency.ref,), with_sources=True)
    assert graph.get((dependency.ref,), with_sources=True)[0].source is not None


def test_install_must_match_the_resolved_references(
    catalog: Catalog, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = catalog.publish(LocalRecord(Ref.from_fields("soc", "cpu", "1.0"), source=checkout))
    unexpected = catalog.publish(LocalRecord(Ref.from_fields("soc", "other", "1.0")))
    root = LocalRecord(Ref.from_fields("soc", "top", "1.0"))
    graph = Graph(catalog, root, dependencies=(requested.ref,))
    wrong = Graph(catalog, root, dependencies=(unexpected.ref,))

    def changed(_directory: Path, _sources: Mapping[str, bool]) -> NativeGraph:
        return wrong._package.graph.native

    monkeypatch.setattr(catalog._client, "install_project", changed)
    with pytest.raises(Conflict, match="resolved graph"):
        graph.get((requested.ref,), with_sources=True)


def test_unpinned_external_requirement_stays_at_resolved_revision(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    leaf = catalog.publish(LocalRecord(Ref.from_fields("soc", "leaf", "1.0")))
    parent = Ref.from_fields("soc", "parent", "1.0")
    LocalRecipe(LocalRecord(parent, source=checkout), (str(leaf.ref),)).write(tmp_path / "external")
    native = catalog._client.export_package(tmp_path / "external")
    assert native.root.ref is not None
    catalog._client.upload(native.root.ref.repr_notime())
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(parent,),
    )
    LocalRecipe(LocalRecord(leaf.ref, {"new": "revision"})).write(tmp_path / "new-leaf")
    catalog._client.upload(catalog._client.export(tmp_path / "new-leaf"))
    assert graph.get((parent,), with_sources=True)[0].source is not None
    assert catalog.get(leaf.ref).ref == leaf.ref
    assert catalog.get(leaf.ref.without_revision()).ref != leaf.ref


def test_required_parent_does_not_fetch_unselected_transitive_source(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    hidden_checkout = tmp_path / "hidden-checkout"
    shutil.copytree(checkout, hidden_checkout)
    hidden = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "hidden", "1.0"),
            source=hidden_checkout,
        )
    )
    parent = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "parent", "1.0"),
            source=checkout,
        ),
        dependencies=(hidden.ref,),
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(parent.ref,),
    )
    hidden_checkout.rename(tmp_path / "offline-hidden")
    profile = catalog._client.home / "profiles/default"
    profile.write_text(profile.read_text() + "\n[conf]\ntools.build:download_source=True\n")
    fetched = graph.get((parent.ref,), with_sources=True)
    assert len(fetched) == 1 and fetched[0].source is not None
    assert (fetched[0].source / "cpu.sv").is_file()
    (tmp_path / "offline-hidden").rename(hidden_checkout)
    additional = graph.get((hidden.ref, parent.ref), with_sources=True)
    assert additional[0].source is not None
    assert additional[1] is fetched[0]


def test_metadata_only_record_is_not_installed_with_source_packages(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    metadata = Ref.from_fields("soc", "meta", "1.0")
    LocalRecipe(LocalRecord(metadata, {"meta": True})).write(tmp_path / "recipe-only")
    catalog._client.upload(catalog._client.export(tmp_path / "recipe-only"))
    dependency = catalog.publish(LocalRecord(Ref.from_fields("soc", "cpu", "1.0"), source=checkout))
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(metadata, dependency.ref),
    )
    fetched = graph.get((dependency.ref, metadata), with_sources=True)
    assert fetched[0].source is not None
    assert fetched[1].payload == {"meta": True}
    assert fetched[1].source is None and fetched[1].files == {}


def test_batch_files_do_not_require_scm_and_can_be_upgraded_to_sources(
    catalog: Catalog, checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "result.lib"
    artifact.write_text("library")
    records = tuple(
        catalog.publish(
            LocalRecord(
                Ref.from_fields("soc", component, "1.0"),
                source=checkout,
                files={"result.lib": artifact},
            )
        )
        for component in ("cpu", "gpio")
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=tuple(record.ref for record in records),
    )
    calls: list[str] = []
    command = catalog._client.run

    def track(*arguments: str) -> object:
        calls.append(arguments[0])
        return command(*arguments)

    monkeypatch.setattr(catalog._client, "run", track)
    checkout.rename(tmp_path / "offline-checkout")
    artifact.unlink()
    fetched = graph.get(tuple(record.ref for record in records))
    assert all(record.source is None for record in fetched)
    assert all(record.files["result.lib"].read_text() == "library" for record in fetched)
    assert calls == ["install"]
    assert graph.get((records[0].ref,))[0] is fetched[0]
    (tmp_path / "offline-checkout").rename(checkout)
    sourced = graph.get(tuple(record.ref for record in records), with_sources=True)
    assert all(
        record.source is not None and (record.source / "cpu.sv").is_file() for record in sourced
    )
    assert tuple(record.files for record in sourced) == tuple(record.files for record in fetched)
    assert graph.get((records[0].ref,))[0] is sourced[0]
    assert graph.get((records[1].ref,), with_sources=True)[0] is sourced[1]
    assert calls == ["install", "install"]


def test_source_only_metadata_get_does_not_install(
    catalog: Catalog, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "1.0"),
            source=checkout,
        )
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(dependency.ref,),
    )

    def reject(*_arguments: str) -> object:
        raise AssertionError("A metadata-only get must not install or fetch SCM")

    monkeypatch.setattr(catalog._client, "run", reject)
    assert graph.get((dependency.ref,)) == (dependency,)
    assert graph.get(()) == ()


def test_published_graph_does_not_fetch_scm_enabled_by_profile(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    leaf = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "leaf", "1.0"),
            source=checkout,
        )
    )
    parent = catalog.publish(
        LocalRecord(Ref.from_fields("soc", "top", "1.0")),
        dependencies=(leaf.ref,),
    )
    checkout.rename(tmp_path / "offline-checkout")
    profile = catalog._client.home / "profiles/default"
    profile.write_text(profile.read_text() + "\n[conf]\nsoc.*:tools.build:download_source=True\n")
    graph = Graph(catalog, parent.ref)
    assert tuple(record.ref for record in graph.dependencies()) == (leaf.ref,)
    assert graph.get((leaf.ref,))[0].source is None


def test_invalid_ref_in_mixed_batch_has_no_side_effects(
    catalog: Catalog, conan_server: LocalServer, checkout: Path, tmp_path: Path
) -> None:
    artifact = tmp_path / "timing.lib"
    artifact.write_text("timing model")
    model = catalog.publish(LocalRecord(Ref("soc.timing/1.0"), files={"timing.lib": artifact})).ref
    rtl = catalog.publish(LocalRecord(Ref("soc.rtl/1.0"), source=checkout)).ref
    consumer = Catalog(conan_server.configure(tmp_path / "consumer"))
    graph = Graph(consumer, LocalRecord(Ref("soc.top/1.0")), dependencies=(model, rtl))
    wrong = Ref(f"{rtl.without_revision()}#different")

    with trace_operations() as rejected:
        with pytest.raises(InvalidPackage, match="Revision is not part"):
            graph.get((model, wrong), with_sources=True)
        with pytest.raises(InvalidPackage, match="Revision is not part"):
            graph.get_sources((rtl, wrong))
    assert not rejected.commands
    assert rejected.package_downloads == rejected.source_clones == 0

    with trace_operations() as fetched:
        records = graph.get((rtl.without_revision(), model, rtl), with_sources=True)
    assert tuple(record.ref for record in records) == (rtl, model)
    assert records[0].source is not None and (records[0].source / "cpu.sv").is_file()
    assert records[1].files["timing.lib"].read_text() == "timing model"
    assert fetched.installs == fetched.source_clones == 1

    with trace_operations() as cached:
        assert graph.get((rtl, model), with_sources=True) == records
        assert graph.get_sources((model, rtl)) == {rtl: records[0].source}
    assert not cached.commands
    assert cached.package_downloads == cached.source_clones == 0
