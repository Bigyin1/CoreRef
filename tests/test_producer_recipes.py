"""Build a generic staged Conan package with the shared recipe generator."""

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from coreref._internal import producer
from coreref._internal.producers import ProducerRecipes, RecipeStage
from coreref._internal.variants import Variants
from coreref.api.build import StageSpec
from coreref.api.packages import JSON, Catalog, PackageInput, Ref
from coreref.testing import Sandbox, environment


def test_shared_executor_stages_only_declared_nested_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def emit(
        _name: str,
        directory: Path,
        _log: Path,
        stage: Mapping[str, JSON],
        pipeline: Mapping[str, JSON],
    ) -> None:
        assert sorted(path.name for path in directory.iterdir()) == ["inputs"]
        data = (directory / "inputs/source").read_text()
        target = directory / "outputs/result"
        target.parent.mkdir()
        target.write_text(f"{pipeline['prefix']}:{stage['tag']}:{data}")

    source = tmp_path / "inputs/source"
    source.parent.mkdir()
    source.write_text("data")
    (tmp_path / "unlisted").touch()
    layout = tmp_path / "pipeline.json"
    layout.write_text(
        json.dumps(
            {
                "configuration": {"prefix": "recipe"},
                "stages": [
                    {
                        "name": "emit",
                        "inputs": ["inputs/source"],
                        "outputs": ["outputs/result"],
                        "configuration": {"tag": "stage"},
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(producer, "_test_handler", emit, raising=False)
    monkeypatch.setattr(
        sys, "argv", ["producer", f"{producer.__name__}:_test_handler", str(layout), str(tmp_path)]
    )
    producer.main()
    assert (tmp_path / "outputs/result").read_text() == "recipe:stage:data"


def _runner(env: Sandbox) -> ProducerRecipes:
    env.root.mkdir(exist_ok=True)
    runner = env.root / "runner.py"
    runner.write_text(
        "def run_pipeline(configuration, stages, directory):\n"
        "    for stage in stages:\n"
        "        data = [configuration['prefix'], *stage['command']]\n"
        "        data += [(directory / name).read_text() for name in stage['inputs']]\n"
        "        for name in stage['outputs']:\n"
        "            output = directory / name\n"
        "            output.parent.mkdir(parents=True, exist_ok=True)\n"
        "            output.write_text(':'.join(data))\n"
    )
    (env.root / "source").write_text("data")
    return ProducerRecipes(env.catalog, env.root, runner=runner, configuration={"prefix": "run"})


def test_recipe_uses_prepared_input_and_exact_stage_outputs(tmp_path: Path) -> None:
    with environment(tmp_path) as env:
        recipes = _runner(env)
        upstream = recipes.publish(
            env.ref("upstream"),
            (RecipeStage(StageSpec(name="emit", outputs=("message",)), ("source",), ("emit",)),),
            members={"emit": ("emit",)},
            outputs={"emit": ("message",)},
            external={},
        )
        stages = (
            RecipeStage(
                StageSpec(name="transform", outputs=("mid/value",)), ("source",), ("transform",)
            ),
            RecipeStage(
                StageSpec(name="finish", needs=("transform",), outputs=("final/result",)),
                ("mid/value", "upstream/message"),
                ("finish",),
            ),
        )
        groups = recipes.publish(
            env.ref("downstream"),
            stages,
            members={"finish": ("finish",), "pending": ("absent",), "transform": ("transform",)},
            outputs={"transform": ("mid/value",), "finish": ("final/result",)},
            external={"upstream/message": (upstream["emit"], "message")},
        )
        assert tuple(groups) == ("transform", "finish")
        result = groups["finish"]
        assert set(result.prerequisites) == {groups["transform"].ref, upstream["emit"].ref}
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
        recipes = _runner(env)

        def publish(outputs: tuple[str, ...]) -> str:
            stage = StageSpec(name="emit", outputs=outputs)
            producer = recipes.publish(
                env.ref("producer"),
                (RecipeStage(stage, ("source",), ("emit",)),),
                members={"emit": ("emit",)},
                outputs={"emit": ("first", "second")},
                external={},
            )
            return producer["emit"].ref

        assert publish(("first", "second")) == publish(("second", "first"))


def test_recipe_rejects_cycle_between_groups(catalog: Catalog, tmp_path: Path) -> None:
    recipes = ProducerRecipes(catalog, tmp_path, runner=tmp_path / "runner.py", configuration={})
    stages = (
        RecipeStage(StageSpec(name="start", outputs=()), (), ()),
        RecipeStage(StageSpec(name="middle", needs=("start",), outputs=()), (), ()),
        RecipeStage(StageSpec(name="finish", needs=("middle",), outputs=()), (), ()),
    )
    with pytest.raises(ValueError, match="Cycle between Conan producer groups"):
        recipes.publish(
            Ref("demo.project/1.0"),
            stages,
            members={"outer": ("start", "finish"), "inner": ("middle",)},
            outputs={},
            external={},
        )
