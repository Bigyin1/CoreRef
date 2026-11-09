"""Package files have SDK semantics; external paths remain consumer payload data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.records import InstalledRecord, LocalRecord, Record, Ref


def test_installed_paths_and_payload_are_detached_and_read_only(tmp_path: Path) -> None:
    paths = {"lib": str(tmp_path / "cpu.lib")}
    record = InstalledRecord(
        Record(
            Ref.from_fields("soc", "cpu", "rtl", "default", "1.0"),
            payload={"external": paths},
            file_names=("report",),
            has_source=True,
        ),
        source=tmp_path / "source",
        files={"report": tmp_path / "report.txt"},
    )
    paths.clear()
    output = json.loads(json.dumps(record.to_dict(), allow_nan=False))
    assert output["source"] == str(record.source)
    assert output["files"] == {"report": str(tmp_path / "report.txt")}
    assert record.payload == {"external": {"lib": str(tmp_path / "cpu.lib")}}
    assert record.files["report"] == tmp_path / "report.txt"
    assert record.source == tmp_path / "source"
    with pytest.raises(TypeError):
        record.files["other"] = tmp_path  # type: ignore[index]


@pytest.mark.parametrize("name,path", [("lib", Path("relative")), ("", Path("/data/lib"))])
def test_package_file_input_validation_belongs_to_caller(name: str, path: Path) -> None:
    record = LocalRecord(Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0"), files={name: path})
    assert record.files == {name: path}
    assert (
        InstalledRecord(Record(record.ref, file_names=(name,)), files=record.files).files
        == record.files
    )


@pytest.mark.parametrize("paths", [{"lib": "relative/path"}, {"": "/unmounted/data"}])
def test_external_paths_are_opaque_payload(catalog: Catalog, paths: dict[str, str]) -> None:
    local = LocalRecord(
        Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0"),
        payload={"resources": paths},
    )
    record = catalog.publish(local)
    assert catalog.get(record.ref).payload == {"resources": paths}
    assert not hasattr(local, "nfs")
    assert not hasattr(record, "nfs")
    assert "nfs" not in json.loads(local.to_json())
    assert "nfs" not in record.to_dict()
