"""Local inputs and registry snapshots expose different operations and paths."""

from pathlib import Path

import pytest

from hwcatalog._internal.recipe import LocalRecipe, StoredRecipe
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import InvalidPackage
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import LocalRecord, Record, Ref


def test_files_are_available_on_fetch_and_source_is_optional(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    local = LocalRecord(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
        payload={"lef": str(tmp_path / "mounted/cpu.lef")},
        source=checkout,
        files={"timing.lib": artifact},
    )
    published = catalog.publish(local)
    assert isinstance(published, Record)
    assert not hasattr(published, "scm")
    assert "scm" not in published.to_dict()
    assert published.files["timing.lib"].read_text() == artifact.read_text()
    fetched = published.ref.get()
    assert fetched.files["timing.lib"].read_text() == artifact.read_text()
    materialized = fetched.ref.get_with_sources()
    assert isinstance(materialized, Record)
    assert materialized.source is not None
    assert (materialized.source / "cpu.sv").is_file()
    assert materialized.files["timing.lib"].read_text() == artifact.read_text()
    assert materialized.payload == local.payload
    assert not hasattr(local, "materialize")
    assert not hasattr(materialized, "materialize")


def test_payload_only_record_does_not_install_a_binary(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = {"lib": str(tmp_path / "not-mounted-yet/cpu.lib")}
    record = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog),
            payload={"paths": paths},
        )
    )
    operations: list[str] = []
    run = catalog._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments[0])
        return run(*arguments)

    monkeypatch.setattr(catalog._client, "run", track)
    for fetch in (record.ref.get, record.ref.get_with_sources):
        local = fetch()
        assert local.payload == {"paths": paths}
        assert local.files == {}
        assert local.source is None
    assert "install" not in operations


def test_published_record_is_not_a_local_publication_input(catalog: Catalog) -> None:
    record = Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    with pytest.raises(InvalidPackage, match="LocalRecord"):
        catalog.publish(record)  # type: ignore[arg-type]  # Deliberate contract misuse.
    assert catalog.search(SearchQuery()) == ()


def test_local_metadata_uses_names_and_record_decodes_package_paths(
    tmp_path: Path, *, catalog: Catalog
) -> None:
    local = LocalRecord(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
        payload={"lef": str(tmp_path / "nfs/cpu.lef")},
        source=tmp_path / "working-tree",
        files={"timing/cpu.lib": tmp_path / "output.lib"},
    )
    metadata = local.to_json()
    assert "working-tree" not in metadata
    assert "output.lib" not in metadata
    with pytest.raises(ValueError, match="Snapshot files"):
        Record.from_json(metadata, catalog=catalog)
    local.source = None
    LocalRecipe(local).write(tmp_path / "downloaded")
    record = StoredRecipe(tmp_path / "downloaded", local.ref, reference=str(local.ref)).record
    assert record.file_names == ("timing/cpu.lib",)
    assert not hasattr(record, "files")
    assert record.payload == local.payload
    assert not record.has_source


@pytest.mark.parametrize("field", ["source", "source_path", "scm"])
def test_package_source_metadata_belongs_to_conan(
    tmp_path: Path, field: str, *, catalog: Catalog
) -> None:
    import json

    local = LocalRecord(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    value = json.loads(local.to_json())
    value[field] = "unexpected"
    LocalRecipe(local).write(tmp_path)
    (tmp_path / "record.json").write_text(json.dumps(value))
    with pytest.raises(InvalidPackage, match="Conan SCM"):
        StoredRecipe(tmp_path, local.ref, reference=str(local.ref))
