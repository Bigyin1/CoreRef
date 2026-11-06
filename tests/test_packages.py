"""Direct package publication, stored artifacts and Conan SCM capture."""

import json
import shutil
from pathlib import Path

import pytest
from dulwich.repo import Repo

from examples.conan_config import configured_home
from hwcatalog._internal.conan import records as conan_records
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from hwcatalog.api.graph import Dependency
from hwcatalog.api.records import Record, Ref
from hwcatalog.api.resources import GitResource
from tests.helpers import resolve_published


@pytest.fixture
def packages(tmp_path: Path) -> Catalog:
    return Catalog(configured_home(tmp_path / "home"))


def test_publish_dependencies_and_stored_files(packages: Catalog, tmp_path: Path) -> None:
    rtl = packages.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
    artifact = tmp_path / "cpu.lib"
    artifact.write_bytes(b"library(cpu) {}\n\x00")
    record = packages.publish(
        Record(Ref("soc", "cpu", "be", "lib-lef", "4.0"), {"corner": "tt"}),
        dependencies=(rtl.ref,),
        files={"timing/cpu.lib": artifact},
    )
    assert record.files == ("timing/cpu.lib",)
    assert record.source is None
    assert record.ref.get() == record
    assert record.ref.dependencies() == (rtl,)
    assert resolve_published(packages, record.ref).dependencies == (
        Dependency(record.ref, rtl.ref),
    )
    artifact.unlink()
    content = record.ref.materialize().path
    assert (content / "artifacts/timing/cpu.lib").read_bytes() == b"library(cpu) {}\n\x00"
    assert record.ref.materialize().path == content
    with pytest.raises(InvalidPackage):
        packages.publish(record)


def test_local_source_is_captured_once_outside_payload(packages: Catalog, checkout: Path) -> None:
    record = packages.publish(
        Record(Ref("soc", "cpu", "rtl", "default", "1.0"), {"tool": "synthesis"}),
        source_path=checkout,
    )
    assert record.source is not None
    with Repo(checkout) as repository:
        assert record.source.resolved_commit == repository.head().decode()
    assert record.payload == {"tool": "synthesis"}
    assert record.ref.get() == record
    artifacts = record.ref.materialize()
    assert artifacts.source_path is None
    assert not (artifacts.path / "source").exists()
    fetched = record.ref.materialize(with_sources=True)
    assert fetched.source_path is not None
    assert (fetched.source_path / "cpu.sv").read_text() == "module cpu; endmodule\n"
    assert not fetched.source_path.is_relative_to(fetched.path)
    shutil.rmtree(checkout)
    assert record.ref.materialize(with_sources=True).source_path == fetched.source_path
    assert Record.from_json(record.to_json()) == record


def test_pinned_source_round_trip_uses_conan_scm(tmp_path: Path, checkout: Path) -> None:
    source = GitResource(str(checkout), "main", _commit(checkout))
    catalog = Catalog(configured_home(tmp_path / "home"))
    description = Record(Ref("soc", "cpu", "rtl", "default", "1.0"), source=source)
    record = catalog.publish(description)
    assert record.source == source
    _, folder = conan_records.locate(catalog._client, record.ref)
    assert "exports_sources" not in (folder / "conanfile.py").read_text()
    assert "source" not in json.loads((folder / "record.json").read_text())
    assert "scm" in (folder / "conandata.yml").read_text()
    assert str(checkout) not in (folder / "conanfile.py").read_text()
    fetched = record.ref.materialize(with_sources=True)
    assert fetched.source_path is not None
    assert (fetched.source_path / "cpu.sv").is_file()
    with pytest.raises(Conflict):
        catalog.publish(description)
    (folder / "conandata.yml").write_text("scm: {commit: nope}")
    with pytest.raises(InvalidPackage):
        record.ref.get()


def test_dirty_source_fails_before_publication(tmp_path: Path, checkout: Path) -> None:
    (checkout / "cpu.sv").write_text("uncommitted")
    catalog = Catalog(configured_home(tmp_path / "home"))
    with pytest.raises(CatalogError, match="dirty"):
        catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")), source_path=checkout)
    assert not catalog.search()


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a\\b", "a//b", "c:/escape", "name\0", "."]
)
def test_invalid_artifact_names_do_not_publish(
    packages: Catalog, tmp_path: Path, name: str
) -> None:
    artifact = tmp_path / "input.txt"
    artifact.write_text("input")
    with pytest.raises(InvalidPackage):
        packages.publish(Record(Ref("soc", "cpu", "be", "lib-lef", "4.0")), files={name: artifact})
    assert not packages.search()


@pytest.mark.parametrize("problem", ["missing", "directory", "overlap", "mismatch"])
def test_invalid_artifact_inputs_do_not_publish(
    packages: Catalog, tmp_path: Path, problem: str
) -> None:
    artifact = tmp_path / "input.txt"
    artifact.write_text("input")
    record = Record(Ref("soc", "cpu", "be", "lib-lef", "4.0"))
    files = {"a": artifact}
    if problem == "missing":
        files = {"a": tmp_path / "missing"}
    elif problem == "directory":
        files = {"a": tmp_path}
    elif problem == "overlap":
        files["a/b"] = artifact
    else:
        record = Record(record.ref, files=("different",))
    with pytest.raises(InvalidPackage):
        packages.publish(record, files=files)
    assert not packages.search()


@pytest.mark.parametrize("problem", ["missing", "file", "both-sources"])
def test_invalid_source_inputs_do_not_publish(
    packages: Catalog, tmp_path: Path, problem: str
) -> None:
    source_path = tmp_path / "checkout"
    record = Record(Ref("soc", "cpu", "rtl", "default", "1.0"))
    if problem == "file":
        source_path.write_text("not a checkout")
    elif problem == "both-sources":
        record = Record(
            record.ref, source=GitResource("https://example.invalid/cpu.git", "v1", "a" * 40)
        )
    with pytest.raises(InvalidPackage):
        packages.publish(record, source_path=source_path)
    assert not packages.search()


def test_missing_content_and_record(packages: Catalog) -> None:
    record = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=packages))
    with pytest.raises(NotFound):
        record.ref.materialize()
    packages.publish(record)
    with pytest.raises(CatalogError):
        record.ref.materialize()


def test_source_is_not_in_binary_and_requires_git_on_first_retrieval(
    packages: Catalog, checkout: Path
) -> None:
    source = GitResource(str(checkout), "HEAD", _commit(checkout))
    record = packages.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0"), source=source))
    shutil.rmtree(checkout)
    assert record.ref.get() == record
    result = record.ref.materialize()
    assert result.source_path is None
    assert not (result.path / "source").exists()
    with pytest.raises(CatalogError):
        record.ref.materialize(with_sources=True)


def test_publishing_pinned_scm_does_not_contact_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def reject(*args: object, **kwargs: object) -> object:
        raise AssertionError("SCM publication must not clone or check out Git")

    monkeypatch.setattr(subprocess, "run", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)
    source = GitResource("https://gitlab.example.invalid/rtl/cpu.git", "v1", "a" * 40)
    record = Record(Ref("soc", "cpu", "rtl", "default", "1.0"), source=source)
    catalog = Catalog(configured_home(tmp_path / "home"))
    record = catalog.publish(record)
    assert record.ref.get() == record
    package = record.ref.materialize()
    assert package.source_path is None
    assert not (package.path / "source").exists()


def _commit(checkout: Path) -> str:
    with Repo(checkout) as repo:
        return repo.head().decode()
