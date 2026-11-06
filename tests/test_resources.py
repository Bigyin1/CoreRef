"""Named filesystem paths stay outside consumer payloads."""

import json
from pathlib import Path

import pytest

from hwcatalog._internal.validation import normalize_record
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import InvalidPackage
from hwcatalog.api.records import LocalRecord, Record, Ref


def test_paths_roundtrip_as_immutable_snapshots(tmp_path: Path, *, catalog: Catalog) -> None:
    nfs = {"lib": tmp_path / "cpu.lib"}
    record = normalize_record(
        Record(
            Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
            source=tmp_path / "source",
            files={"report": tmp_path / "report.txt"},
            nfs=nfs,
        )
    )
    nfs.clear()
    restored = Record.from_json(json.dumps(record.to_dict(), allow_nan=False), catalog=catalog)
    assert restored == record
    assert restored.nfs["lib"] == tmp_path / "cpu.lib"
    assert restored.files["report"] == tmp_path / "report.txt"
    assert restored.source == tmp_path / "source"
    with pytest.raises(TypeError):
        restored.nfs["other"] = tmp_path  # type: ignore[index]


@pytest.mark.parametrize("name,path", [("lib", Path("relative")), ("", Path("/data/lib"))])
def test_nfs_paths_require_names_and_absolute_paths(
    catalog: Catalog, name: str, path: Path
) -> None:
    record = LocalRecord(
        Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog), nfs={name: path}
    )
    with pytest.raises(InvalidPackage):
        catalog.publish(record)
