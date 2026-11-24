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
    transform: str
    configuration: Mapping[str, JSON] = field(default_factory=dict)
    files: Mapping[str, Path] = field(default_factory=dict)
    artifacts: Bindings = field(default_factory=dict)

    @property
    def recipe_outputs(self) -> tuple[str, ...]:
        outputs = self.spec.recipe_outputs
        return self.spec.outputs if outputs is None else outputs


class ProducerRecipes:
    """Package prepared stage groups and their locked Python environment."""

    def __init__(self, catalog: Catalog, prepared: Path, *, runtime: ProducerRuntime) -> None:
        self._catalog = catalog
        self._variants = catalog._variants
        self._prepared = prepared
        self._runtime = runtime

    def publish(
        self,
        project: Ref,
        stages: tuple[RecipeStage, ...],
        *,
        members: Mapping[str, tuple[str, ...]],
    ) -> dict[str, Producer]:
        """Publish complete groups from topologically ordered, prepared stages."""
        plan = _groups(stages, members)
        if not plan:
            return {}
        component = fingerprint(project.package_name)
        runtime = self._publish_runtime()
        published: dict[str, Producer] = {}
        for group, dependencies, commands in plan:
            ref = Ref(f"{PRODUCER_NAMESPACE}.{component}-{group}/{project.version}")
            bindings = _bindings(commands, published)
            required = {published[name].ref: published[name] for name in dependencies}
            required.update((producer.ref, producer) for producer, _ in bindings.values())
            published[group] = self._publish_group(
                ref, commands, bindings=bindings, dependencies=required, runtime=runtime
            )
        return published

    def _publish_runtime(self) -> Ref:
        self._runtime.validate()
        files = self._runtime.files
        digest = fingerprint({name: file_digest(path) for name, path in files.items()})
        ref = Ref(f"{PRODUCER_NAMESPACE}.uv-runtime-{digest}/1.0")
        return self._catalog.publish(LocalRecord(ref, files=files)).ref

    def _publish_group(
        self,
        ref: Ref,
        stages: tuple[RecipeStage, ...],
        *,
        bindings: Bindings,
        dependencies: Mapping[str, Producer],
        runtime: Ref,
    ) -> Producer:
        required = {str(runtime), *dependencies}
        closure = required.copy()
        for producer in dependencies.values():
            closure.update(producer.prerequisites)

        outputs = tuple(sorted(name for stage in stages for name in stage.recipe_outputs))
        with TemporaryDirectory(prefix="coreref-producer-") as temporary:
            directory = Path(temporary)
            layout = {
                "configuration": {},  # Older locked CoreRef runners still read this key.
                "stages": self._stage_inputs(directory, stages, bindings),
                "outputs": outputs,
                "bindings": {
                    target: (Ref(producer.ref).package_name, filename)
                    for target, (producer, filename) in bindings.items()
                },
            }
            (directory / "pipeline.json").write_text(
                json.dumps(layout, sort_keys=True), encoding="utf-8"
            )
            recipe = _PRODUCER_TEMPLATE.render(
                name=ref.package_name,
                version=ref.version,
                dependencies=sorted(required),
                runtime=runtime.package_name,
            )
            (directory / "conanfile.py").write_text(recipe, encoding="utf-8")
            exact = self._variants.publish_recipe(directory)
        return Producer(
            ref=str(exact),
            prerequisites=tuple(sorted(closure)),
            outputs=outputs,
        )

    def _stage_inputs(
        self, directory: Path, stages: tuple[RecipeStage, ...], bindings: Bindings
    ) -> list[JSON]:
        staged = directory / "inputs"
        staged.mkdir()
        available = set(bindings)
        sources: dict[str, Path] = {}
        commands: list[JSON] = []
        for stage in stages:
            for name in stage.inputs:
                source = stage.files.get(name)
                if name in available and source is not None and sources.get(name) != source:
                    raise ValueError(f"Conflicting input {name!r} in producer recipe")
                if name in available:
                    continue
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source or self._prepared / name, target)
                sources[name] = source or self._prepared / name
            available.update(stage.inputs)
            available.update(stage.spec.outputs)
            commands.append(
                {
                    "name": stage.spec.name,
                    "inputs": stage.inputs,
                    "outputs": tuple(sorted(stage.spec.outputs)),
                    "configuration": stage.configuration,
                    "transform": stage.transform,
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
) -> dict[str, tuple[Producer, str]]:
    inputs = {name for stage in stages for name in stage.inputs if name not in stage.files}
    produced = {name for stage in stages for name in stage.spec.outputs}
    explicit: dict[str, tuple[Producer, str]] = {}
    for stage in stages:
        for name, source in stage.artifacts.items():
            if explicit.setdefault(name, source) != source:
                raise ValueError(f"Conflicting artifact binding {name!r} in producer recipe")
    bindings = explicit.copy()
    for producer in published.values():
        for name in sorted((inputs & set(producer.outputs)) - produced - explicit.keys()):
            bindings[name] = (producer, name)
    return bindings
