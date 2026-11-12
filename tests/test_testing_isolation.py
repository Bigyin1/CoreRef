"""Fixture setup preserves source trees and existing client configuration."""

from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.testing import LocalServer, Sandbox, configured_home

pytestmark = pytest.mark.conan_remote


@pytest.mark.parametrize("absolute", [True, False])
@pytest.mark.parametrize("directory", [True, False])
def test_internal_symlinks_edit_only_the_working_copy(
    conan_server: LocalServer,
    tmp_path: Path,
    *,
    absolute: bool,
    directory: bool,
) -> None:
    source = tmp_path / "input"
    (source / "shared").mkdir(parents=True)
    original = source / "shared/config"
    original.write_text("original")
    target = source / "shared" if directory else original
    link_target = target if absolute else target.relative_to(source)
    (source / "alias").symlink_to(link_target, target_is_directory=directory)
    env = Sandbox(conan_server, tmp_path / "infra")
    copied = env.load_projects(source)
    edited = copied / "alias/config" if directory else copied / "alias"
    edited.write_text("edited")
    assert original.read_text() == "original"
    assert (copied / "shared/config").read_text() == "edited"
    assert (source / "alias").readlink() == link_target
    assert not (copied / "alias").readlink().is_absolute()


def test_external_symlinks_are_rejected_before_copy(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input"
    source.mkdir()
    outside = tmp_path / "external"
    outside.write_text("outside")
    (source / "alias").symlink_to(outside)
    env = Sandbox(conan_server, tmp_path / "infra")
    with pytest.raises(ValueError, match="outside the source tree"):
        env.load_projects(source)
    assert not env.projects.exists()
    assert outside.read_text() == "outside"


def test_repeated_configuration_preserves_settings_and_switches_local_scm_policy(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    home = conan_server.configure(tmp_path / "reader")
    config = home / "global.conf"
    config.write_text("# custom settings\ncore:non_interactive=True\ncore.scm:local_url=block\n")
    conan_server.configure(home)
    before = config.read_text()
    conan_server.configure(home)
    assert config.read_text() == before
    assert "# custom settings\ncore:non_interactive=True\n" in before
    assert before.count("core.scm:local_url=") == 1
    assert "core.scm:local_url=allow" in before
    conan_server.configure(home, local_sources=False)
    assert "core.scm:local_url=block" in config.read_text()
    env = Sandbox(conan_server, tmp_path / "infra")
    record = env.package(env.ref("metadata"))
    assert Catalog(home).publish(record).ref.without_revision() == record.ref


def test_conflicting_remote_does_not_overwrite_existing_configuration(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    home = configured_home(tmp_path / "reader")
    config = home / "global.conf"
    config.write_text("core:non_interactive=True\n")
    catalog = Catalog(home)
    catalog._client.run("remote", "add", conan_server.remote, "http://127.0.0.1:1")
    with pytest.raises(ValueError, match="another server"):
        conan_server.configure(home)
    assert config.read_text() == "core:non_interactive=True\n"
