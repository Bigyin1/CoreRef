"""The SDK consumes Conan configuration prepared by the caller."""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import Record, Ref
from tests.helpers import local_record


class _Profile(Protocol):
    settings: Mapping[str, str]


def test_configured_profile_defaults_are_used_without_rewriting(
    tmp_path: Path, *, catalog: Catalog
) -> None:
    home = configured_home(tmp_path / "cache")
    default = home / "profiles/default"
    content = default.read_text()
    default.unlink()
    (home / "profiles/project-host").write_text(content.replace("Release", "Debug"))
    (home / "profiles/project-build").write_text(content)
    config = home / "global.conf"
    config.write_text(
        "core:default_profile=project-host\ncore:default_build_profile=project-build\n"
    )
    before = {path.name: path.read_bytes() for path in (home / "profiles").iterdir()}
    catalog = Catalog(str(home))
    profiles = cast(Mapping[str, _Profile], catalog._client.run("profile", "show"))
    assert profiles["host"].settings["build_type"] == "Debug"
    assert profiles["build"].settings["build_type"] == "Release"
    root = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    assert Graph(catalog, local_record(root)).result.records == (local_record(root),)
    catalog.publish(local_record(root))
    assert before == {path.name: path.read_bytes() for path in (home / "profiles").iterdir()}
    assert config.read_text() == (
        "core:default_profile=project-host\ncore:default_build_profile=project-build\n"
    )
    assert not (home / "hwcatalog-profile").exists()
    assert not (home / "hwcatalog-build-profile").exists()


def test_unconfigured_profile_is_reported_by_conan(tmp_path: Path, *, catalog: Catalog) -> None:
    home = configured_home(tmp_path / "cache")
    (home / "profiles/default").unlink()
    catalog = Catalog(home)
    with pytest.raises(CatalogError, match="profile"):
        Graph(
            catalog,
            local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))),
        )
    assert not (home / "profiles/default").exists()


def test_multiple_enabled_remotes_require_an_explicit_selection(tmp_path: Path) -> None:
    home = configured_home(tmp_path / "cache")
    connection = Catalog(home)
    connection._client.run("remote", "add", "first", "http://127.0.0.1:1")
    connection._client.run("remote", "add", "second", "http://127.0.0.1:2")
    with pytest.raises(CatalogError, match="Multiple enabled Conan remotes"):
        Catalog(home)
    selected = Catalog(home, remote="second")
    assert selected._client.remote == "second"
