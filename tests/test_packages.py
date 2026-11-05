"""Public single-package descriptions, stored artifacts and Conan SCM capture."""

import json
import shutil
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

import pytest
from dulwich.repo import Repo

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import (
    BackendUnavailable,
    CatalogError,
    IdentityConflict,
    InvalidPackage,
    InvalidPublication,
    MaterializationError,
    NotFound,
)
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.api.models.resources import GitResource
from hwcatalog.backends.conan import ConanBackend
from tests.helpers import resolve_published


@pytest.fixture
def packages(tmp_path: Path) -> Iterator[Catalog]:
    backend = ConanBackend(tmp_path / "home")
    with Catalog(backend) as catalog:
        yield catalog


def description(tmp_path: Path, **fields: object) -> Path:
    path = tmp_path / "package.json"
    document = {"ref": asdict(Ref("cpu", "4.0", "lib-lef", "soc")), **fields}
    path.write_text(json.dumps(document))
    return path


def test_publish_json_dependencies_and_stored_files(packages: Catalog, tmp_path: Path) -> None:
    rtl = Record(Ref("cpu", "1.0", "rtl", "soc"))
    packages.publish(rtl)
    artifact = tmp_path / "cpu.lib"
    artifact.write_bytes(b"library(cpu) {}\n\x00")
    path = description(
        tmp_path,
        payload={"corner": "tt"},
        dependencies=[asdict(rtl.ref)],
        files={"timing/cpu.lib": "cpu.lib"},
    )
    record = packages.publish_file(str(path))
    assert record.files == ("timing/cpu.lib",)
    assert record.source is None
    assert packages.get(record.ref) == record
    assert packages.graph.dependencies(record.ref) == (packages.get(rtl.ref),)
    assert resolve_published(packages, record.ref).dependencies == (
        Dependency(record.ref, rtl.ref),
    )
    artifact.unlink()
    path.unlink()
    content = packages.materialize(record.ref).path
    assert (content / "artifacts/timing/cpu.lib").read_bytes() == b"library(cpu) {}\n\x00"
    assert packages.materialize(record.ref).path == content
    with pytest.raises(InvalidPublication):
        packages.publish(record)


def test_local_source_is_captured_once_outside_payload(
    packages: Catalog, checkout: Path, tmp_path: Path
) -> None:
    path = description(tmp_path, source={"path": "checkout"}, payload={"tool": "synthesis"})
    record = packages.publish_file(path)
    assert record.source is not None
    with Repo(checkout) as repository:
        assert record.source.resolved_commit == repository.head().decode()
    assert record.payload == {"tool": "synthesis"}
    assert packages.get(record.ref) == record
    artifacts = packages.materialize(record.ref)
    assert artifacts.source_path is None
    assert not (artifacts.path / "source").exists()
    fetched = packages.materialize(record.ref, with_sources=True)
    assert fetched.source_path is not None
    assert (fetched.source_path / "cpu.sv").read_text() == "module cpu; endmodule\n"
    assert not fetched.source_path.is_relative_to(fetched.path)
    shutil.rmtree(checkout)
    assert packages.materialize(record.ref, with_sources=True).source_path == fetched.source_path
    assert Record.from_json(record.to_json()) == record


def test_pinned_source_round_trip_uses_conan_scm(tmp_path: Path, checkout: Path) -> None:
    source = GitResource(str(checkout), "main", _commit(checkout))
    with Catalog(backend := ConanBackend(tmp_path / "home")) as catalog:
        record = catalog.publish_file(description(tmp_path, source=asdict(source)))
        assert record.source == source
        _, folder = backend._locate(record.ref)
        assert "source" not in json.loads((folder / "record.json").read_text())
        assert "scm" in (folder / "conandata.yml").read_text()
        assert str(checkout) not in (folder / "conanfile.py").read_text()
        fetched = catalog.materialize(record.ref, with_sources=True)
        assert fetched.source_path is not None
        assert (fetched.source_path / "cpu.sv").is_file()
        with pytest.raises(IdentityConflict):
            catalog.publish_file(description(tmp_path, source=asdict(source)))
        (folder / "conandata.yml").write_text("scm: {commit: nope}")
        with pytest.raises(InvalidPackage):
            catalog.get(record.ref)


