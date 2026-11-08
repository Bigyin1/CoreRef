"""The standard YAML format is an optional adapter over LocalRecord and Graph."""

from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from coreref.api.records import InstalledRecord, LocalRecord, Ref
from coreref.api.workspace import Workspace
from coreref.tools.deps import DepsFile


@pytest.fixture
def file_catalog(tmp_path: Path) -> Catalog:
    return Catalog(tmp_path / "file-home")


def test_yaml_paths_payload_and_explicit_dependency_edits(
    file_catalog: Catalog, tmp_path: Path
) -> None:
    path = tmp_path / "project/deps.yaml"
    path.parent.mkdir()
    path.write_text(
        "ref: soc.top.rtl.default/1.0\nsource: .\n"
        "files: {spec.json: build/spec.json}\npayload: {sources: [top.sv]}\n"
        "dependencies: [soc.cpu.rtl.default/1.0#old]\n"
    )
    document = DepsFile(file_catalog, path)
    assert document.record.source == path.parent
    assert document.record.files == {"spec.json": path.parent / "build/spec.json"}
    record = document.record
    assert isinstance(record.payload, dict)
    record.payload["sources"] = []
    assert document.record.payload == {"sources": ["top.sv"]}
    document.add(Ref("soc.cpu.rtl.default/2.0#new", catalog=file_catalog))
    document.add(Ref("soc.cpu.rtl.default/2.0#new", catalog=file_catalog))
    assert document.dependencies == (Ref("soc.cpu.rtl.default/2.0#new", catalog=file_catalog),)
    assert "1.0#old" in path.read_text()
    document.save()
    restored = DepsFile(file_catalog, path)
    assert restored.dependencies == document.dependencies
    assert restored.record.payload == document.record.payload
    with pytest.raises(Conflict):
        restored.remove(Ref("soc.cpu.rtl.default/2.0#other", catalog=file_catalog))
    with pytest.raises(NotFound):
        restored.remove(Ref("soc.unknown.rtl.default/1.0", catalog=file_catalog))
    with pytest.raises(InvalidPackage, match="itself"):
        restored.add(restored.record.ref)
    restored.remove(Ref("soc.cpu.rtl.default/2.0", catalog=file_catalog))
    restored.save()
    assert DepsFile(file_catalog, path).dependencies == ()
    assert restored.lockfile == path.parent / "deps.lock"


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
        "ref: soc.top.rtl.default/1.0\ndependencies: []\ndependencies: []",
        "ref: soc.top.rtl.default/1.0\npayload: {value: 1, value: 2}",
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
    path = tmp_path / "deps.yaml"
    path.write_text(text)
    with (
        patch.object(file_catalog._client, "run", side_effect=AssertionError("Unexpected command")),
        pytest.raises(InvalidPackage),
    ):
        DepsFile(file_catalog, path)
    assert path.read_text() == text


def test_create_preserves_existing_files_and_supports_independent_manifests(
    file_catalog: Catalog, tmp_path: Path
) -> None:
    ref = Ref("demo.block.analysis.model/1.0", catalog=file_catalog)
    first = DepsFile.create(file_catalog, ref, tmp_path / "config/model.yaml", source="../source")
    second = DepsFile.create(file_catalog, ref, tmp_path / "config/report.yml")
    assert first.record.source == tmp_path / "source"
    assert second.record.source is None
    assert first.lockfile != second.lockfile
    original = first.path.read_bytes()
    with pytest.raises(Conflict, match="already exists"):
        DepsFile.create(file_catalog, ref, first.path)
    assert first.path.read_bytes() == original
    with pytest.raises(InvalidPackage, match="revision"):
        DepsFile.create(
            file_catalog, Ref(f"{ref}#fixed", catalog=file_catalog), tmp_path / "bad.yaml"
        )
    assert not (tmp_path / "bad.yaml").exists()


@pytest.fixture
def scm_package(catalog: Catalog, checkout: Path, git_origin: Path) -> InstalledRecord:
    assert git_origin.is_dir()
    return catalog.publish(
        LocalRecord(Ref("soc.cpu.rtl.default/1.0", catalog=catalog), source=checkout)
    )


