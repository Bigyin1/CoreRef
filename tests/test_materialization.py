"""External resource paths remain independent of Conan package installation."""

from pathlib import Path

import pytest

from hwcatalog.api.errors import MaterializationError
from hwcatalog.api.models.resources import LocatedResource, MaterializedResource


@pytest.mark.parametrize("provider", ["local", "file", "nfs"])
@pytest.mark.parametrize("use_uri", [False, True])
def test_local_resource_paths(tmp_path: Path, provider: str, use_uri: bool) -> None:
    target = tmp_path / "mounted data"
    target.mkdir()
    for path in (target, target / "report.txt"):
        if path != target:
            path.write_text("report")
        locator = path.as_uri() if use_uri else str(path)
        resource = LocatedResource(provider, locator)
        expected = MaterializedResource(path)
        assert resource.materialize() == expected
        assert resource.materialize(tmp_path / "unused-cache") == expected
        assert not (tmp_path / "unused-cache").exists()
    localhost = target.as_uri().replace("file:///", "file://localhost/")
    assert LocatedResource(provider, localhost).materialize().path == target


@pytest.mark.parametrize("locator", ["missing", "nfs://server/export", "file://server/export"])
def test_unavailable_local_paths(tmp_path: Path, locator: str) -> None:
    if locator == "missing":
        locator = str(tmp_path / "missing")
    with pytest.raises(MaterializationError):
        LocatedResource("nfs", locator).materialize()
    with pytest.raises(MaterializationError, match="Unsupported filesystem provider"):
        LocatedResource("s3", "s3://bucket/report").materialize(tmp_path)
