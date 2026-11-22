"""Generate Conan producer recipes from groups of prepared stages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from ..api.build.route import StageSpec
from ..api.packages.catalog import Catalog
from ..api.packages.records import JSON, LocalRecord, Ref
from .artifacts import PRODUCER_NAMESPACE, Producer
from .hashing import file_digest, fingerprint
from .recipe import _RECIPE_ENV
from .runtime import ProducerRuntime

# Staged name -> exact producer and filename in its binary package.
Bindings = Mapping[str, tuple[Producer, str]]
_PRODUCER_TEMPLATE = _RECIPE_ENV.get_template("producer.py.j2")


@dataclass(frozen=True)
class RecipeStage:
    spec: StageSpec
    inputs: tuple[str, ...]
    command: tuple[str, ...] = ()
    configuration: Mapping[str, JSON] = field(default_factory=dict)


@dataclass(frozen=True)
class _RecipeDependencies:
    producers: Mapping[str, Producer]
    runtime: Ref | None


class ProducerRecipes:
    """Package prepared inputs, stage groups, and a self-contained Python runner."""

    def __init__(
        self,
        catalog: Catalog,
        prepared: Path,
        *,
        runner: Path | ProducerRuntime,
        configuration: Mapping[str, JSON],
    ) -> None:
        self._catalog = catalog
        self._variants = catalog._variants
        self._prepared = prepared
        self._runner = runner
        self._configuration = dict(configuration)

    def publish(
        self,
        project: Ref,
        stages: tuple[RecipeStage, ...],
        *,
        members: Mapping[str, tuple[str, ...]],
        outputs: Mapping[str, tuple[str, ...]],
        external: Bindings,
    ) -> dict[str, Producer]:
        """Publish complete groups from topologically ordered, prepared stages."""
        component = fingerprint(project.package_name)
        published: dict[str, Producer] = {}
        plan = _groups(stages, members)
        runtime = self._publish_runtime() if plan else None
        for group, dependencies, commands in plan:
            ref = Ref(f"{PRODUCER_NAMESPACE}.{component}-{group}/{project.version}")
            bindings = _bindings(commands, published, outputs, external)
            required = {published[name].ref: published[name] for name in dependencies}
            required.update((producer.ref, producer) for producer, _ in bindings.values())
            if runtime is not None:
                required[str(runtime)] = Producer(ref=str(runtime))
            published[group] = self._publish_group(
                ref,
                commands,
                outputs=outputs[group],
                bindings=bindings,
                dependencies=_RecipeDependencies(required, runtime),
            )
        return published

    def _publish_runtime(self) -> Ref | None:
        if not isinstance(self._runner, ProducerRuntime):
            return None
        self._runner.validate()
        files = self._runner.files
        digest = fingerprint({name: file_digest(path) for name, path in files.items()})
        ref = Ref(f"{PRODUCER_NAMESPACE}.uv-runtime-{digest}/1.0")
        return self._catalog.publish(LocalRecord(ref, files=files)).ref

    def _publish_group(
        self,
        ref: Ref,
        stages: tuple[RecipeStage, ...],
        *,
        outputs: tuple[str, ...],
        bindings: Bindings,
        dependencies: _RecipeDependencies,
    ) -> Producer:
        closure = set(dependencies.producers)
        for producer in dependencies.producers.values():
            closure.update(producer.prerequisites)

        with TemporaryDirectory(prefix="coreref-producer-") as temporary:
            directory = Path(temporary)
            commands = self._stage_inputs(directory, stages, bindings)
            layout = {
                "configuration": self._configuration,
                "stages": commands,
                "outputs": outputs,
                "bindings": {
                    target: (Ref(producer.ref).package_name, filename)
                    for target, (producer, filename) in bindings.items()
                },
            }
            (directory / "pipeline.json").write_text(
                json.dumps(layout, sort_keys=True), encoding="utf-8"
            )
            if isinstance(self._runner, Path):
                copyfile(self._runner, directory / "stage_runner.py")
            runtime = dependencies.runtime
            recipe = _PRODUCER_TEMPLATE.render(
                name=ref.package_name,
                version=ref.version,
                dependencies=sorted(dependencies.producers),
                runtime=runtime.package_name if runtime is not None else None,
                runner_args=self._runner._arguments()
                if isinstance(self._runner, ProducerRuntime)
                else None,
            )
            (directory / "conanfile.py").write_text(recipe, encoding="utf-8")
            exact = self._variants.publish_recipe(directory)
        return Producer(ref=str(exact), prerequisites=tuple(sorted(closure)))

    def _stage_inputs(
        self, directory: Path, stages: tuple[RecipeStage, ...], bindings: Bindings
    ) -> list[JSON]:
        staged = directory / "inputs"
        staged.mkdir()
        available = set(bindings)
        commands: list[JSON] = []
        for stage in stages:
            for name in sorted(set(stage.inputs) - available):
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                copyfile(self._prepared / name, target)
            available.update(stage.inputs)
            available.update(stage.spec.outputs)
            commands.append(
                {
                    "name": stage.spec.name,
                    "command": stage.command,
                    "inputs": stage.inputs,
                    "outputs": tuple(sorted(stage.spec.outputs)),
                    **({"configuration": stage.configuration} if stage.configuration else {}),
                }
            )
        return commands


def _groups(
    stages: tuple[RecipeStage, ...], members: Mapping[str, tuple[str, ...]]
) -> list[tuple[str, tuple[str, ...], tuple[RecipeStage, ...]]]:
    names = {stage.spec.name for stage in stages}
    stage_groups = {
        stage: group for group, group_members in members.items() for stage in group_members
    }
    complete = {
        group: tuple(stage for stage in stages if stage.spec.name in group_members)
        for group, group_members in members.items()
        if set(group_members) <= names
    }
    edges = {
        group: tuple(sorted(_group_needs(group, group_stages, stage_groups)))
        for group, group_stages in complete.items()
    }
    try:
        ordered = tuple(TopologicalSorter(edges).static_order())
    except CycleError as exc:
        raise ValueError(f"Cycle between Conan producer groups: {exc.args[1]}") from exc
    selected: dict[str, tuple[RecipeStage, ...]] = {}
    for group in ordered:
        if group in complete and all(name in selected for name in edges[group]):
            selected[group] = complete[group]
    return [(group, edges[group], commands) for group, commands in selected.items()]


def _group_needs(
    group: str, stages: tuple[RecipeStage, ...], stage_groups: Mapping[str, str]
) -> set[str]:
    return {
        stage_groups[required]
        for stage in stages
        for required in stage.spec.needs
        if required in stage_groups and stage_groups[required] != group
    }


def _bindings(
    stages: tuple[RecipeStage, ...],
    published: Mapping[str, Producer],
    outputs: Mapping[str, tuple[str, ...]],
    external: Bindings,
) -> dict[str, tuple[Producer, str]]:
    inputs = {name for stage in stages for name in stage.inputs}
    produced = {name for stage in stages for name in stage.spec.outputs}
    bindings = {name: external[name] for name in sorted(inputs & external.keys())}
    for group, producer in published.items():
        for name in sorted((inputs & set(outputs[group])) - produced):
            bindings[name] = (producer, name)
    return bindings
