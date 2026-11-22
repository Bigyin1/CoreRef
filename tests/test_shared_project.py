"""Exercise the common manifest, routes and release inputs used by a tool flow."""

import json
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType

import pytest
from typer.testing import CliRunner

from coreref._internal.editor import ManifestFile
from coreref._internal.snapshot import SliceInputs, Snapshot, clean_commit, committed_slice
from coreref.api.build import Route, Stage, StageSpec
from coreref.api.errors import CatalogError
from coreref.api.packages import Catalog, LocalRecord, PackageInput, Ref, Requirement, SearchQuery
from coreref.api.projects import Manifest, Project
from coreref.cli import create_deps_app
from coreref.testing import git_commit, git_repository, git_upstream, trace_operations


def test_manifest_read_and_edit_share_the_same_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    installed = version("coreref")
    path.write_text(f"""ref: demo.project/1.0
type: custom
flow_version: "{installed}"
release_through: my-stage
options: {{width: [8, 16]}}
dependencies:
  - "demo.rtl/[>=1 <2]"
  - {{name: ram, ref: demo.memory/1.0, options: {{width: 8}}}}
""")
    original = Manifest.load(path, flow_package="coreref")
    editable = ManifestFile(path)
    editable.add("demo.extra/1.0")
    editable.save()
    restored = Manifest.load(path, flow_package="coreref")
    assert restored.options == original.options
    assert restored.release_through == original.release_through
    assert restored.type == "custom"
    assert restored.requirements == (Requirement("demo.rtl/[>=1 <2]"), Ref("demo.extra/1.0"))
    assert restored.configured_dependencies == {
        "ram": PackageInput(Ref("demo.memory/1.0"), {"width": 8})
    }


