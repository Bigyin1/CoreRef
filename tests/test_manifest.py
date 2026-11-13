"""The standard YAML format is an optional adapter over LocalRecord and Graph."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from coreref.api.records import InstalledRecord, LocalRecord, Ref
from coreref.api.workspace import Workspace
from coreref.tools.manifest import ManifestFile


@pytest.fixture
def file_catalog(tmp_path: Path) -> Catalog:
    return Catalog(tmp_path / "file-home")


def test_yaml_paths_payload_and_explicit_dependency_edits(tmp_path: Path) -> None:
    path = tmp_path / "project/manifest.yml"
    path.parent.mkdir()
    path.write_text(
        "ref: soc.top.rtl.default/1.0\n"
        "files: {spec.json: build/spec.json}\npayload: {sources: [top.sv]}\n"
        "dependencies: [soc.cpu.rtl.default/1.0#old]\n"
    )
    document = ManifestFile(path)
    assert document.record.source is None
    assert document.record.files == {"spec.json": path.parent / "build/spec.json"}
    record = document.record
    assert isinstance(record.payload, dict)
    record.payload["sources"] = []
    assert document.record.payload == {"sources": ["top.sv"]}
    document.add(Ref("soc.cpu.rtl.default/2.0#new"))
    document.add(Ref("soc.cpu.rtl.default/2.0#new"))
    assert document.dependencies == (Ref("soc.cpu.rtl.default/2.0#new"),)
    assert "1.0#old" in path.read_text()
    document.save()
    restored = ManifestFile(path)
    assert restored.dependencies == document.dependencies
    assert restored.record.payload == document.record.payload
    with pytest.raises(Conflict):
        restored.remove(Ref("soc.cpu.rtl.default/2.0#other"))
    with pytest.raises(NotFound):
        restored.remove(Ref("soc.unknown.rtl.default/1.0"))
    with pytest.raises(InvalidPackage, match="itself"):
        restored.add(restored.record.ref)
    restored.remove(Ref("soc.cpu.rtl.default/2.0"))
    restored.save()
    assert ManifestFile(path).dependencies == ()
    assert restored.lockfile == path.parent / "manifest.lock"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "ref: [",
        "ref: no-ref",
        "ref: soc.top.rtl.default/1.0#fixed",
        "ref: soc.top.rtl.default/1.0\nunknown: true",
        "ref: soc.top.rtl.default/1.0\ndependencies: [42]",
        "ref: soc.top.rtl.default/1.0\ndependencies: [broken]",
        "ref: soc.top.rtl.default/1.0\nfiles: [file.bin]",
        "ref: soc.top.rtl.default/1.0\npayload: {when: 2020-01-01}",
        "!!python/object/apply:builtins.print [unsafe]",
        "ref: soc.top.rtl.default/1.0\n"
        "dependencies: [soc.cpu.rtl.default/1.0, soc.cpu.rtl.default/1.0]",
        "ref: soc.top.rtl.default/1.0\n"
        "dependencies: [soc.cpu.rtl.default/1.0, soc.cpu.rtl.default/2.0]",
        "ref: soc.top.rtl.default/1.0\ndependencies: [soc.top.rtl.default/1.0]",
    ],
)
def test_invalid_yaml_is_rejected_without_remote_access(
    file_catalog: Catalog, tmp_path: Path, text: str
) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text(text)
    with (
        patch.object(file_catalog._client, "run", side_effect=AssertionError("Unexpected command")),
        pytest.raises(InvalidPackage),
    ):
        ManifestFile(path)
    assert path.read_text() == text


def test_create_preserves_existing_files_and_supports_independent_manifests(tmp_path: Path) -> None:
    ref = Ref("demo.block.analysis.model/1.0")
    first = ManifestFile.create(ref, tmp_path / "config/model.yaml")
    second = ManifestFile.create(ref, tmp_path / "config/report.yml")
    assert first.record.source is None
    assert second.record.source is None
    assert first.lockfile != second.lockfile
    original = first.path.read_bytes()
    with pytest.raises(Conflict, match="already exists"):
        ManifestFile.create(ref, first.path)
    assert first.path.read_bytes() == original
    with pytest.raises(InvalidPackage, match="revision"):
        ManifestFile.create(Ref(f"{ref}#fixed"), tmp_path / "bad.yaml")
    assert not (tmp_path / "bad.yaml").exists()


@pytest.fixture
def scm_package(catalog: Catalog, checkout: Path, git_origin: Path) -> InstalledRecord:
    assert git_origin.is_dir()
    return catalog.publish(LocalRecord(Ref("soc.cpu.rtl.default/1.0"), source=checkout))


@pytest.mark.conan_remote
def test_full_tree_is_batched_and_lock_refresh_is_explicit(
    catalog: Catalog, scm_package: InstalledRecord, tmp_path: Path
) -> None:
    artifact = tmp_path / "leaf.bin"
    artifact.write_text("leaf bytes")
    leaf = catalog.publish(
        LocalRecord(Ref("soc.leaf.data.default/1.0"), files={"leaf.bin": artifact})
    )
    parent = catalog.publish(
        LocalRecord(Ref("soc.parent.rtl.default/1.0")),
        dependencies=(scm_package.ref, leaf.ref),
    )
    document = ManifestFile.create(
        Ref("soc.top.rtl.default/1.0"), tmp_path / "project/manifest.yml"
    )
    document.add(parent.ref.without_revision())
    document.save()
    document.create_lockfile(catalog)
    original = document.lockfile.read_bytes()
    catalog.publish(LocalRecord(parent.ref.without_revision(), {"new": True}))
    graph = document.resolve(catalog)
    assert graph.info(parent.ref.without_revision()).ref == parent.ref
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as commands:
        installed = graph.get_with_sources()
        assert sum(call.args[0] == "install" for call in commands.call_args_list) == 1
    assert len(installed) == 3
    assert (
        next(r for r in installed if r.ref == leaf.ref).files["leaf.bin"].read_text()
        == "leaf bytes"
    )
    assert next(r for r in installed if r.ref == scm_package.ref).source is not None
    assert document.lockfile.read_bytes() == original
    document.create_lockfile(catalog)
    assert document.lockfile.read_bytes() != original
    assert document.resolve(catalog).info(parent.ref.without_revision()).ref != parent.ref


@pytest.mark.conan_remote
def test_failed_lock_refresh_preserves_previous_file(catalog: Catalog, tmp_path: Path) -> None:
    document = ManifestFile.create(Ref("soc.top.rtl.default/1.0"), tmp_path / "manifest.yml")
    document.create_lockfile(catalog)
    original = document.lockfile.read_bytes()
    document.add(Ref("soc.absent.rtl.default/1.0"))
    document.save()
    with pytest.raises(CatalogError):
        document.create_lockfile(catalog)
    assert document.lockfile.read_bytes() == original


@pytest.mark.conan_remote
def test_yaml_graph_uses_native_workspace_from_current_directory(
    catalog: Catalog, scm_package: InstalledRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = ManifestFile.create(
        Ref("soc.top.rtl.default/1.0"), tmp_path / "project/manifest.yml"
    )
    document.add(scm_package.ref)
    document.save()
    source = Workspace(catalog, document.path.parent / "conanws.yml").open(scm_package.ref)
    monkeypatch.chdir(document.path.parent)
    graph = document.resolve(catalog)
    editable = graph.get_with_sources()[0]
    assert editable.is_editable and editable.source == source
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected install")):
        assert graph.get_with_sources() == (editable,)


@pytest.mark.parametrize("name", ["input.lock", "input.json", "input"])
def test_invalid_extension_never_creates_or_overwrites_a_manifest(
    tmp_path: Path, name: str
) -> None:
    path = tmp_path / name
    ref = Ref("soc.top.rtl.default/1.0")
    with pytest.raises(InvalidPackage, match="extension"):
        ManifestFile.create(ref, path)
    assert not path.exists()
    path.write_text(f"ref: {ref}\n")
    with pytest.raises(InvalidPackage, match="extension"):
        ManifestFile(path)
    assert path.read_text() == f"ref: {ref}\n"


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_lockfile_alias_to_manifest_is_rejected_before_conan(
    file_catalog: Catalog, tmp_path: Path, link: str
) -> None:
    document = ManifestFile.create(Ref("soc.top.rtl.default/1.0"), tmp_path / "manifest.yml")
    original = document.path.read_bytes()
    path = document.lockfile
    if link == "symlink":
        path.symlink_to(document.path)
    else:
        path.hardlink_to(document.path)
    with (
        patch.object(file_catalog._client, "run", side_effect=AssertionError("Unexpected Conan")),
        pytest.raises(InvalidPackage, match="different files"),
    ):
        document.create_lockfile(file_catalog)
    assert document.path.read_bytes() == original
    with pytest.raises(InvalidPackage, match="different files"):
        ManifestFile(document.path)


def test_yaml_duplicate_keys_use_standard_loader_behavior(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text(
        "ref: soc.top.rtl.default/1.0\n"
        "dependencies: [soc.first.rtl.default/1.0]\n"
        "dependencies: [soc.second.rtl.default/1.0]\n"
        "payload: {value: 1, value: 2}\n"
    )
    document = ManifestFile(path)
    assert document.dependencies == (Ref("soc.second.rtl.default/1.0"),)
    assert document.record.payload == {"value": 2}


@pytest.mark.parametrize("source", [".", "rtl", "true", "null"])
def test_source_is_not_a_manifest_field(tmp_path: Path, source: str) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text(f"ref: soc.cpu.rtl.default/1.0\nsource: {source}\n")
    with pytest.raises(InvalidPackage, match="source"):
        ManifestFile(path)


@pytest.mark.parametrize("view", ["rtl", "be"])
def test_scm_is_explicit_flow_policy_for_every_view(
    tmp_path: Path,
    view: str,
) -> None:
    manifest = ManifestFile.create(
        Ref(f"soc.cpu.{view}.default/1.0"), tmp_path / view / "manifest.yml"
    )
    record = manifest.record
    assert record.source is None
    sourced = manifest.record_with_source(manifest.path.parent)
    assert sourced.source == manifest.path.parent
    assert manifest.record.source is None
    assert "source" not in manifest.model_dump()
    manifest.save()
    assert "source:" not in manifest.path.read_text()


def test_default_name_and_lockfile_are_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    manifest = ManifestFile.create(Ref("soc.cpu.rtl.default/1.0"))
    assert manifest.path == tmp_path / "manifest.yml"
    assert manifest.lockfile == tmp_path / "manifest.lock"
    assert ManifestFile().record.ref == manifest.record.ref


@pytest.mark.conan_remote
@pytest.mark.parametrize("update", [False, True])
def test_resolve_preserves_explicit_scm_in_snapshot_and_publication(
    catalog: Catalog, checkout: Path, git_origin: Path, tmp_path: Path, *, update: bool
) -> None:
    assert git_origin.is_dir()
    document = ManifestFile.create(
        Ref("soc.top.rtl.default/1.0"), tmp_path / "project/manifest.yml"
    )
    document.payload = {"generation": 1}
    document.save()
    document.create_lockfile(catalog)
    manifest_before = document.path.read_bytes()
    lock_before = document.lockfile.read_bytes()
    graph = document.resolve(catalog, source=checkout, update=update)
    local = graph.record
    assert isinstance(local, LocalRecord) and local.source == checkout
    assert document.record.source is None
    document.payload = {"generation": 2}
    published = catalog.publish(graph)
    assert published.has_source and published.payload == {"generation": 1}
    installed = catalog.get_with_sources(published.ref)
    assert installed.source is not None and installed.source != checkout
    assert (installed.source / "cpu.sv").read_bytes() == (checkout / "cpu.sv").read_bytes()
    assert document.path.read_bytes() == manifest_before
    assert document.lockfile.read_bytes() == lock_before


def test_manifest_editing_and_record_creation_need_no_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "unused-conan-home"
    monkeypatch.setenv("CONAN_HOME", str(home))
    with (
        patch("coreref._internal.client.ConanAPI", side_effect=AssertionError("Conan initialized")),
        patch("requests.sessions.Session.send", side_effect=AssertionError("Network")),
    ):
        manifest = ManifestFile.create(Ref("soc.top.rtl.default/1.0"), tmp_path / "manifest.yml")
        manifest.add(Ref("soc.cpu.rtl.default/1.0"))
        manifest.files = {"result": "build/result"}
        manifest.payload = {"nested": {"value": 1}}
        manifest.save()
        restored = ManifestFile(manifest.path)
        sourced = restored.record_with_source(tmp_path / "repository")
        plain = restored.record
        assert sourced is not plain and plain is not restored.record
        assert sourced.source == tmp_path / "repository" and plain.source is None
        assert isinstance(sourced.payload, dict)
        sourced.payload["nested"] = {}
        sourced.files = {}
        assert restored.record.payload == {"nested": {"value": 1}}
        assert restored.record.files == {"result": tmp_path / "build/result"}
        restored.remove(restored.dependencies[0])
        restored.save()
        assert ManifestFile(restored.path).dependencies == ()
    assert not home.exists()
