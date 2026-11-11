"""Real Conan instances and Git fixtures use owned temporary directories."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.testing import LocalServer, git_repository, git_upstream, local_server


@pytest.fixture
def conan_server(tmp_path: Path) -> Iterator[LocalServer]:
    with local_server(tmp_path / "test-server") as server:
        yield server


@pytest.fixture
def catalog(tmp_path: Path, conan_server: LocalServer) -> Catalog:
    return Catalog(conan_server.configure(tmp_path / "conan-home", local_sources=True))


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    return git_repository(tmp_path / "checkout", {"cpu.sv": "module cpu; endmodule\n"})


@pytest.fixture
def git_origin(checkout: Path, tmp_path: Path) -> Path:
    """A separate authoritative Git remote, standing in for GitLab in local tests."""
    return git_upstream(checkout, tmp_path / "gitlab-origin.git")


@pytest.fixture(autouse=True)
def isolated_default_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONAN_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("CONAN_DEFAULT_BUILD_PROFILE", raising=False)
