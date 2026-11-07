"""Metadata reads do not fetch binaries; installed records expose verified paths."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.local_server import LocalServer
from hwcatalog._internal.types import NativeGraph
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import InvalidPackage
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import InstalledRecord, LocalRecord, Record, Ref
from tests.test_models import UserPayload


def test_metadata_operations_leave_artifacts_on_the_server(
    catalog: Catalog, conan_server: LocalServer, tmp_path: Path
) -> None:
    artifact = tmp_path / "cpu.lib"
    artifact.write_bytes(b"package data" * 10000)
    leaf = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=catalog),
            files={"cpu.lib": artifact},
        )
    )
    parent = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "parent", "rtl", "default", "1.0", catalog=catalog),
        ),
        dependencies=(leaf.ref,),
    )
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    ref = Ref.from_fields(**leaf.ref.to_dict(), catalog=reader)
    with patch.object(reader._client, "run", wraps=reader._client.run) as commands:
        metadata = ref.info()
        assert type(metadata) is Record and metadata.file_names == ("cpu.lib",)
        assert not hasattr(metadata, "files") and not hasattr(metadata, "source")
        assert reader.search() == tuple(sorted((leaf, parent), key=lambda item: item.ref))
        assert Ref.from_fields(**parent.ref.to_dict(), catalog=reader).dependencies() == (metadata,)
        graph = Graph(
            reader,
            LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=reader)),
            dependencies=(parent.ref,),
        )
        assert metadata in graph.records
        assert not any(path.is_dir() for path in reader._client.home.rglob("artifacts"))
        assert not any(call.args[0] == "install" for call in commands.call_args_list)
        ready = graph.get((ref,))[0]
        assert isinstance(ready, InstalledRecord)
        assert ready.files["cpu.lib"].read_bytes() == artifact.read_bytes()
        assert graph.get((ref,))[0] is ready
        assert sum(call.args[0] == "install" for call in commands.call_args_list) == 1
        assert type(next(item for item in graph.records if item.ref == ref)) is Record


def test_exported_metadata_has_digests_and_no_artifact_bytes(
    catalog: Catalog, tmp_path: Path
) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"contents that belong only to the package")
    ref = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=catalog)
    first = catalog.publish(LocalRecord(ref, files={"result.bin": artifact}))
    folder = catalog._package(first.ref).recipe().directory
    value = json.loads((folder / "record.json").read_text())
    assert not (folder / "artifacts").exists()
    assert value["file_digests"] == {
        "result.bin": hashlib.sha256(artifact.read_bytes()).hexdigest()
    }
    relocated = tmp_path / "another.bin"
    relocated.write_bytes(artifact.read_bytes())
    assert catalog.publish(LocalRecord(ref, files={"result.bin": relocated})).ref == first.ref
    relocated.write_bytes(b"new contents")
    second = catalog.publish(LocalRecord(ref, files={"result.bin": relocated}))
    assert first.ref != second.ref
    assert first.ref.get().files["result.bin"].read_bytes() == artifact.read_bytes()
    assert second.files["result.bin"].read_bytes() == b"new contents"


def test_source_presence_and_source_readiness_are_distinct(
    catalog: Catalog, checkout: Path
) -> None:
    record = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
            source=checkout,
        )
    )
    metadata = record.ref.info()
    assert type(metadata) is Record and metadata.has_source
    assert record.has_source and record.source is None and not record.sources_ready
    fetched = record.ref.get_with_sources()
    assert fetched.has_source and fetched.source is not None and fetched.sources_ready
    assert (fetched.source / "cpu.sv").is_file()
    empty = catalog.publish(
        LocalRecord(Ref.from_fields("soc", "empty", "be", "lib-lef", "1.0", catalog=catalog))
    )
    assert not empty.has_source and empty.source is None and empty.sources_ready
    restored = Record.from_json(json.dumps(fetched.to_dict()), catalog=catalog)
    assert isinstance(restored, InstalledRecord) and restored.sources_ready
    assert restored.source == fetched.source


def test_missing_package_file_is_not_reported_as_installed(
    catalog: Catalog, tmp_path: Path
) -> None:
    artifact = tmp_path / "file"
    artifact.write_text("contents")
    record = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=catalog),
            files={"file": artifact},
        )
    )
    record.files["file"].unlink()
    assert record.ref.info().file_names == ("file",)
    with pytest.raises(InvalidPackage, match="missing a declared file"):
        record.ref.get()


@pytest.mark.parametrize(
    "attribute", ["ref", "payload", "file_names", "has_source", "files", "source"]
)
def test_metadata_and_installed_fields_are_read_only(catalog: Catalog, attribute: str) -> None:
    metadata = Record(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), {"value": [1]}
    )
    ready = InstalledRecord(metadata)
    if attribute in ("ref", "payload", "file_names", "has_source"):
        with pytest.raises(AttributeError):
            setattr(metadata, attribute, None)
    with pytest.raises(AttributeError):
        setattr(ready, attribute, None)
    assert ready == metadata and hash(ready) == hash(metadata)


def test_installed_record_requires_the_declared_contents(catalog: Catalog) -> None:
    ref = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=catalog)
    with pytest.raises(ValueError, match="declared file names"):
        InstalledRecord(Record(ref, file_names=("missing",)))
    with pytest.raises(ValueError, match="no SCM source"):
        InstalledRecord(Record(ref), source=Path("/source"))


def test_installation_reuses_the_immutable_payload(catalog: Catalog) -> None:
    payload = UserPayload({"nested": [1, 2]})
    metadata = Record(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog), payload
    )
    ready = InstalledRecord(metadata)
    assert payload.calls == 1
    assert ready.payload is metadata.payload
    malformed = metadata.to_dict() | {"source": "/source"}
    with pytest.raises(ValueError, match="must include its files mapping"):
        Record.from_json(json.dumps(malformed), catalog=catalog)


def test_incomplete_package_is_rejected_before_upload(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "result"
    artifact.write_text("contents")
    export = catalog._client.export_package

    def incomplete(directory: Path, *, lockfile: Path | None = None) -> NativeGraph:
        native = export(directory, lockfile=lockfile)
        folder = native.root.conanfile.package_folder
        assert folder is not None
        (Path(folder) / "artifacts/result").unlink()
        return native

    monkeypatch.setattr(catalog._client, "export_package", incomplete)
    with patch.object(catalog._client, "upload", wraps=catalog._client.upload) as upload:
        with pytest.raises(InvalidPackage, match="missing a declared file"):
            catalog.publish(
                LocalRecord(
                    Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0", catalog=catalog),
                    files={"result": artifact},
                )
            )
        upload.assert_not_called()
    assert catalog.search() == ()
