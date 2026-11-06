"""Package files have SDK semantics; external paths remain consumer payload data."""

import json
from pathlib import Path

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.records import LocalRecord, Record, Ref


def test_paths_roundtrip_as_immutable_snapshots(tmp_path: Path, *, catalog: Catalog) -> None:
    paths = {"lib": str(tmp_path / "cpu.lib")}
    record = Record(
        Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
        payload={"external": paths},
        source=tmp_path / "source",
        files={"report": tmp_path / "report.txt"},
    )
    paths.clear()
    restored = Record.from_json(json.dumps(record.to_dict(), allow_nan=False), catalog=catalog)
    assert restored == record
    assert restored.payload == {"external": {"lib": str(tmp_path / "cpu.lib")}}
    assert restored.files["report"] == tmp_path / "report.txt"
    assert restored.source == tmp_path / "source"
    with pytest.raises(TypeError):
        restored.files["other"] = tmp_path  # type: ignore[index]


@pytest.mark.parametrize("name,path", [("lib", Path("relative")), ("", Path("/data/lib"))])
def test_package_file_input_validation_belongs_to_caller(
    catalog: Catalog, name: str, path: Path
) -> None:
    record = LocalRecord(
        Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog), files={name: path}
    )
    assert record.files == {name: path}
    assert Record(record.ref, files=record.files).files == record.files


@pytest.mark.parametrize("paths", [{"lib": "relative/path"}, {"": "/unmounted/data"}])
def test_external_paths_are_opaque_payload(catalog: Catalog, paths: dict[str, str]) -> None:
    local = LocalRecord(
        Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog), payload={"resources": paths}
    )
    record = catalog.publish(local)
    assert record.ref.get().payload == {"resources": paths}
    assert not hasattr(local, "nfs")
    assert not hasattr(record, "nfs")
    assert "nfs" not in json.loads(local.to_json())
    assert "nfs" not in record.to_dict()
