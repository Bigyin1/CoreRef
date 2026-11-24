"""Check native derivation edges, named outputs and opt-in host execution."""

import json
import subprocess
from pathlib import Path
from shlex import quote
from socket import create_server

import pytest

from coreref.api.build import Route, Stage, StageSpec
from coreref.api.errors import CatalogError
from coreref.api.packages import Catalog
from coreref.api.projects import Manifest, Project

from . import stages

pytestmark = pytest.mark.nix


def _demo(directory: Path, catalog: Catalog) -> Project:
    project = Project(directory, Manifest(ref="demo.nix/1.0"), catalog)
    source = directory / "input.txt"
    project.route = Route(
        [
            StageSpec(name="prepare", outputs=("small.txt", "other.txt")),
            StageSpec(name="upper", needs=("prepare",), outputs=("upper.txt",)),
        ]
    )
    project.stages = {
        "prepare": Stage(transform=stages.prepare, files={"input.txt": source}),
        "upper": Stage(transform=stages.upper, inputs=("small.txt",)),
    }
    return project


def test_python_stages_are_native_derivations_with_selected_input_outputs(tmp_path: Path) -> None:
    catalog = Catalog()
    (tmp_path / "input.txt").write_text("hello", encoding="utf-8")
    project = _demo(tmp_path, catalog)
    artifacts = project.build("upper")
    assert artifacts["upper.txt"].read_text() == "HELLO"
    assert artifacts["small.txt"].resolve().parent != artifacts["other.txt"].resolve().parent
    deriver = catalog._nix.command(
        "path-info", "--derivation", str(artifacts["upper.txt"].resolve().parent)
    )
    graph = json.loads(catalog._nix.command("derivation", "show", deriver))
    inputs = (
        graph["derivations"][Path(deriver).name]["inputs"]["drvs"]
        if "derivations" in graph
        else graph[deriver]["inputDrvs"]
    )
    upstream = next(value for key, value in inputs.items() if "-prepare" in key)
    assert (upstream["outputs"] if isinstance(upstream, dict) else upstream) == ["out"]
    initial = artifacts["upper.txt"].resolve()
    (tmp_path / "README.md").write_text("not a stage input", encoding="utf-8")
    assert project.build("upper")["upper.txt"].resolve() == initial
    (tmp_path / "input.txt").write_text("changed", encoding="utf-8")
    changed = project.build("upper")["upper.txt"]
    assert changed.resolve() != initial
    assert changed.read_text() == "CHANGED"


def test_host_launcher_runs_the_tool_from_path_with_the_pinned_version(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with create_server(("127.0.0.1", 0)) as server:
        project = _host_project(tmp_path, server.getsockname()[1])
        assert project.build("host")["host.txt"].read_text() == "loaded"
        server.settimeout(5)
        connection, _ = server.accept()
        with connection:
            assert connection.recv(1) == b"L"
    log = project.build_dir / "host.log"
    assert log.read_text() == "ran on host"
    log.unlink()
    assert project.build("host")["host.txt"].read_text() == "loaded"
    assert not log.exists()

    manifest = tmp_path / "manifest.yml"
    manifest.write_text(manifest.read_text().replace("24.1", "24.2"), encoding="utf-8")
    with pytest.raises(CatalogError, match=r"Stage demo\.host-host failed"):
        project.build("host")
    assert "fixture-synth must be '24.2'" in capfd.readouterr().err


def _host_project(directory: Path, port: int) -> Project:
    tools = directory / "installed tools"
    tools.mkdir()
    executable = tools / "fixture-synth"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import socket
import sys

if sys.argv[1:] == ["-version"]:
    print("fixture-synth 24.1")
elif sys.argv[1:] == ["run"]:
    with socket.create_connection(("127.0.0.1", int(os.environ["FIXTURE_LICENSE_PORT"])),
                                  timeout=5) as server:
        server.sendall(b"L")
    print("loaded", end="")
else:
    sys.exit(2)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    wrapper = directory / "tool-env"
    wrapper.write_text(
        f'#!/bin/sh\nexport PATH={quote(str(tools))}:"$PATH"\n'
        f'export FIXTURE_LICENSE_PORT={port}\nexec "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    manifest = directory / "manifest.yml"
    manifest.write_text("ref: demo.host/1.0\neda_tools: [fixture-synth/24.1]\n", encoding="utf-8")
    project = Project(directory, Manifest.load(manifest), Catalog(host_launcher=(str(wrapper),)))
    project.route = Route([StageSpec(name="host", outputs=("host.txt",))])
    project.stages = {
        "host": Stage(transform=stages.host, execution="host", files={manifest.name: manifest})
    }
    return project


def test_release_index_can_retrieve_one_output(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".gitignore").write_text(".build/\n.env\n", encoding="utf-8")
    (checkout / ".env").write_text("untracked private config", encoding="utf-8")
    manifest = checkout / "manifest.yml"
    manifest.write_text("ref: demo.nix/1.0\ntype: rtl\npayload: {module: demo}\n", encoding="utf-8")
    (checkout / "input.txt").write_text("published", encoding="utf-8")

    cache = (tmp_path / "cache").as_uri()
    project = _demo(checkout, Catalog(cache=cache))
    project.manifest = Manifest.load(manifest)
    project.lock()
    _commit(checkout)
    index = project.publish("prepare")
    consumer = Catalog(cache=cache, releases={project.ref: index})
    info = consumer.info(project.ref)
    assert consumer.get_outputs(info, ("small.txt",))["small.txt"].read_text() == "published"
    assert set(info.outputs) == {"lockfile", "small.txt", "other.txt"}
    assert info.kind == "rtl"
    assert info.payload == {"module": "demo"}
    assert info.commit
    assert consumer._nix.command("path-info", "--store", cache, str(index)) == str(index)
    assert not (info.source / ".env").exists()
    assert project.publish("prepare") == index

    using = Project(
        tmp_path, Manifest(ref="demo.consumer/1.0", dependencies=[str(project.ref)]), consumer
    )
    using.lock()
    consumer.releases[project.ref] = project.publish("upper")
    assert consumer.releases[project.ref] != index
    assert "upper.txt" in consumer.info(project.ref).outputs
    assert using.graph.info(project.ref) == info


def _commit(directory: Path) -> None:
    options = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid")
    for arguments in (("init",), ("add", "."), (*options, "commit", "-m", "Fixture")):
        subprocess.run(  # noqa: S603 - isolated Git fixture with fixed subcommands.
            ["git", "-C", str(directory), *arguments],  # noqa: S607
            check=True,
            capture_output=True,
        )


def test_git_fetcher_preserves_the_locked_source_after_a_new_commit(tmp_path: Path) -> None:
    repo = tmp_path / "component"
    repo.mkdir()
    manifest = repo / "manifest.yml"
    manifest.write_text("ref: demo.git/1.0\n", encoding="utf-8")
    _commit(repo)
    nix = Catalog()._nix
    path, locked = nix.prefetch(f"git+{repo.as_uri()}")
    manifest.write_text("ref: demo.git/1.0\ntype: updated\n", encoding="utf-8")
    _commit(repo)
    assert nix.fetch(locked) == path
    assert (path / "manifest.yml").read_text() == "ref: demo.git/1.0\n"


def test_native_output_request_materializes_only_named_result(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_text("selected", encoding="utf-8")
    project = _demo(tmp_path, Catalog())
    files = project._build(("prepare",), selected=("other.txt",))
    assert tuple(files) == ("other.txt",)
    assert files["other.txt"].read_text() == "separate result"
    assert not (project.build_dir / "small.txt").exists()