@pytest.mark.conan_remote
def test_full_tree_is_batched_and_lock_refresh_is_explicit(
    catalog: Catalog, scm_package: InstalledRecord, tmp_path: Path
) -> None:
    artifact = tmp_path / "leaf.bin"
    artifact.write_text("leaf bytes")
    leaf = catalog.publish(
        LocalRecord(Ref("soc.leaf.data.default/1.0", catalog=catalog), files={"leaf.bin": artifact})
    )
    parent = catalog.publish(
        LocalRecord(Ref("soc.parent.rtl.default/1.0", catalog=catalog)),
        dependencies=(scm_package.ref, leaf.ref),
    )
    document = DepsFile.create(
        catalog, Ref("soc.top.rtl.default/1.0", catalog=catalog), tmp_path / "project/deps.yaml"
    )
    document.add(parent.ref.without_revision())
    document.save()
    document.create_lockfile()
    original = document.lockfile.read_bytes()
    catalog.publish(LocalRecord(parent.ref.without_revision(), {"new": True}))
    graph = document.resolve()
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
    document.create_lockfile()
    assert document.lockfile.read_bytes() != original
    assert document.resolve().info(parent.ref.without_revision()).ref != parent.ref


@pytest.mark.conan_remote
def test_failed_lock_refresh_preserves_previous_file(catalog: Catalog, tmp_path: Path) -> None:
    document = DepsFile.create(
        catalog, Ref("soc.top.rtl.default/1.0", catalog=catalog), tmp_path / "deps.yaml"
    )
    document.create_lockfile()
    original = document.lockfile.read_bytes()
    document.add(Ref("soc.absent.rtl.default/1.0", catalog=catalog))
    document.save()
    with pytest.raises(CatalogError):
        document.create_lockfile()
    assert document.lockfile.read_bytes() == original


@pytest.mark.conan_remote
def test_yaml_graph_uses_its_native_workspace_from_another_directory(
    catalog: Catalog, scm_package: InstalledRecord, tmp_path: Path
) -> None:
    document = DepsFile.create(
        catalog, Ref("soc.top.rtl.default/1.0", catalog=catalog), tmp_path / "project/deps.yaml"
    )
    document.add(scm_package.ref)
    document.save()
    editable = Workspace(catalog, document.path.parent / "conanws.yml").add(scm_package.ref)
    graph = document.resolve()
    assert graph.editables == (editable,)
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected install")):
        assert graph.get_with_sources() == (editable,)


@pytest.mark.parametrize("name", ["input.lock", "input.json", "input"])
def test_invalid_extension_never_creates_or_overwrites_a_manifest(
    file_catalog: Catalog, tmp_path: Path, name: str
) -> None:
    path = tmp_path / name
    ref = Ref("soc.top.rtl.default/1.0", catalog=file_catalog)
    with pytest.raises(InvalidPackage, match="extension"):
        DepsFile.create(file_catalog, ref, path)
    assert not path.exists()
    path.write_text(f"ref: {ref}\n")
    with pytest.raises(InvalidPackage, match="extension"):
        DepsFile(file_catalog, path)
    assert path.read_text() == f"ref: {ref}\n"


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_lockfile_alias_to_manifest_is_rejected_before_conan(
    file_catalog: Catalog, tmp_path: Path, link: str
) -> None:
    document = DepsFile.create(
        file_catalog, Ref("soc.top.rtl.default/1.0", catalog=file_catalog), tmp_path / "deps.yaml"
    )
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
        document.create_lockfile()
    assert document.path.read_bytes() == original
    with pytest.raises(InvalidPackage, match="different files"):
        DepsFile(file_catalog, document.path)


@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("phase", ["write", "commit"])
def test_failed_yaml_write_preserves_original_and_allows_retry(
    file_catalog: Catalog, tmp_path: Path, create: bool, phase: str
) -> None:
    document = DepsFile.create(
        file_catalog, Ref("soc.top.rtl.default/1.0", catalog=file_catalog), tmp_path / "deps.yaml"
    )
    document.path.chmod(0o600)
    original = document.path.read_bytes()
    target = tmp_path / "new.yaml" if create else document.path
    original_write = Path.write_text

    def write_partial(path: Path, content: str, *, encoding: str | None = None) -> int:
        original_write(path, content[:8], encoding=encoding)
        raise OSError("injected storage failure")

    failure: AbstractContextManager[object]
    if phase == "write":
        failure = patch.object(Path, "write_text", write_partial)
    else:
        failure = patch.object(
            Path, "hardlink_to" if create else "replace", side_effect=OSError("injected failure")
        )
    with failure, pytest.raises(OSError):
        if create:
            DepsFile.create(file_catalog, document.record.ref, target)
        else:
            document.save()
    assert document.path.read_bytes() == original
    assert document.path.stat().st_mode & 0o777 == 0o600
    assert not tuple(tmp_path.glob(".coreref-write-*"))
    if create:
        assert not target.exists()
        DepsFile.create(file_catalog, document.record.ref, target)
    else:
        document.save()
        assert document.path.stat().st_mode & 0o777 == 0o600
    assert DepsFile(file_catalog, target).record.ref == document.record.ref
