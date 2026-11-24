"""Build a generic staged Conan package with the shared recipe generator."""

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from coreref._internal import producer
from coreref._internal.producers import ProducerRecipes, RecipeStage
from coreref._internal.runtime import ProducerRuntime
from coreref._internal.variants import Variants
from coreref.api.build import StageSpec
from coreref.api.packages import JSON, Catalog, PackageInput, Ref
from coreref.testing import Sandbox, environment


def test_shared_executor_stages_only_declared_nested_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def emit(
        directory: Path,
        _log: Path,
        configuration: Mapping[str, JSON],
    ) -> None:
        assert sorted(path.name for path in directory.iterdir()) == ["inputs"]
        data = (directory / "inputs/source").read_text()
        target = directory / "outputs/result"
        target.parent.mkdir()
        target.write_text(f"{configuration['tag']}:{data}")

    source = tmp_path / "inputs/source"
    source.parent.mkdir()
    source.write_text("data")
    (tmp_path / "unlisted").touch()
    layout = tmp_path / "pipeline.json"
    layout.write_text(
        json.dumps(
            {
                "stages": [
                    {
                        "name": "emit",
                        "inputs": ["inputs/source"],
                        "outputs": ["outputs/result"],
                        "transform": f"{producer.__name__}:_test_transform",
                        "configuration": {"tag": "stage"},
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(producer, "_test_transform", emit, raising=False)
    monkeypatch.setattr(sys, "argv", ["producer", str(layout), str(tmp_path)])
    producer.main()
    assert (tmp_path / "outputs/result").read_text() == "stage:data"


def _recipes(env: Sandbox) -> ProducerRecipes:
    env.root.mkdir(exist_ok=True)
    (env.root / "pyproject.toml").write_text('[project]\nname = "producer-test"\nversion = "0"\n')
    (env.root / "uv.lock").write_text("version = 1\n")
    (env.root / ".python-version").write_text("3.10.20\n")
    (env.root / "source").write_text("data")
    return ProducerRecipes(env.catalog, env.root, runtime=ProducerRuntime(env.root))


def _emit_recipe_output(work: Path, _log: Path, configuration: Mapping[str, JSON]) -> None:
    inputs = sorted(path for path in work.rglob("*") if path.is_file())
    data = [f"run:{configuration['tag']}", *(path.read_text() for path in inputs)]
    output = work / str(configuration["target"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(":".join(data))


_TRANSFORM = "test_producer_recipes:_emit_recipe_output"


def test_recipe_uses_prepared_input_and_exact_stage_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Supply the installed test transform without downloading a Python environment.
    uv = tmp_path / "uv"
    uv.write_text(
        f"""#!{sys.executable}
import sys
from pathlib import Path
from runpy import run_module

args = sys.argv[1:]
assert args[:3] == ["run", "--locked", "--no-dev"]
assert Path("uv.lock").is_file() and Path("pyproject.toml").is_file()
assert args[args.index("--python") + 1] == Path(".python-version").read_text().strip()
sys.path.insert(0, {str(Path(__file__).parent)!r})
module, *arguments = args[args.index("-m") + 1:]
sys.argv = [module, *arguments]
run_module(module, run_name="__main__")
"""
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    with environment(tmp_path) as env:
        recipes = _recipes(env)
        upstream = recipes.publish(
            env.ref("upstream"),
            (
                RecipeStage(
                    StageSpec(name="emit", outputs=("message",)),
                    ("source",),
                    _TRANSFORM,
                    configuration={"tag": "emit", "target": "message"},
                ),
            ),
            members={"emit": ("emit",)},
        )
        stages = (
            RecipeStage(
                StageSpec(name="transform", outputs=("mid/value",)),
                ("source",),
                _TRANSFORM,
                configuration={"tag": "transform", "target": "mid/value"},
            ),
            RecipeStage(
                StageSpec(name="finish", needs=("transform",), outputs=("final/result",)),
                ("mid/value", "upstream/message"),
                _TRANSFORM,
                configuration={"tag": "finish", "target": "final/result"},
                artifacts={"upstream/message": (upstream["emit"], "message")},
            ),
        )
        groups = recipes.publish(
            env.ref("downstream"),
            stages,
            members={"finish": ("finish",), "pending": ("absent",), "transform": ("transform",)},
        )
        assert tuple(groups) == ("transform", "finish")
        result = groups["finish"]
        (runtime,) = upstream["emit"].prerequisites
        assert Ref(runtime).component.startswith("uv-runtime-")
        assert set(result.prerequisites) == {runtime, groups["transform"].ref, upstream["emit"].ref}
        package = Variants(env.new_catalog())._get(
            PackageInput(Ref(result.ref), {}),
            lockfile=None,
            build_missing=True,
            build_prerequisites=tuple(map(Ref, result.prerequisites)),
        )
        assert (package.directory / "final/result").read_text() == (
            "run:finish:run:transform:data:run:emit:data"
        )


def test_reordering_declared_outputs_keeps_producer_revision(tmp_path: Path) -> None:
    with environment(tmp_path) as env:
        recipes = _recipes(env)

        def publish(outputs: tuple[str, ...]) -> str:
            stage = StageSpec(name="emit", outputs=outputs)
            producer = recipes.publish(
                env.ref("producer"),
                (RecipeStage(stage, ("source",), _TRANSFORM),),
                members={"emit": ("emit",)},
            )
            assert producer["emit"].outputs == ("first", "second")
            return producer["emit"].ref

        assert publish(("first", "second")) == publish(("second", "first"))


def test_recipe_rejects_cycle_between_groups(catalog: Catalog, tmp_path: Path) -> None:
    recipes = ProducerRecipes(catalog, tmp_path, runtime=ProducerRuntime(tmp_path))
    stages = (
        RecipeStage(StageSpec(name="start", outputs=()), (), _TRANSFORM),
        RecipeStage(StageSpec(name="middle", needs=("start",), outputs=()), (), _TRANSFORM),
        RecipeStage(StageSpec(name="finish", needs=("middle",), outputs=()), (), _TRANSFORM),
    )
    with pytest.raises(ValueError, match="Cycle between Conan producer groups"):
        recipes.publish(
            Ref("demo.project/1.0"),
            stages,
            members={"outer": ("start", "finish"), "inner": ("middle",)},
        )


@pytest.mark.parametrize("invalid_output", ["missing", "symlink"])
def test_recipe_runner_preserves_all_old_outputs_if_one_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_output: str
) -> None:
    def emit(
        directory: Path,
        _log: Path,
        _configuration: Mapping[str, JSON],
    ) -> None:
        (directory / "first").write_text("new data")
        if invalid_output == "symlink":
            (directory / "second").symlink_to(directory / "first")

    layout = tmp_path / "pipeline.json"
    layout.write_text(
        json.dumps(
            {
                "stages": [
                    {
                        "name": "emit",
                        "inputs": [],
                        "outputs": ["first", "second"],
                        "transform": f"{producer.__name__}:_test_transform",
                    }
                ],
            }
        )
    )
    for name in ("first", "second"):
        (tmp_path / name).write_text(f"previous {name}")
    monkeypatch.setattr(producer, "_test_transform", emit, raising=False)
    monkeypatch.setattr(sys, "argv", ["producer", str(layout), str(tmp_path)])

    with pytest.raises(RuntimeError, match="emit produced no second"):
        producer.main()

    assert (tmp_path / "first").read_text() == "previous first"
    assert (tmp_path / "second").read_text() == "previous second"
    assert not tuple(tmp_path.glob("emit-*"))
