"""Direct package publication, stored artifacts and Conan SCM capture."""

import json
import shutil
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from dulwich.repo import Repo

from examples.conan_config import configured_home
from hwcatalog._internal.recipe import LocalRecipe, StoredRecipe
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from hwcatalog.api.graph import Dependency
from hwcatalog.api.records import LocalRecord, Record, Ref
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
    assert record.payload == {"tool": "synthesis"}
    assert record.ref.get() == record
    artifacts = record.ref.get()
    assert artifacts.source is None
    assert artifacts.files == {}
    fetched = record.ref.get_with_sources()
    assert fetched.source is not None
    assert _commit(fetched.source) == _commit(checkout)
    assert (fetched.source / "cpu.sv").read_text() == "module cpu; endmodule\n"
    assert fetched.source.is_relative_to(packages._client.home)
    shutil.rmtree(checkout)
    assert record.ref.get_with_sources().source == fetched.source
    assert (
        Record.from_json(json.dumps(record.to_dict(), allow_nan=False), catalog=packages) == record
    )


def test_pinned_source_round_trip_uses_conan_scm(
    tmp_path: Path, checkout: Path, *, catalog: Catalog
) -> None:
    catalog = Catalog(configured_home(tmp_path / "home"))
    description = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    record = catalog.publish(local_record(description, source=checkout))
    folder = catalog._package(record.ref).recipe().directory
    assert "exports_sources" not in (folder / "conanfile.py").read_text()
    assert "source" not in json.loads((folder / "record.json").read_text())
    assert "scm" not in json.loads((folder / "record.json").read_text())
    assert "scm" in (folder / "conandata.yml").read_text()
    assert str(checkout) not in (folder / "conanfile.py").read_text()
    fetched = record.ref.get_with_sources()
    assert fetched.source is not None
    assert (fetched.source / "cpu.sv").is_file()
    with pytest.raises(Conflict):
        catalog.publish(local_record(description))


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


@pytest.mark.parametrize("name", ["../escape", "/absolute"])
def test_copy_cannot_escape_package_directory(packages: Catalog, tmp_path: Path, name: str) -> None:
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
        record.ref.get_with_sources()


def _commit(checkout: Path) -> str:
    with Repo(checkout) as repo:
        return repo.head().decode()


def test_requirements_and_scm_share_one_exported_conandata(
    catalog: Catalog, checkout: Path
) -> None:
    dependency = catalog.publish(
        LocalRecord(Ref("soc", "leaf", "rtl", "default", "1.0", catalog=catalog))
    )
    parent = catalog.publish(
        LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), source=checkout),
        dependencies=(dependency.ref,),
    )
    recipe = catalog._package(parent.ref).recipe()
    record = json.loads((recipe.directory / "record.json").read_text())
    data = yaml.safe_load((recipe.directory / "conandata.yml").read_text())
    assert record["format"] == 6
    assert set(record) == {"format", "ref", "payload"}
    assert not (recipe.directory / "hwcatalog.json").exists()
    assert set(data) == {"requires", "scm"}
    assert tuple(data["requires"]) == recipe.requirements
    assert data["requires"][0].startswith(f"{dependency.ref}#")
    assert data["scm"]["commit"] == _commit(checkout)
    assert recipe.has_source
    assert parent.ref.dependencies() == (dependency,)
    assert "requires =" not in (recipe.directory / "conanfile.py").read_text()
    assert parent.ref.get_with_sources().source is not None


def test_conandata_fields_are_read_without_an_sdk_schema(catalog: Catalog, tmp_path: Path) -> None:
    record = LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    LocalRecipe(record).write(tmp_path)
    data = {
        "requires": ["ordinary-library/1.0"],
        "scm": {"url": "managed-by-conan", "commit": "main", "extra": {"option": True}},
        "patches": [],
    }
    (tmp_path / "conandata.yml").write_text(yaml.safe_dump(data))
    recipe = StoredRecipe(tmp_path, record.ref, reference=str(record.ref))
    assert recipe.requirements == ("ordinary-library/1.0",)
    assert recipe.has_source


@pytest.mark.parametrize("scm", [None, {}])
def test_absent_or_empty_scm_does_not_request_sources(
    catalog: Catalog, tmp_path: Path, scm: dict[str, object] | None
) -> None:
    record = LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    LocalRecipe(record).write(tmp_path)
    (tmp_path / "conandata.yml").write_text(yaml.safe_dump({"scm": scm}))
    assert not StoredRecipe(tmp_path, record.ref, reference=str(record.ref)).has_source


def test_conan_reports_missing_scm_coordinates_when_sources_are_requested(
    catalog: Catalog, checkout: Path
) -> None:
    record = catalog.publish(
        LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), source=checkout)
    )
    folder = catalog._package(record.ref).recipe().directory
    (folder / "conandata.yml").write_text(yaml.safe_dump({"scm": {"commit": "HEAD"}}))
    assert record.ref.get() == record
    with pytest.raises(CatalogError, match="source"):
        record.ref.get_with_sources()


def test_conan_reports_invalid_requires_during_resolution(catalog: Catalog, tmp_path: Path) -> None:
    record = LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    LocalRecipe(record, ("not-a-reference",)).write(tmp_path)
    assert StoredRecipe(tmp_path, record.ref, reference=str(record.ref)).requirements == (
        "not-a-reference",
    )
    with pytest.raises(CatalogError, match="require"):
        catalog._client.graph(tmp_path)


def test_exported_recipe_omits_publisher_paths_and_has_stable_revision(
    catalog: Catalog, checkout: Path, tmp_path: Path, *, git_origin: Path
) -> None:
    relocated = tmp_path / "publisher's rtl checkout"
    shutil.copytree(checkout, relocated)
    ref = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    first = tmp_path / "first-recipe"
    second = tmp_path / "second-recipe"
    LocalRecipe(LocalRecord(ref, source=checkout)).write(first)
    LocalRecipe(LocalRecord(ref, source=relocated)).write(second)
    assert str(relocated) not in (second / "conanfile.py").read_text()
    assert (second / "source-path.txt").read_text() == str(relocated)
    assert (
        catalog._client.export_package(first).root.ref
        == catalog._client.export_package(second).root.ref
    )
    stored = catalog._package(ref).recipe()
    assert "package_type" not in (stored.directory / "conanfile.py").read_text()
    assert str(checkout) not in (stored.directory / "conanfile.py").read_text()
    assert str(relocated) not in (stored.directory / "conanfile.py").read_text()
    assert not (stored.directory / "source-path.txt").exists()
    assert stored.has_source and git_origin.is_dir()
    shutil.rmtree(checkout)
    shutil.rmtree(relocated)
    fetched = ref.get_with_sources()
    assert fetched.source is not None and (fetched.source / "cpu.sv").is_file()
