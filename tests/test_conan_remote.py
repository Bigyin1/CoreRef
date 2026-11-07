"""Round trips through an owned Conan server, never a configured user server."""

import shutil
from collections.abc import Callable, Iterator
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from dulwich import porcelain
from dulwich.refs import Ref as GitRef
from dulwich.repo import Repo

from examples.conan_config import configured_home
from examples.local_server import local_server
from hwcatalog._internal.recipe import LocalRecipe, StoredRecipe
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import (
    CatalogError,
    NotFound,
)
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import LocalRecord, Record, Ref
from tests.helpers import local_record, resolve_published

pytestmark = pytest.mark.conan_remote


@pytest.fixture
def remote_catalog(tmp_path: Path) -> Iterator[Catalog]:
    with local_server(tmp_path / "server") as server:
        configured = replace(server, remote="catalog")
        yield Catalog(configured.configure(tmp_path / "publisher"))


def test_remote_round_trip_external_ci_and_cache_independence(
    remote_catalog: Catalog, tmp_path: Path
) -> None:
    rtl = Record(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=remote_catalog),
        {"source": "cpu.sv"},
    )
    be = Record(
        Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0", catalog=remote_catalog),
        {"source": "cpu.lib"},
    )
    rtl = remote_catalog.publish(local_record(rtl))
    be = remote_catalog.publish(local_record(be), dependencies=(rtl.ref,))
    assert remote_catalog.publish(local_record(rtl)) == rtl
    original_graph = resolve_published(remote_catalog, be.ref)
    native = remote_catalog._client.graph(remote_catalog._package(be.ref).recipe().directory)
    assert tuple(
        str(dep.ref) for dep in native.root.conanfile.dependencies.host.topological_sort.values()
    ) == ("soc.cpu.rtl.default/1.0",)
    home = tmp_path / "reader"
    home.mkdir()
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(configured_home(home), remote="catalog")
    assert Ref.from_fields(**be.ref.to_dict(), catalog=reader).get().payload == {
        "source": "cpu.lib"
    }
    assert resolve_published(reader, be.ref) == original_graph
    new_rtl = Record(Ref.from_fields("soc", "cpu", "rtl", "default", "2.0", catalog=remote_catalog))
    external = tmp_path / "ci"
    LocalRecipe(local_record(new_rtl)).write(external)
    remote_catalog._client.create_lockfile(external)
    pinned = remote_catalog._client.export(external)
    remote_catalog._client.upload(pinned)
    assert (
        Ref.from_fields(**new_rtl.ref.to_dict(), catalog=reader).get().ref.without_revision()
        == new_rtl.ref
    )
    assert resolve_published(reader, be.ref) == original_graph
    remote_catalog._client.run("remote", "update", "catalog", "--url", "http://127.0.0.1:1")
    with pytest.raises(CatalogError):
        remote_catalog.search(SearchQuery.for_component(rtl.ref, view="be", type="lib-lef"))


def test_cached_package_is_not_published_until_remote_upload_succeeds(
    remote_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = Record(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=remote_catalog),
        {"source": "cpu.sv"},
    )
    catalog = remote_catalog

    def reject_upload(reference: str) -> None:
        raise CatalogError(f"Simulated upload failure: {reference}")

    with monkeypatch.context() as patch:
        patch.setattr(remote_catalog._client, "upload", reject_upload)
        with pytest.raises(CatalogError, match="Simulated upload failure"):
            record = catalog.publish(local_record(record))
    directory = remote_catalog._client.cache_path(str(record.ref))
    assert StoredRecipe(directory, record.ref, reference=str(record.ref)).record == record
    assert catalog.search() == ()

    # A prepared local package must not block another attempt to publish to the server.
    record = catalog.publish(local_record(record))
    assert Ref.from_fields(**record.ref.to_dict(), catalog=catalog).get() == record


def test_remote_keeps_artifacts_and_fetches_sources_from_git_on_demand(
    remote_catalog: Catalog, tmp_path: Path, checkout: Path, git_origin: Path
) -> None:
    # The isolated test server deliberately accepts a local SCM URL.
    (remote_catalog._client.home / "global.conf").write_text("core.scm:local_url=allow\n")
    dependency = Record(
        Ref.from_fields("tools", "flow", "run", "default", "1.0", catalog=remote_catalog)
    )
    dependency = remote_catalog.publish(local_record(dependency))
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    published = remote_catalog.publish(
        local_record(
            Record(
                Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=remote_catalog),
                {"corner": "tt"},
            ),
            source=checkout,
            files={"cpu.lib": artifact},
        ),
        dependencies=(dependency.ref,),
    )
    artifact.unlink()
    shutil.rmtree(checkout)
    home = tmp_path / "fresh-reader"
    home.mkdir()
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(configured_home(home), remote="catalog")
    fetched_record = Ref.from_fields(**published.ref.to_dict(), catalog=reader).get()
    assert fetched_record.ref == published.ref
    assert fetched_record.source == published.source
    assert fetched_record.payload == published.payload
    installed = Ref.from_fields(**published.ref.to_dict(), catalog=reader).get()
    assert installed.source is None
    assert installed.files.keys() == {"cpu.lib"}
    assert installed.files["cpu.lib"].read_text() == "library(cpu) {}"
    offline = git_origin.with_name("offline-origin.git")
    git_origin.rename(offline)
    try:
        with pytest.raises(CatalogError):
            Ref.from_fields(**published.ref.to_dict(), catalog=reader).get_with_sources()
    finally:
        offline.rename(git_origin)
    fetched = Ref.from_fields(**published.ref.to_dict(), catalog=reader).get_with_sources()
    assert fetched.files == installed.files
    assert fetched.source is not None
    assert (fetched.source / "cpu.sv").read_text() == "module cpu; endmodule\n"
    assert fetched.source.is_relative_to(reader._client.home)


