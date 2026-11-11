"""Metadata reads do not fetch binaries; installed records expose verified paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from coreref._internal.types import NativeGraph
from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import InstalledRecord, LocalRecord, Record, Ref
from coreref.testing import LocalServer
from tests.test_models import UserPayload


def test_metadata_operations_leave_artifacts_on_the_server(
    catalog: Catalog, conan_server: LocalServer, tmp_path: Path
) -> None:
    artifact = tmp_path / "cpu.lib"
    artifact.write_bytes(b"package data" * 10000)
    leaf = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0"),
            files={"cpu.lib": artifact},
        )
    )
    parent = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "parent", "rtl", "default", "1.0"),
        ),
        dependencies=(leaf.ref,),
    )
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    ref = Ref.from_fields(**leaf.ref.to_dict())
    with patch.object(reader._client, "run", wraps=reader._client.run) as commands:
        metadata = reader.info(ref)
        assert type(metadata) is Record and metadata.file_names == ("cpu.lib",)
        assert not hasattr(metadata, "files") and not hasattr(metadata, "source")
        assert reader.search(SearchQuery()) == tuple(
            sorted((leaf, parent), key=lambda item: item.ref)
        )
        assert Graph(reader, Ref.from_fields(**parent.ref.to_dict())).dependencies() == (metadata,)
        graph = Graph(
            reader,
            LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0")),
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


@pytest.mark.parametrize(
    "contents",
    [b"", b"contents that belong only to the package", b"x" * (2 * 1024 * 1024 + 1)],
    ids=["empty", "small", "multiple-chunks"],
)
def test_exported_metadata_has_digests_and_no_artifact_bytes(
    catalog: Catalog, tmp_path: Path, contents: bytes
) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(contents)
    ref = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0")
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
    assert catalog.get(first.ref).files["result.bin"].read_bytes() == artifact.read_bytes()
    assert second.files["result.bin"].read_bytes() == b"new contents"


def test_source_presence_and_source_readiness_are_distinct(
    catalog: Catalog, checkout: Path
) -> None:
    record = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "rtl", "default", "1.0"),
            source=checkout,
        )
    )
    metadata = catalog.info(record.ref)
    assert type(metadata) is Record and metadata.has_source
    assert record.has_source and record.source is None and not record.sources_ready
    fetched = catalog.get_with_sources(record.ref)
    assert fetched.has_source and fetched.source is not None and fetched.sources_ready
    assert (fetched.source / "cpu.sv").is_file()
    empty = catalog.publish(LocalRecord(Ref.from_fields("soc", "empty", "be", "lib-lef", "1.0")))
    assert not empty.has_source and empty.source is None and empty.sources_ready
    output = json.loads(json.dumps(fetched.to_dict()))
    assert output["source"] == str(fetched.source)


def test_missing_package_file_is_not_reported_as_installed(
    catalog: Catalog, tmp_path: Path
) -> None:
    artifact = tmp_path / "file"
    artifact.write_text("contents")
    record = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0"),
            files={"file": artifact},
        )
    )
    record.files["file"].unlink()
    assert catalog.info(record.ref).file_names == ("file",)
    with pytest.raises(InvalidPackage, match="missing a declared file"):
        catalog.get(record.ref)


@pytest.mark.parametrize(
    "attribute", ["ref", "payload", "file_names", "has_source", "files", "source"]
)
def test_metadata_and_installed_fields_are_read_only(attribute: str) -> None:
    metadata = Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0"), {"value": [1]})
    ready = InstalledRecord(metadata)
    if attribute in ("ref", "payload", "file_names", "has_source"):
        with pytest.raises(AttributeError):
            setattr(metadata, attribute, None)
    with pytest.raises(AttributeError):
        setattr(ready, attribute, None)
    assert ready == metadata and hash(ready) == hash(metadata)


def test_installed_record_requires_the_declared_contents() -> None:
    ref = Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0")
    with pytest.raises(ValueError, match="declared file names"):
        InstalledRecord(Record(ref, file_names=("missing",)))
    with pytest.raises(ValueError, match="no SCM source"):
        InstalledRecord(Record(ref), source=Path("/source"))


def test_installation_reuses_the_immutable_payload() -> None:
    payload = UserPayload({"nested": [1, 2]})
    metadata = Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0"), payload)
    ready = InstalledRecord(metadata)
    assert payload.calls == 1
    assert ready.payload is metadata.payload


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
                    Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0"),
                    files={"result": artifact},
                )
            )
        upload.assert_not_called()
    assert catalog.search(SearchQuery()) == ()