def test_manifest_checks_active_flow_version(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text("ref: demo.project/1.0\n")
    with pytest.raises(ValueError, match="declare flow_version"):
        Manifest.load(path, flow_package="coreref")
    path.write_text('ref: demo.project/1.0\nflow_version: "0.0.0"\n')
    with pytest.raises(ValueError, match="active environment"):
        Manifest.load(path, flow_package="coreref")
    with pytest.raises(ValueError, match="Install it"):
        Manifest.load(path, flow_package="coreref-nonexistent-flow")


def test_manifest_locks_and_installs_both_dependency_kinds(
    tmp_path: Path, catalog: Catalog
) -> None:
    ordinary = catalog.publish(LocalRecord(Ref("demo.rtl/1.0")))
    generator = tmp_path / "generator.py"
    generator.write_text(
        "def generate(options, source, output):\n"
        '    (output / "model").write_text(options["width"])\n'
    )
    ref = catalog.publish_variant_recipe(
        Ref("demo.memory/1.0"), options={"width": (8, 16)}, inputs={}, runner=generator
    )
    path = tmp_path / "manifest.yml"
    path.write_text("""ref: demo.project/1.0
dependencies:
  - demo.rtl/1.0
  - {name: ram, ref: demo.memory/1.0, options: {width: 16}}
""")
    document = ManifestFile(path)
    lockfile = document.create_lockfile(catalog)
    project = Project(tmp_path, Manifest.load(path), catalog)
    assert project.lock() == lockfile
    records = project.graph.dependencies(transitive=True)
    assert tuple(record.ref for record in records) == (ordinary.ref,)
    packages = project.install_variants(build_missing=True)
    assert packages["ram"].ref == ref
    assert (packages["ram"].directory / "model").read_text() == "16"

    previous = lockfile.read_bytes()
    with pytest.raises(CatalogError):
        project.lock(PackageInput(Ref("demo.absent/1.0"), {}))
    assert lockfile.read_bytes() == previous


def test_shared_cli_reads_the_standard_flow_fields(tmp_path: Path, catalog: Catalog) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text("ref: demo.project/1.0\ntype: custom\nrelease_through: lint\n")
    app = create_deps_app(catalog_factory=lambda: catalog)
    result = CliRunner().invoke(app, ["install", "--file", str(path)])
    assert result.exit_code == 0, result.exception
    assert json.loads(result.output) == []


def test_committed_slice_checks_external_inputs_and_dirty_checkout(tmp_path: Path) -> None:
    repository = git_repository(tmp_path / "work", {"src/main": "sources"})
    lockfile = tmp_path / "manifest.lock"
    lockfile.write_text("first lock")

    def capture(commit: str) -> SliceInputs:
        return SliceInputs(Ref("demo.project/1.0"), commit, lockfile, (), {})

    with committed_slice(repository, capture) as inputs:
        assert inputs.commit == clean_commit(repository)
    assert clean_commit(repository / "src") is None
    assert clean_commit(tmp_path) is None

    with pytest.raises(ValueError, match="inputs changed"), committed_slice(repository, capture):
        lockfile.write_text("another lock")
    with pytest.raises(ValueError, match="inputs changed"), committed_slice(repository, capture):
        (repository / "unexpected").touch()
    with (
        pytest.raises(ValueError, match="clean Git checkout"),
        committed_slice(repository, capture),
    ):
        pytest.fail("A dirty checkout reached the build")


def test_snapshot_tracks_inputs_of_release_stages(tmp_path: Path, catalog: Catalog) -> None:
    project = Project(tmp_path, Manifest(ref="demo.project/1.0", flow_version="1.0"), catalog)
    project.lock()
    route = Route(
        [
            StageSpec(name="emit", outputs=("payload.out",)),
            StageSpec(name="inspect", outputs=("note.txt",), purpose="development"),
        ]
    )
    source, optional = tmp_path / "model.src", tmp_path / "tuning.cfg"
    source.write_text("initial")
    flags = ["one"]

    def stage_inputs(stage: StageSpec) -> Stage:
        assert stage.name == "emit"
        return Stage(
            action=lambda: None,
            files={"source": source, "optional": optional},
            configuration={"flags": tuple(flags)},
        )

    snapshot = Snapshot(
        project, route, stage_inputs=stage_inputs, variants=project.install_variants
    )
    original = snapshot.inputs("first")
    before = original.key()
    assert snapshot.metadata(original)["commit"] == "first"
    (tmp_path / "unrelated.py").write_text("unrelated")
    assert snapshot.inputs("another").key() == before

    optional.write_text("enabled")
    configured = snapshot.inputs("another")
    assert configured.key() != before
    flags.append("two")
    assert snapshot.inputs("another").key() != configured.key()


def test_shared_tool_pins_check_exact_versions(tmp_path: Path) -> None:
    executable = tmp_path / "eda"
    executable.write_text("#!/bin/sh\nprintf 'EDA 1.2.3\\n'\n")
    executable.chmod(0o755)
    image = f"engine@sha256:{'a' * 64}"
    manifest = Manifest.model_validate(
        {
            "ref": "demo.project/1.0",
            "tools": {
                "eda": {"command": str(executable), "version": "1.2.3"},
                "engine": {"image": image},
            },
        }
    )
    assert manifest.command("eda") == (str(executable), "1.2.3")
    assert manifest.image("engine") == image
    assert manifest.image("eda") is None
    with pytest.raises(ValueError, match="Declare"):
        manifest.command("missing")


def test_publish_keeps_previous_revision_when_an_external_input_changes(
    tmp_path: Path, catalog: Catalog
) -> None:
    directory = git_repository(
        tmp_path / "project", {".gitignore": ".build/\n", "rtl.v": "module block; endmodule"}
    )
    git_upstream(directory, tmp_path / "upstream.git")
    tuning = tmp_path / "tuning.cfg"
    tuning.write_text("original")
    change_during_build = False

    class EmitProject(Project):
        @property
        def route(self) -> Route:
            return Route([StageSpec(name="emit", outputs=("result.v",))])

        def _stage(self, _spec: StageSpec) -> Stage:
            return Stage(action=self._emit, files={"tuning": tuning}, always_run=True)

        def _emit(self) -> None:
            (self.build_dir / "result.v").write_text(tuning.read_text())
            if change_during_build:
                tuning.write_text("updated")

    project = EmitProject(directory, Manifest(ref="demo.block/1.0"), catalog)
    lockfile = project.lock()
    git_commit(directory, {lockfile.name: lockfile.read_bytes()}, push=True)
    first = project.publish("emit")

    change_during_build = True
    with trace_operations() as failed, pytest.raises(ValueError, match="inputs changed"):
        project.publish("emit")
    assert all(command[0] != "upload" for command in failed.commands)
    assert catalog.info(project.record.ref).ref == first.ref

    change_during_build = False
    second = project.publish("emit")
    assert second.ref != first.ref
    assert project.outputs.get(first, ("result.v",))["result.v"].read_text() == "original"
    assert project.outputs.get(second, ("result.v",))["result.v"].read_text() == "updated"


def test_build_targets_share_prerequisites_and_leave_other_branches_idle(tmp_path: Path) -> None:
    executed: list[str] = []

    class BranchProject(Project):
        @property
        def route(self) -> Route:
            return Route(
                [
                    StageSpec(name="rtl", outputs=("rtl.v",)),
                    StageSpec(name="synth", needs=("rtl",), outputs=("netlist.v",)),
                    StageSpec(name="simulate", needs=("rtl",), outputs=("trace.vcd",)),
                    StageSpec(name="route", needs=("synth",), outputs=("routed.odb",)),
                ]
            )

        def _stage(self, spec: StageSpec) -> Stage:
            def emit() -> None:
                executed.append(spec.name)
                for name in spec.outputs:
                    (self.build_dir / name).write_text(spec.name)

            return Stage(action=emit)

    project = BranchProject(tmp_path, Manifest(ref="demo.block/1.0"), Catalog(tmp_path / "home"))
    with pytest.raises(ValueError, match="Select at least one"):
        project.build(())
    with pytest.raises(ValueError, match="Unknown stage"):
        project.build(("synth", "unknown"))
    assert not executed

    artifacts = project.build(("synth", "simulate", "synth"))
    assert artifacts.keys() == {"rtl.v", "netlist.v", "trace.vcd"}
    assert executed[0] == "rtl"
    assert sorted(executed) == ["rtl", "simulate", "synth"]

    assert project.build(("simulate", "synth")) == artifacts
    assert executed[0] == "rtl"
    assert sorted(executed) == ["rtl", "simulate", "synth"]
    assert not (project.build_dir / "routed.odb").exists()


class ProducerProject(Project):
    _producer_handler = "example.flow:run"
    _output_groups = MappingProxyType({"synth": ("netlist.v",), "layout": ("layout.lef",)})

    @property
    def route(self) -> Route:
        return Route(
            [
                StageSpec(name="rtl", outputs=("rtl.v",)),
                StageSpec(name="synth", needs=("rtl",), outputs=("netlist.v",)),
                StageSpec(name="layout", needs=("synth",), outputs=("layout.lef",)),
            ]
        )

    def _stage(self, spec: StageSpec) -> Stage:
        def emit() -> None:
            if spec.name != "rtl":
                pytest.fail(f"Publication of RTL must not run {spec.name}")
            (self.build_dir / "rtl.v").write_text("module block; endmodule")

        return Stage(action=emit, files=self._producer_files())

    def _stage_ready(self, spec: StageSpec, *, for_recipe: bool) -> bool:
        if for_recipe:
            return all(path.is_file() for path in self._producer_files().values())
        return super()._stage_ready(spec, for_recipe=False)

    def _producer_members(self) -> dict[str, tuple[str, ...]]:
        return {"synth": ("synth",), "layout": ("layout",)}


def test_publish_rtl_registers_ready_recipe_groups_without_building_them(
    tmp_path: Path, catalog: Catalog
) -> None:
    directory = git_repository(
        tmp_path / "project",
        {
            ".gitignore": ".build/\n",
            ".python-version": "3.13.14\n",
            "pyproject.toml": '[project]\nname = "producer-fixture"\n',
            "uv.lock": (
                'version = 1\n[[package]]\nname = "producer-fixture"\nsource = {virtual = "."}\n'
            ),
        },
    )
    git_upstream(directory, tmp_path / "upstream.git")

    project = ProducerProject(directory, Manifest(ref="demo.block/1.0"), catalog)
    lockfile = project.lock()
    git_commit(directory, {lockfile.name: lockfile.read_bytes()}, push=True)

    with trace_operations() as operations:
        release = project.publish("rtl", with_producers=True)

    assert not operations.installs
    assert set(project.outputs.links(release) or ()) == {"rtl.v", "lockfile"}
    assert project.outputs.available(release, producer_only=True) == {"netlist.v", "layout.lef"}
    refs = catalog.search_refs(SearchQuery(namespace="coreref-producer"))
    assert len(refs) == 3
    assert len([ref for ref in refs if ref.component.startswith("uv-runtime-")]) == 1
    assert all(ref.revision is not None for ref in refs)