def test_one_configured_remote_is_used_automatically(
    remote_catalog: Catalog, tmp_path: Path
) -> None:
    record = Record(
        Ref.from_fields("soc", "automatic", "rtl", "default", "1.0", catalog=remote_catalog)
    )
    publisher = remote_catalog
    assert remote_catalog._client.remote == "catalog"
    record = publisher.publish(local_record(record))
    home = configured_home(tmp_path / "automatic-reader")
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(home)
    assert Ref.from_fields(**record.ref.to_dict(), catalog=reader).get() == record


def test_local_revision_does_not_override_remote_dependency(
    remote_catalog: Catalog, tmp_path: Path
) -> None:
    rtl = Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=remote_catalog))
    rtl = remote_catalog.publish(local_record(rtl))
    # A conflicting local-only revision must never enter published requirements.
    rogue = tmp_path / "local-only-revision"
    LocalRecipe(local_record(Record(rtl.ref, {"local-only": True}))).write(rogue)
    remote_catalog._client.create_lockfile(rogue)
    remote_catalog._client.export(rogue)
    another_be = Record(
        Ref.from_fields("soc", "cpu", "be", "lib-lef", "5.0", catalog=remote_catalog)
    )
    another_be = remote_catalog.publish(local_record(another_be), dependencies=(rtl.ref,))
    assert {r.ref for r in resolve_published(remote_catalog, another_be.ref).records} == {
        another_be.ref,
        rtl.ref,
    }


@pytest.mark.parametrize("operation", ["get", "resolve", "search", "publish"])
def test_warm_operations_do_not_redownload_recipes(
    remote_catalog: Catalog, tmp_path: Path, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "report.txt"
    artifact.write_text("report")
    leaf = remote_catalog.publish(
        LocalRecord(Ref.from_fields("soc", "leaf", "rtl", "default", "1.0", catalog=remote_catalog))
    )
    root = remote_catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "root", "rtl", "default", "1.0", catalog=remote_catalog),
            files={"report": artifact},
        ),
        dependencies=(leaf.ref,),
    )
    calls: list[tuple[str, ...]] = []
    run = remote_catalog._client.run

    def track(*arguments: str) -> object:
        calls.append(arguments)
        return run(*arguments)

    monkeypatch.setattr(remote_catalog._client, "run", track)
    actions: dict[str, Callable[[], object]] = {
        "get": root.ref.get,
        "resolve": lambda: Graph(remote_catalog, local_record(root), dependencies=(leaf.ref,)),
        "search": remote_catalog.search,
        "publish": lambda: remote_catalog.publish(local_record(root), dependencies=(leaf.ref,)),
    }
    actions[operation]()
    downloads = [args[1] for args in calls if args[0] == "download"]
    assert not downloads
    assert sum(args[:2] == ("graph", "info") for args in calls) == (
        operation in ("resolve", "publish")
    )
    assert sum(args[0] == "install" for args in calls) == (operation == "get")


def test_graph_batch_downloads_sources_and_artifacts_into_a_fresh_reader(
    remote_catalog: Catalog, tmp_path: Path, checkout: Path, git_origin: Path
) -> None:
    (remote_catalog._client.home / "global.conf").write_text("core.scm:local_url=allow\n")
    leaf = remote_catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "leaf", "rtl", "default", "1.0", catalog=remote_catalog),
            source=checkout,
        )
    )
    artifact = tmp_path / "report.txt"
    artifact.write_text("server artifact")
    parent = remote_catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "parent", "rtl", "default", "1.0", catalog=remote_catalog),
            source=checkout,
            files={"report": artifact},
        ),
        dependencies=(leaf.ref,),
    )
    artifact.unlink()
    shutil.rmtree(checkout)
    home = tmp_path / "batch-reader"
    home.mkdir()
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    (home / "global.conf").write_text("core.scm:local_url=allow\n")
    reader = Catalog(configured_home(home), remote="catalog")
    graph = Graph(
        reader,
        LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=reader)),
        dependencies=(parent.ref,),
    )
    with patch.object(reader._client, "run", wraps=reader._client.run) as command:
        fetched = graph.get_with_sources((leaf.ref, parent.ref))
        assert all(
            record.source is not None and (record.source / "cpu.sv").is_file() for record in fetched
        )
        assert fetched[1].files["report"].read_text() == "server artifact"
        assert all(record.ref._catalog is reader for record in fetched)
        shutil.rmtree(git_origin)
        assert graph.get_with_sources((parent.ref,))[0] is fetched[1]
        assert command.call_count == 1 and command.call_args.args[0] == "install"