def test_dirty_source_fails_before_publication(tmp_path: Path, checkout: Path) -> None:
    (checkout / "cpu.sv").write_text("uncommitted")
    with Catalog(ConanBackend(tmp_path / "home")) as catalog:
        with pytest.raises(BackendUnavailable, match="dirty"):
            catalog.publish_file(description(tmp_path, source={"path": "checkout"}))
        assert not catalog.search()


@pytest.mark.parametrize(
    "fields",
    [
        {"source": [{"path": "."}, {"path": "."}]},
        {"source": {"path": ".", "repository_url": "mixed"}},
        {"source": {"repository_url": "repo", "resolved_commit": "main"}},
        {"source": {"path": "missing"}},
        {"source": {"repository_url": "$(unwanted)", "resolved_commit": "a" * 40}},
        {"source": {"path": "input.txt"}},
        {"files": {"../escape": "input.txt"}},
        {"files": {"/absolute": "input.txt"}},
        {"files": {"a\\b": "input.txt"}},
        {"files": {"a//b": "input.txt"}},
        {"files": {"c:/escape": "input.txt"}},
        {"files": {"name\0": "input.txt"}},
        {"files": {"a": "input.txt", "a/b": "input.txt"}},
        {"files": {".": "input.txt"}},
        {"files": {"missing": "missing"}},
        {"files": {"directory": "."}},
        {"dependencies": [{"name": "incomplete"}]},
        {"unknown": True},
    ],
)
def test_invalid_manifest_does_not_publish(
    packages: Catalog, tmp_path: Path, fields: dict[str, object]
) -> None:
    (tmp_path / "input.txt").write_text("input")
    with pytest.raises(InvalidPublication):
        packages.publish_file(description(tmp_path, **fields))
    assert not packages.search()


def test_invalid_json_and_missing_description(packages: Catalog, tmp_path: Path) -> None:
    path = tmp_path / "package.json"
    with pytest.raises(InvalidPublication):
        packages.publish_file(path)
    path.write_text("{")
    with pytest.raises(InvalidPublication):
        packages.publish_file(path)


def test_missing_content_and_record(packages: Catalog) -> None:
    record = Record(Ref("cpu", "1.0", "rtl", "soc"))
    with pytest.raises(NotFound):
        packages.materialize(record.ref)
    packages.publish(record)
    with pytest.raises(MaterializationError):
        packages.materialize(record.ref)


def test_source_is_not_in_binary_and_requires_git_on_first_retrieval(
    packages: Catalog, checkout: Path
) -> None:
    source = GitResource(str(checkout), "HEAD", _commit(checkout))
    record = Record(Ref("cpu", "1.0", "rtl", "soc"), source=source)
    packages.publish(record)
    shutil.rmtree(checkout)
    assert packages.get(record.ref) == record
    result = packages.materialize(record.ref)
    assert result.source_path is None
    assert not (result.path / "source").exists()
    with pytest.raises(CatalogError):
        packages.materialize(record.ref, with_sources=True)


def test_publishing_pinned_scm_does_not_contact_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def reject(*args: object, **kwargs: object) -> object:
        raise AssertionError("SCM publication must not clone or check out Git")

    monkeypatch.setattr(subprocess, "run", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)
    source = GitResource("https://gitlab.example.invalid/rtl/cpu.git", "v1", "a" * 40)
    record = Record(Ref("cpu", "1.0", "rtl", "soc"), source=source)
    with Catalog(ConanBackend(tmp_path / "home")) as catalog:
        catalog.publish(record)
        assert catalog.get(record.ref) == record
        package = catalog.materialize(record.ref)
        assert package.source_path is None
        assert not (package.path / "source").exists()


def _commit(checkout: Path) -> str:
    with Repo(checkout) as repo:
        return repo.head().decode()
