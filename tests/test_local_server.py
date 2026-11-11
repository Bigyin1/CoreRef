"""The test server retries only port collisions and always stops owned processes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from coreref.testing import server as servers


def test_real_bind_collision_is_retried_and_both_children_are_stopped(
    tmp_path: Path, conan_server: servers.LocalServer
) -> None:
    render = servers._server_config
    occupied = urlsplit(conan_server.url).port
    assert occupied is not None
    attempts = 0

    def use_occupied_port_once(root: Path, port: int, password: str) -> str:
        nonlocal attempts
        attempts += 1
        return render(root, occupied if attempts == 1 else port, password)

    with (
        patch.object(servers, "_server_config", side_effect=use_occupied_port_once) as config,
        patch.object(servers, "_wait_for_server", wraps=servers._wait_for_server) as wait,
    ):
        with servers.local_server(tmp_path / "server") as server:
            server.configure(tmp_path / "reader")
            assert config.call_count == 2
            assert wait.call_args_list[0].args[1].poll() is not None
            assert wait.call_args_list[1].args[1].poll() is None
        assert all(call.args[1].poll() is not None for call in wait.call_args_list)


def test_non_port_startup_error_is_propagated_without_retry(tmp_path: Path) -> None:
    with patch.object(
        servers, "_wait_for_server", side_effect=RuntimeError("invalid server configuration")
    ) as wait:
        with (
            pytest.raises(RuntimeError, match="invalid server configuration"),
            servers.local_server(tmp_path / "server"),
        ):
            pytest.fail("The context must not be entered after a startup error")
        assert wait.call_count == 1
        assert wait.call_args.args[1].poll() is not None


def test_caller_error_is_propagated_and_does_not_restart_server(tmp_path: Path) -> None:
    with patch.object(servers, "_wait_for_server", wraps=servers._wait_for_server) as wait:
        with (
            pytest.raises(ValueError, match="caller failed"),
            servers.local_server(tmp_path / "server"),
        ):
            raise ValueError("caller failed")
        assert wait.call_count == 1
        assert wait.call_args.args[1].poll() is not None


def test_repeated_port_collisions_have_a_bounded_retry_count(tmp_path: Path) -> None:
    with patch.object(
        servers, "_wait_for_server", side_effect=servers._PortInUse("occupied")
    ) as wait:
        with (
            pytest.raises(RuntimeError, match="could not bind a port"),
            servers.local_server(tmp_path / "server"),
        ):
            pytest.fail("A server could not start")
        assert wait.call_count == 3
        assert all(call.args[1].poll() is not None for call in wait.call_args_list)
