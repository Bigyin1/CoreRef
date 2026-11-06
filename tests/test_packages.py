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
from tests.helpers import local_record, resolve_published


@pytest.fixture
def packages(tmp_path: Path) -> Catalog:
    return Catalog(configured_home(tmp_path / "home"))


def test_publish_dependencies_and_stored_files(packages: Catalog, tmp_path: Path) -> None:
    rtl = packages.publish(
        local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=packages)))
    )
    artifact = tmp_path / "cpu.lib"
    artifact.write_bytes(b"library(cpu) {}\n\x00")
    record = packages.publish(
        local_record(
            Record(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=packages), {"corner": "tt"}),
            files={"timing/cpu.lib": artifact},
        ),
        dependencies=(rtl.ref,),
    )
    assert tuple(record.files) == ("timing/cpu.lib",)
    assert record.source is None
    assert record.ref.get() == record
    assert record.ref.dependencies() == (rtl,)
    assert resolve_published(packages, record.ref).dependencies == (
        Dependency(record.ref, rtl.ref),
    )
    artifact.unlink()
    content = record.ref.get().files["timing/cpu.lib"]
    assert content.read_bytes() == b"library(cpu) {}\n\x00"
    assert record.ref.get().files["timing/cpu.lib"] == content
    with pytest.raises(Conflict):
        packages.publish(local_record(record))


def test_local_source_is_captured_once_outside_payload(packages: Catalog, checkout: Path) -> None:
    record = packages.publish(
        local_record(
            Record(
                Ref("soc", "cpu", "rtl", "default", "1.0", catalog=packages), {"tool": "synthesis"}
            ),
            source=checkout,
        )
    )
    assert record.scm is not None
    with Repo(checkout) as repository:
        assert record.scm["commit"] == repository.head().decode()
    assert record.payload == {"tool": "synthesis"}
    assert record.ref.get() == record
    artifacts = record.ref.get()
    assert artifacts.source is None
    assert artifacts.files == {}
    fetched = record.ref.get(with_sources=True)
    assert fetched.source is not None
    assert (fetched.source / "cpu.sv").read_text() == "module cpu; endmodule\n"
    assert fetched.source.is_relative_to(packages._client.home)
    shutil.rmtree(checkout)
    assert record.ref.get(with_sources=True).source == fetched.source
    assert (
        Record.from_json(json.dumps(record.to_dict(), allow_nan=False), catalog=packages) == record
    )


def test_pinned_source_round_trip_uses_conan_scm(
    tmp_path: Path, checkout: Path, *, catalog: Catalog
) -> None:
    source = {"url": str(checkout), "requested_ref": "main", "commit": _commit(checkout)}
    catalog = Catalog(configured_home(tmp_path / "home"))
    description = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), scm=source)
    record = catalog.publish(local_record(description, source=checkout))
    assert record.scm is not None
    assert record.scm["commit"] == source["commit"]
    _, folder = conan_records.locate(catalog._client, record.ref)
    assert "exports_sources" not in (folder / "conanfile.py").read_text()
    assert "source" not in json.loads((folder / "record.json").read_text())
    assert "scm" in (folder / "conandata.yml").read_text()
    assert str(checkout) not in (folder / "conanfile.py").read_text()
    fetched = record.ref.get(with_sources=True)
    assert fetched.source is not None
    assert (fetched.source / "cpu.sv").is_file()
    with pytest.raises(Conflict):
        catalog.publish(local_record(description))
    (folder / "conandata.yml").write_text("scm: {commit: nope}")
    with pytest.raises(InvalidPackage):
        record.ref.get()


def test_dirty_source_fails_before_publication(
    tmp_path: Path, checkout: Path, *, catalog: Catalog
) -> None:
    (checkout / "cpu.sv").write_text("uncommitted")
    catalog = Catalog(configured_home(tmp_path / "home"))
    with pytest.raises(CatalogError, match="dirty"):
        catalog.publish(
            local_record(
                Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)), source=checkout
            )
        )
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
        packages.publish(
            local_record(
                Record(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=packages)),
                files={name: artifact},
            )
        )
    assert not packages.search()


@pytest.mark.parametrize("problem", ["missing", "directory", "overlap"])
def test_invalid_artifact_inputs_do_not_publish(
    packages: Catalog, tmp_path: Path, problem: str
) -> None:
    artifact = tmp_path / "input.txt"
    artifact.write_text("input")
    record = Record(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=packages))
    files = {"a": artifact}
    if problem == "missing":
        files = {"a": tmp_path / "missing"}
    elif problem == "directory":
        files = {"a": tmp_path}
    elif problem == "overlap":
        files["a/b"] = artifact
    with pytest.raises(InvalidPackage):
        packages.publish(local_record(record, files=files))
    assert not packages.search()


@pytest.mark.parametrize("problem", ["missing", "file"])
def test_invalid_source_inputs_do_not_publish(
    packages: Catalog, tmp_path: Path, problem: str
) -> None:
    source_path = tmp_path / "checkout"
    record = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=packages))
    if problem == "file":
        source_path.write_text("not a checkout")
    with pytest.raises(InvalidPackage):
        packages.publish(local_record(record, source=source_path))
    assert not packages.search()


def test_missing_content_and_record(packages: Catalog) -> None:
    record = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=packages))
    with pytest.raises(NotFound):
        record.ref.get()
    packages.publish(local_record(record))
    assert record.ref.get().files == {}


def test_source_is_not_in_binary_and_requires_git_on_first_retrieval(
    packages: Catalog, checkout: Path
) -> None:
    record = packages.publish(
        local_record(
            Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=packages)), source=checkout
        )
    )
    shutil.rmtree(checkout)
    assert record.ref.get() == record
    result = record.ref.get()
    assert result.source is None
    assert result.files == {}
    with pytest.raises(CatalogError):
        record.ref.get(with_sources=True)


def _commit(checkout: Path) -> str:
    with Repo(checkout) as repo:
        return repo.head().decode()
