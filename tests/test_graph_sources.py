"""Batch installation uses the resolved snapshot and fetches only requested sources."""

import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from hwcatalog._internal.recipe import LocalRecipe
from hwcatalog._internal.types import NativeGraph
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, Conflict, InvalidPackage
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import LocalRecord, Record, Ref


def test_sources_and_files_are_fetched_once_without_project_lockfile(
    catalog: Catalog,
    checkout: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    leaf = catalog.publish(
        LocalRecord(Ref("soc", "leaf", "rtl", "default", "1.0", catalog=catalog), source=checkout)
    )
    artifact = tmp_path / "report.txt"
    artifact.write_text("report")
    parent = catalog.publish(
        LocalRecord(
            Ref("soc", "parent", "rtl", "default", "1.0", catalog=catalog),
            source=checkout,
            files={"report": artifact},
        ),
        dependencies=(leaf.ref,),
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
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
    fetched = graph.get_with_sources((parent.ref, leaf.ref, parent.ref))
    assert tuple(record.ref for record in fetched) == (parent.ref, leaf.ref)
    assert fetched[0].files["report"].read_text() == "report"
    assert all(
        record.source is not None and (record.source / "cpu.sv").is_file() for record in fetched
    )
    assert graph.get_with_sources((leaf.ref,))[0] is fetched[1]
    assert graph.get_with_sources(()) == ()
    assert all(
        not hasattr(record, "source") for record in graph.records if isinstance(record, Record)
    )
    assert "hwcatalog-conan-batch-" not in capsys.readouterr().err
    assert len(calls) == 1 and calls[0][0] == "install"
    assert not Path(calls[0][calls[0].index("--lockfile") + 1]).exists()


def test_metadata_only_selection_does_not_run_conan(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = catalog.publish(
        LocalRecord(Ref("soc", "meta", "be", "default", "1.0", catalog=catalog))
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(record.ref,),
    )

    def reject(*_arguments: str) -> object:
        raise AssertionError("Metadata and invalid selections must not invoke Conan")

    monkeypatch.setattr(catalog._client, "run", reject)
    assert graph.get_with_sources((record.ref,)) == (record,)
    assert graph.get_with_sources(()) == ()
    for ref in (graph.record.ref, Ref("soc", "absent", "rtl", "default", "1.0", catalog=catalog)):
        with pytest.raises(InvalidPackage, match=r"published dependencies|Unknown reference"):
            graph.get_with_sources((ref,))


def test_failed_batch_can_be_retried(
    catalog: Catalog, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = catalog.publish(
        LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), source=checkout)
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(dependency.ref,),
    )

    def fail(_directory: Path, _sources: Mapping[str, bool]) -> NativeGraph:
        raise CatalogError("temporary failure")

    with monkeypatch.context() as patch:
        patch.setattr(catalog._client, "install_project", fail)
        with pytest.raises(CatalogError, match="temporary failure"):
            graph.get_with_sources((dependency.ref,))
    assert graph.get_with_sources((dependency.ref,))[0].source is not None


def test_install_must_match_the_resolved_references(
    catalog: Catalog, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = catalog.publish(
        LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), source=checkout)
    )
    unexpected = catalog.publish(
        LocalRecord(Ref("soc", "other", "rtl", "default", "1.0", catalog=catalog))
    )
    root = LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    graph = Graph(catalog, root, dependencies=(requested.ref,))
    wrong = Graph(catalog, root, dependencies=(unexpected.ref,))

    def changed(_directory: Path, _sources: Mapping[str, bool]) -> NativeGraph:
        return wrong._package.graph.native

    monkeypatch.setattr(catalog._client, "install_project", changed)
    with pytest.raises(Conflict, match="resolved graph"):
        graph.get_with_sources((requested.ref,))


def test_unpinned_external_requirement_stays_at_resolved_revision(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    leaf = catalog.publish(
        LocalRecord(Ref("soc", "leaf", "rtl", "default", "1.0", catalog=catalog))
    )
    parent = Ref("soc", "parent", "rtl", "default", "1.0", catalog=catalog)
    LocalRecipe(LocalRecord(parent, source=checkout), (str(leaf.ref),)).write(tmp_path / "external")
    native = catalog._client.export_package(tmp_path / "external")
    assert native.root.ref is not None
    catalog._client.upload(native.root.ref.repr_notime())
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(parent,),
    )
    LocalRecipe(LocalRecord(leaf.ref, {"new": "revision"})).write(tmp_path / "new-leaf")
    catalog._client.upload(catalog._client.export(tmp_path / "new-leaf"))
    assert graph.get_with_sources((parent,))[0].source is not None
    assert leaf.ref.get().ref == leaf.ref
    assert leaf.ref.without_revision().get().ref != leaf.ref


def test_required_parent_does_not_fetch_unselected_transitive_source(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    hidden_checkout = tmp_path / "hidden-checkout"
    shutil.copytree(checkout, hidden_checkout)
    hidden = catalog.publish(
        LocalRecord(
            Ref("soc", "hidden", "rtl", "default", "1.0", catalog=catalog),
            source=hidden_checkout,
        )
    )
    parent = catalog.publish(
        LocalRecord(
            Ref("soc", "parent", "rtl", "default", "1.0", catalog=catalog), source=checkout
        ),
        dependencies=(hidden.ref,),
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(parent.ref,),
    )
    hidden_checkout.rename(tmp_path / "offline-hidden")
    profile = catalog._client.home / "profiles/default"
    profile.write_text(profile.read_text() + "\n[conf]\ntools.build:download_source=True\n")
    fetched = graph.get_with_sources((parent.ref,))
    assert len(fetched) == 1 and fetched[0].source is not None
    assert (fetched[0].source / "cpu.sv").is_file()
    (tmp_path / "offline-hidden").rename(hidden_checkout)
    additional = graph.get_with_sources((hidden.ref, parent.ref))
    assert additional[0].source is not None
    assert additional[1] is fetched[0]


def test_metadata_only_record_is_not_installed_with_source_packages(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    metadata = Ref("soc", "meta", "run", "default", "1.0", catalog=catalog)
    LocalRecipe(LocalRecord(metadata, {"meta": True})).write(tmp_path / "recipe-only")
    catalog._client.upload(catalog._client.export(tmp_path / "recipe-only"))
    dependency = catalog.publish(
        LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), source=checkout)
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(metadata, dependency.ref),
    )
    fetched = graph.get_with_sources((dependency.ref, metadata))
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
                Ref("soc", component, "rtl", "default", "1.0", catalog=catalog),
                source=checkout,
                files={"result.lib": artifact},
            )
        )
        for component in ("cpu", "gpio")
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
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
    sourced = graph.get_with_sources(tuple(record.ref for record in records))
    assert all(
        record.source is not None and (record.source / "cpu.sv").is_file() for record in sourced
    )
    assert tuple(record.files for record in sourced) == tuple(record.files for record in fetched)
    assert graph.get((records[0].ref,))[0] is sourced[0]
    assert graph.get_with_sources((records[1].ref,))[0] is sourced[1]
    assert calls == ["install", "install"]


def test_source_only_metadata_get_does_not_install(
    catalog: Catalog, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = catalog.publish(
        LocalRecord(
            Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
            source=checkout,
        )
    )
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
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
        LocalRecord(Ref("soc", "leaf", "rtl", "default", "1.0", catalog=catalog), source=checkout)
    )
    parent = catalog.publish(
        LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(leaf.ref,),
    )
    checkout.rename(tmp_path / "offline-checkout")
    profile = catalog._client.home / "profiles/default"
    profile.write_text(profile.read_text() + "\n[conf]\nsoc.*:tools.build:download_source=True\n")
    graph = parent.ref.graph()
    assert graph.requirements == (leaf.ref,)
    assert graph.get((leaf.ref,))[0].source is None
