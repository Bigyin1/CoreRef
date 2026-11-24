"""Check native derivation edges, named outputs and opt-in host execution."""

import json
import subprocess
from pathlib import Path
from shlex import quote

import pytest

from coreref.api.build import Outputs, Route, Stage, StageSpec
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
    tools = tmp_path / "installed tools"
    tools.mkdir()
    executable = tools / "fixture-synth"
    executable.write_text(
        """#!/bin/sh
case "$1" in
    -version) printf '%s' 'fixture-synth 24.1' ;;
    run) printf '%s' "$FIXTURE_LICENSE_ENV" ;;
    *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    wrapper = tmp_path / "tool-env"
    wrapper.write_text(
        f'#!/bin/sh\nexport PATH={quote(str(tools))}:"$PATH"\n'
        'export FIXTURE_LICENSE_ENV=loaded\nexec "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    manifest = tmp_path / "manifest.yml"
    document = 'ref: demo.host/1.0\ntools:\n  fixture-synth:\n    version: "24.1"\n'
    manifest.write_text(document, encoding="utf-8")
    project = Project(tmp_path, Manifest.load(manifest), Catalog(host_launcher=(str(wrapper),)))
    project.route = Route([StageSpec(name="host", outputs=("host.txt",))])
    project.stages = {
        "host": Stage(transform=stages.host, execution="host", files={manifest.name: manifest})
    }
    assert project.build("host")["host.txt"].read_text() == "loaded"
    log = project.build_dir / "host.log"
    assert log.read_text() == "ran on host"
    log.unlink()
    assert project.build("host")["host.txt"].read_text() == "loaded"
    assert not log.exists()

    manifest.write_text(document.replace("24.1", "24.2"), encoding="utf-8")
    with pytest.raises(CatalogError, match=r"Stage demo\.host-host failed"):
        project.build("host")
    assert "fixture-synth must be '24.2'" in capfd.readouterr().err


def test_release_index_can_retrieve_one_output(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".gitignore").write_text(".build/\n.env\n", encoding="utf-8")
    (checkout / ".env").write_text("untracked private config", encoding="utf-8")
    (checkout / "manifest.yml").write_text("ref: demo.nix/1.0\n", encoding="utf-8")
    (checkout / "input.txt").write_text("published", encoding="utf-8")

    cache = (tmp_path / "cache").as_uri()
    project = _demo(checkout, Catalog(cache=cache))
    project.lock()
    _commit(checkout)
    publication = project.publish("prepare")
    index = publication.files["release.json"]
    consumer = Catalog(cache=cache, releases={publication.ref: index})
    info = consumer.info(publication.ref)
    assert Outputs(consumer).get(info, ("small.txt",))["small.txt"].read_text() == "published"
    assert set(Outputs.links(info) or {}) == {"lockfile", "small.txt", "other.txt"}
    assert consumer._nix.command("path-info", "--store", cache, str(index)) == str(index)
    assert publication.source is not None
    assert not (publication.source / ".env").exists()


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
