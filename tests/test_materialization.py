"""Local inputs and registry snapshots expose different operations and paths."""

from pathlib import Path

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import InvalidPackage
from hwcatalog.api.records import LocalRecord, Record, Ref


def test_files_are_available_on_fetch_and_scm_is_optional(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    local = LocalRecord(
        Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
        source=checkout,
        files={"timing.lib": artifact},
        nfs={"lef": tmp_path / "mounted/cpu.lef"},
    )
    published = catalog.publish(local)
    assert isinstance(published, Record)
    assert published.scm is not None
    assert published.files["timing.lib"].read_text() == artifact.read_text()
    fetched = published.ref.get()
    assert fetched.files["timing.lib"].read_text() == artifact.read_text()
    materialized = fetched.ref.get(with_sources=True)
    assert isinstance(materialized, Record)
    assert materialized.source is not None
    assert (materialized.source / "cpu.sv").is_file()
    assert materialized.files["timing.lib"].read_text() == artifact.read_text()
    assert materialized.nfs == local.nfs
    assert not hasattr(local, "materialize")
    assert not hasattr(materialized, "materialize")


def test_nfs_only_materialization_does_not_install_a_binary(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = {"lib": tmp_path / "not-mounted-yet/cpu.lib"}
    record = catalog.publish(
        LocalRecord(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog), nfs=paths)
    )
    operations: list[str] = []
    run = catalog._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments[0])
        return run(*arguments)

    monkeypatch.setattr(catalog._client, "run", track)
    local = record.ref.get()
    assert local.nfs == paths
    assert local.files == {}
    assert local.source is None
    assert "install" not in operations


def test_published_record_is_not_a_local_publication_input(catalog: Catalog) -> None:
    record = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    with pytest.raises(InvalidPackage, match="LocalRecord"):
        catalog.publish(record)  # type: ignore[arg-type]  # Deliberate contract misuse.
    assert catalog.search() == ()


def test_local_metadata_uses_names_and_record_decodes_package_paths(
    tmp_path: Path, *, catalog: Catalog
) -> None:
    local = LocalRecord(
        Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
        source=tmp_path / "working-tree",
        files={"timing/cpu.lib": tmp_path / "output.lib"},
        nfs={"lef": tmp_path / "nfs/cpu.lef"},
    )
    metadata = local.to_json()
    assert "working-tree" not in metadata
    assert "output.lib" not in metadata
    with pytest.raises(ValueError, match="Package directory"):
        Record.from_json(metadata, catalog=catalog)
    record = Record.from_json(metadata, package_dir=tmp_path / "downloaded", catalog=catalog)
    assert record.files == {"timing/cpu.lib": tmp_path / "downloaded/artifacts/timing/cpu.lib"}
    assert record.nfs == local.nfs
    assert record.source is None


@pytest.mark.parametrize("field", ["source", "source_path", "scm"])
def test_package_source_metadata_belongs_to_conan(
    tmp_path: Path, field: str, *, catalog: Catalog
) -> None:
    import json

    local = LocalRecord(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    value = json.loads(local.to_json())
    value[field] = "unexpected"
    with pytest.raises(ValueError, match="Conan SCM"):
        Record.from_json(json.dumps(value), package_dir=tmp_path, catalog=catalog)
