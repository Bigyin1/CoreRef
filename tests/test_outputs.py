"""Check publication, selective retrieval, and on-demand outputs through the SDK."""

from dataclasses import replace
from pathlib import Path

import pytest

from coreref._internal.artifacts import Producer
from coreref._internal.snapshot import SliceInputs
from coreref._internal.variants import Variants
from coreref.api.build import Outputs
from coreref.api.errors import InvalidPackage, NotFound
from coreref.api.packages import Catalog, LocalRecord, Ref
from coreref.api.projects import Graph
from coreref.testing import Sandbox, environment
from coreref.testing.operations import trace_operations


def _project(env: Sandbox) -> tuple[Graph, SliceInputs]:
    graph = Graph(env.catalog, LocalRecord(env.ref("block"), payload={"author": "flow"}))
    lock = graph.create_lockfile(env.root / "build.lock")
    return graph, SliceInputs(graph.record.ref, "first", lock, (), {})


def test_publish_extends_same_slice_across_other_revisions(tmp_path: Path) -> None:
    with environment(tmp_path) as env:
        graph, inputs = _project(env)
        output = tmp_path / "output"
        output.write_text("LEF")
        store = Outputs(env.catalog)
        local = store._pack(inputs, {"abstract.lef": output}, {})
        assert store.get(local, ("abstract.lef",))["abstract.lef"].read_text() == "LEF"
        first = store._publish(graph, inputs, {"abstract.lef": output}, {"type": "macro"})
        assert graph.record.payload == {"author": "flow"}
        assert isinstance(graph.record, LocalRecord) and not graph.record.files
        assert str(first.ref) in env.diagnostics()

        output.write_text("Liberty")
        store._publish(graph, replace(inputs, commit="other"), {"timing.lib": output}, {})
        assert env.new_catalog().info(first.ref.without_revision()).ref != first.ref
        updated = store._publish(graph, inputs, {"timing.lib": output}, {})
        with trace_operations() as operations:
            links = store.links(updated)
        assert links is not None and set(links) == {"abstract.lef", "timing.lib"}
        assert all(ref.revision is not None for ref in links.values())
        assert not operations.commands
        consumer = Outputs(env.new_catalog())
        assert consumer.get(first, ()) == {}
        paths = consumer.get(updated, ("timing.lib", "abstract.lef"))
        assert list(map(Path.read_text, paths.values())) == ["Liberty", "LEF"]
        with pytest.raises(ValueError, match="Previously published"):
            store._publish(graph, inputs, {"abstract.lef": output}, {})


def test_outputs_reject_uncommitted_or_mismatched_project(tmp_path: Path, catalog: Catalog) -> None:
    local = LocalRecord(Ref("demo.block/1.0"))
    graph = Graph(catalog, local)
    lock = graph.create_lockfile(tmp_path / "lock")
    inputs = SliceInputs(local.ref, None, lock, (), {})
    with pytest.raises(InvalidPackage, match="matching, committed project"):
        Outputs(catalog)._publish(graph, inputs, {}, {})
    with pytest.raises(InvalidPackage, match="local graph for this catalog"):
        Outputs(Catalog(tmp_path / "another-home"))._publish(graph, inputs, {}, {})
    release = catalog.publish(local)
    installed = Graph(catalog, release.ref)
    with pytest.raises(InvalidPackage, match="local graph"):
        Outputs(catalog)._publish(installed, inputs, {}, {})


def _producer(env: Sandbox) -> Producer:
    directory = env.root / "recipe"
    directory.mkdir()
    (directory / "conanfile.py").write_text(
        "from conan import ConanFile\n"
        "from conan.tools.files import save\n"
        "class Layout(ConanFile):\n"
        "    name = 'coreref-producer.layout'\n"
        "    version = '1.0'\n"
        "    def package(self):\n"
        "        save(self, self.package_folder + '/abstract.lef', 'LEF')\n"
        "        save(self, self.package_folder + '/timing.lib', 'Liberty')\n"
    )
    return Producer(ref=str(Variants(env.catalog).publish_recipe(directory)))


def test_get_uses_only_registered_producers_and_checks_overlapping_outputs(tmp_path: Path) -> None:
    with environment(tmp_path) as env:
        graph, inputs = _project(env)
        producer = _producer(env)
        output = tmp_path / "output"
        output.write_text("LEF")
        store = Outputs(env.catalog)
        index = store._publish(
            graph, inputs, {"abstract.lef": output}, {}, producers={"layout": producer}
        )
        assert Outputs._producers(index) == {"layout": producer}
        assert store._publish(graph, inputs, {}, {}).ref == index.ref

        reader = env.new_catalog()
        groups = {"layout": ("abstract.lef", "timing.lib", "unproduced")}
        with pytest.raises(NotFound, match="Producer binary unavailable"):
            Outputs(reader, groups=groups).get(index, ("timing.lib",))
        with pytest.raises(ValueError, match="missing outputs"):
            Outputs(reader).get(index, ("unknown",))
        outputs = Outputs(reader, groups=groups, build_missing=True)
        with trace_operations() as trace:
            assert outputs.available(index, producer_only=True) == set(groups["layout"])
            assert outputs.available(index) == set(groups["layout"])
            assert Outputs(reader).available(index) == {"abstract.lef"}
        assert not trace.commands
        assert outputs.get(index, ("timing.lib",))["timing.lib"].read_text() == "Liberty"
        with pytest.raises(ValueError, match="did not produce"):
            outputs.get(index, ("unproduced",))

        different = Producer(ref=str(producer.ref).replace("layout", "different"))
        with pytest.raises(ValueError, match="Producer recipes changed"):
            store._publish(graph, inputs, {}, {}, producers={"layout": different})


def test_plain_packaged_files_remain_available(catalog: Catalog, tmp_path: Path) -> None:
    path = tmp_path / "timing.lib"
    path.write_text("liberty")
    package = catalog.publish(LocalRecord(Ref("demo.macro/1.0"), files={path.name: path}))
    files = Outputs(catalog).get(catalog.info(package.ref), (path.name,))
    assert files[path.name].read_text() == "liberty"
    assert Outputs.links(package) is None
    graph = Graph(catalog, LocalRecord(Ref("demo.local/1.0")))
    lock = graph.create_lockfile(tmp_path / "manifest.lock")
    inputs = SliceInputs(graph.record.ref, None, lock, (), {})
    assert Outputs.links(Outputs(catalog)._pack(inputs, {}, {})) == {}