def test_partial_be_is_searchable_from_rtl_in_a_fresh_reader(
    remote_catalog: Catalog, tmp_path: Path
) -> None:
    rtl = remote_catalog.publish(
        LocalRecord(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=remote_catalog))
    )
    artifact = tmp_path / "alu.lib"
    artifact.write_text("library from server")
    partial = remote_catalog.publish(
        LocalRecord(
            Ref.from_fields(
                "soc",
                "cpu",
                "be",
                "lib-lef",
                "4.0",
                subcomponent="alu",
                catalog=remote_catalog,
            ),
            {"partition": "alu"},
            files={"lib": artifact},
        ),
        dependencies=(rtl.ref,),
    )
    home = tmp_path / "partial-reader"
    home.mkdir()
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(configured_home(home), remote="catalog")
    found = reader.search(SearchQuery(namespace="soc", component="cpu", view="be"))
    assert tuple(record.ref for record in found) == (partial.ref,)
    assert found[0].ref.subcomponent == "alu"
    assert found[0].ref.dependencies()[0].ref == rtl.ref
    assert found[0].ref.get().files["lib"].read_text() == "library from server"


def test_remote_latest_and_locked_graphs_survive_new_revisions(
    remote_catalog: Catalog, tmp_path: Path
) -> None:
    logical = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=remote_catalog)
    first = remote_catalog.publish(LocalRecord(logical, {"generation": 1}))
    home = configured_home(tmp_path / "revision-reader")
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(home)
    dependency = Ref.from_fields(**logical.to_dict(), catalog=reader)
    root = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=reader))
    graph = Graph(reader, root, dependencies=(dependency,))
    lock = graph.create_lockfile(tmp_path / "old.lock")
    second = remote_catalog.publish(LocalRecord(logical, {"generation": 2}))
    assert dependency.get().ref == second.ref
    assert Graph(reader, root, dependencies=(dependency,)).requirements == (second.ref,)
    assert Graph(reader, root, dependencies=(dependency,), lockfile=lock).requirements == (
        first.ref,
    )
    assert graph.get((dependency,))[0].ref == first.ref
    assert reader.search() == (second,)
    assert set(reader.search(all_revisions=True)) == {first, second}
    missing = Ref.from_fields(**logical.to_dict(), revision="a" * 32, catalog=reader)
    with pytest.raises(CatalogError):
        missing.get()


def _advance_git_source(checkout: Path, origin: Path) -> None:
    (checkout / "cpu.sv").write_text("module cpu; wire second; endmodule\n")
    porcelain.add(checkout, paths=["cpu.sv"])
    porcelain.commit(
        checkout,
        message=b"Second RTL",
        author=b"Tests <test@example.invalid>",
        committer=b"Tests <test@example.invalid>",
    )
    porcelain.push(checkout, str(origin), refspecs="refs/heads/main", errstream=BytesIO())
    with Repo(checkout) as repo:
        repo.refs[GitRef(b"refs/remotes/origin/main")] = repo.head()


def test_remote_sources_and_files_belong_to_the_requested_revision(
    remote_catalog: Catalog, tmp_path: Path, checkout: Path, git_origin: Path
) -> None:
    (remote_catalog._client.home / "global.conf").write_text("core.scm:local_url=allow\n")
    ref = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=remote_catalog)
    file = tmp_path / "report"
    file.write_text("first")
    first = remote_catalog.publish(LocalRecord(ref, source=checkout, files={"report": file}))
    _advance_git_source(checkout, git_origin)
    file.write_text("second")
    second = remote_catalog.publish(LocalRecord(ref, source=checkout, files={"report": file}))
    home = configured_home(tmp_path / "source-reader")
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(home)
    shutil.rmtree(checkout)
    for record, text in ((first, "first"), (second, "second")):
        fetched = Ref.from_fields(**record.ref.to_dict(), catalog=reader).get_with_sources()
        assert fetched.ref == record.ref
        assert fetched.files["report"].read_text() == text
        assert fetched.source is not None
        assert ("wire second" in (fetched.source / "cpu.sv").read_text()) == (text == "second")


def test_deleted_remote_revision_remains_available_by_exact_cached_ref(
    remote_catalog: Catalog,
) -> None:
    record = remote_catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "deleted", "rtl", "default", "1.0", catalog=remote_catalog)
        )
    )
    remote_catalog._client.run("remove", str(record.ref), "--remote", "catalog", "--confirm")
    assert record.ref.get() == record
    assert record.ref.graph().record == record
    assert remote_catalog.search() == ()
    with pytest.raises(NotFound):
        record.ref.without_revision().get()
