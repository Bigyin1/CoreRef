"""The SDK consumes Conan configuration prepared by the caller."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Record, Ref
from examples.conan_config import configured_home
from tests.helpers import local_record
from tests.test_models import UserPayload


class _Profile(Protocol):
    settings: Mapping[str, str]


def test_configured_profile_defaults_are_used_without_rewriting(*, catalog: Catalog) -> None:
    home = catalog._client.home
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
    root = Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0"))
    assert Graph(catalog, local_record(root)).records == (local_record(root),)
    root = catalog.publish(local_record(root))
    assert before == {path.name: path.read_bytes() for path in (home / "profiles").iterdir()}
    assert config.read_text() == (
        "core:default_profile=project-host\ncore:default_build_profile=project-build\n"
    )
    assert not (home / "coreref-profile").exists()
    assert not (home / "coreref-build-profile").exists()


def test_unconfigured_profile_is_reported_by_conan(tmp_path: Path, *, catalog: Catalog) -> None:
    home = configured_home(tmp_path / "cache")
    (home / "profiles/default").unlink()
    catalog = Catalog(home)
    with pytest.raises(CatalogError, match="profile"):
        Graph(
            catalog,
            local_record(Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0"))),
        )
    assert not (home / "profiles/default").exists()


def test_multiple_enabled_remotes_require_an_explicit_selection(tmp_path: Path) -> None:
    home = configured_home(tmp_path / "cache")
    connection = Catalog(home)
    connection._client.run("remote", "add", "first", "http://127.0.0.1:1")
    connection._client.run("remote", "add", "second", "http://127.0.0.1:2")
    with pytest.raises(CatalogError, match="Multiple enabled registry remotes"):
        Catalog(home)
    selected = Catalog(home, remote="second")
    assert selected._client.remote == "second"


def test_publish_without_remote_fails_before_preparing_inputs(tmp_path: Path) -> None:
    catalog = Catalog(configured_home(tmp_path / "offline"))
    payload = UserPayload({"test": True})
    record = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0"), payload)
    with patch.object(
        catalog._client, "run", side_effect=AssertionError("Unexpected Conan command")
    ):
        with pytest.raises(CatalogError, match="requires a configured registry remote"):
            catalog.publish(record)
        with pytest.raises(CatalogError, match="requires a configured registry remote"):
            catalog._client.upload(str(record.ref))
    assert payload.calls == 0
    assert catalog.search(SearchQuery()) == ()
    assert tuple(sorted(Graph(catalog, record).networkx.edges)) == ()
