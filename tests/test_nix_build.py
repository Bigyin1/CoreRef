"""Check native derivation edges, named outputs and opt-in host execution."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from shlex import quote
from socket import create_server

import pytest

from coreref.api.build import Route, Stage, StageSpec
from coreref.api.errors import CatalogError
from coreref.api.packages import Catalog, Ref
from coreref.api.projects import Manifest, Project

from . import stages

pytestmark = pytest.mark.nix


def _demo(directory: Path, catalog: Catalog, manifest: Manifest | None = None) -> Project:
    project = Project(directory, manifest or Manifest(ref="demo.nix/1.0"), catalog)
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
    with pytest.raises(CatalogError, match="Stage host failed"):
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
    ref = Ref(f"demo.{'component' * 30}/1.0+{'a' * 110}")
    manifest.write_text(
        f"ref: {ref}\ntype: rtl\npayload: {{module: demo, mask: {2**100}}}\n",
        encoding="utf-8",
    )
    (checkout / "input.txt").write_text("published", encoding="utf-8")

    cache = (tmp_path / "cache").as_uri()
    project = _demo(checkout, Catalog(cache=cache), Manifest.load(manifest))
    project.lock()
    _commit(checkout)
    index = project.publish("prepare")
    consumer = Catalog(cache=cache, releases={project.ref: index})
    info = consumer.info(project.ref)
    assert consumer.get_outputs(info, ("small.txt",))["small.txt"].read_text() == "published"
    assert set(info.outputs) == {"lockfile", "small.txt", "other.txt"}
    assert info.kind == "rtl"
    assert info.payload == {"module": "demo", "mask": 2**100}
    assert info.commit
    assert consumer._nix.command("path-info", "--store", cache, "--recursive", str(index)) == str(
        index
    )
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

    local_record = replace(info, source=checkout)
    using.route = Route([StageSpec(name="upper", outputs=("upper.txt",))])
    using.stages = {
        "upper": Stage(transform=stages.upper, artifacts={"small.txt": (local_record, "small.txt")})
    }
    assert using.build("upper")["upper.txt"].read_text() == "PUBLISHED"


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


def test_selective_build_after_host_stage(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("missing", encoding="utf-8")
    project = _demo(tmp_path, Catalog())
    project.route = Route(
        [
            StageSpec(name="host", outputs=("small.txt", "stage.log", "results.json")),
            StageSpec(name="prepare", needs=("host",), outputs=("result.txt", "stage-copy.txt")),
        ]
    )
    project.stages = {
        "host": Stage(transform=stages.two_outputs, execution="host", files={"input.txt": source}),
        "prepare": Stage(transform=stages.two_results, inputs=("small.txt",)),
    }
    with pytest.raises(CatalogError, match="Stage host failed"):
        project.build("host")
    assert not list(project.build_dir.glob(".host-*.json"))
    assert not list(project.build_dir.glob("host-stage-*"))

    source.write_text("same bytes", encoding="utf-8")
    host = project.build("host")
    assert host["stage.log"].read_text() == host["small.txt"].read_text() == "same bytes"
    assert host["results.json"].read_text() == "{}"
    assert host["small.txt"].resolve() != host["stage.log"].resolve()
    log = project.build_dir / "host.log"
    log.unlink()
    result = project._build(("prepare",), selected=("result.txt",))
    assert result["result.txt"].read_text() == "SAME BYTES"
    assert not (project.build_dir / "stage-copy.txt").exists()
    assert not log.exists()


def test_cold_output_uses_configured_substituter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    cache = (tmp_path / "binary-cache").as_uri()
    monkeypatch.setenv("NIX_REMOTE", f"local?root={producer}")
    nix = Catalog(cache=cache)._nix
    paths: list[Path] = []
    for name in ("wanted", "other"):
        artifact = tmp_path / name
        artifact.write_text(str(artifact), encoding="utf-8")
        paths.append(nix.add(artifact, name))
    nix.upload(paths)

    monkeypatch.setenv("NIX_REMOTE", f"local?root={consumer}")
    monkeypatch.setenv("NIX_CONFIG", f"substituters = {cache}")
    wanted, other = paths
    assert not wanted.exists()
    Catalog()._nix.restore((wanted,))
    assert (consumer / wanted.relative_to("/")).read_text() == str(tmp_path / "wanted")
    assert not (consumer / other.relative_to("/")).exists()
