"""Check publication, selective retrieval, and on-demand outputs through the SDK."""

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest

from coreref._internal.artifacts import Producer
from coreref._internal.snapshot import SliceInputs
from coreref._internal.variants import Variants
from coreref.api.build import Outputs
from coreref.api.errors import InvalidPackage, NotFound
from coreref.api.packages import Catalog, LocalRecord, Record, Ref
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


_DIGEST = "a" * 64
_ARTIFACT = f"coreref-artifact.file/{_DIGEST}#revision"


@pytest.mark.parametrize(
    ("name", "ref", "message"),
    [
        ("../outside.lef", _ARTIFACT, "Invalid output name"),
        ("views//cell.lef", _ARTIFACT, "Invalid output name"),
        ("layout.lef", f"coreref-artifact.file/{_DIGEST}", "exact content-addressed reference"),
        ("layout.lef", f"coreref-artifact.file/{'b' * 64}#revision", "content-addressed reference"),
        ("layout.lef", f"demo.file/{_DIGEST}#revision", "content-addressed reference"),
    ],
)
def test_outputs_rejects_invalid_index_before_installation(
    tmp_path: Path, name: str, ref: str, message: str
) -> None:
    record = Record(
        Ref("demo.macro/1.0#published"),
        {
            "output_index": {
                "key": _DIGEST,
                "outputs": {name: {"ref": ref, "sha256": _DIGEST, "size": 1}},
            }
        },
    )
    outputs = Outputs(Catalog(tmp_path / "home"))
    with trace_operations() as operations, pytest.raises(ValueError, match=message):
        outputs.get(record, (name,))
    assert not operations.commands
    assert operations.package_downloads == 0


def test_indexed_output_fetch_can_be_retried_after_the_exact_artifact_is_uploaded(
    tmp_path: Path,
) -> None:
    with environment(tmp_path) as env:
        _, inputs = _project(env)
        source = tmp_path / "layout.lef"
        source.write_text("macro layout")
        record = Outputs(env.catalog)._pack(inputs, {source.name: source}, {})
        links = Outputs.links(record)
        assert links is not None
        artifact_ref = links[source.name]
        consumer = Outputs(env.new_catalog())

        with pytest.raises(NotFound, match="Missing indexed artifact") as missing:
            consumer.get(record, (source.name,))
        assert str(artifact_ref) in str(missing.value)

        uploaded = env.publish(LocalRecord(artifact_ref.without_revision(), files={"data": source}))
        assert uploaded.ref == artifact_ref
        assert consumer.get(record, (source.name,))[source.name].read_text() == "macro layout"


def test_outputs_with_same_names_keep_their_own_artifact_paths(tmp_path: Path) -> None:
    with environment(tmp_path) as env:
        names = ("abstract.lef", "timing.lib")
        cpu, dma = (
            _publish_views(env, component, (*names, "final.gds")) for component in ("cpu", "dma")
        )
        reader = Outputs(env.new_catalog())
        with trace_operations() as operations:
            cpu_files = reader.get(cpu, names)
            dma_files = reader.get(dma, names)
            assert reader.get(cpu, names) == cpu_files
        for record, files in ((cpu, cpu_files), (dma, dma_files)):
            for name in names:
                assert files[name].read_text() == f"{record.ref.component}:{name}"
        assert operations.package_downloads == 4


def _publish_views(env: Sandbox, component: str, names: tuple[str, ...]) -> Record:
    graph = Graph(env.catalog, LocalRecord(env.ref(component)))
    lock = graph.create_lockfile(env.root / f"{component}.lock")
    inputs = SliceInputs(graph.record.ref, "first", lock, (), {})
    files = env.package(
        graph.record.ref, files={name: f"{component}:{name}" for name in names}
    ).files
    return Outputs(env.catalog)._publish(graph, inputs, files, {})


def test_second_artifact_upload_failure_keeps_the_previous_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with environment(tmp_path) as env:
        names = ("abstract.lef", "timing.lib")
        first = _publish_views(env, "block", names)
        publish = _second_release(env, names)
        uploaded = _interrupt_upload(env.catalog, monkeypatch, publish)
        reader = env.new_catalog()
        assert reader.get(uploaded).files["data"].read_text().startswith("second:")
        assert reader.info(first.ref.without_revision()).ref == first.ref
        locked = Graph(reader, first.ref.without_revision()).create_lockfile(
            tmp_path / "consumer.lock"
        )
        assert _read_views(reader, first, names) == {name: f"block:{name}" for name in names}

        second = publish()
        cold = env.new_catalog()
        assert cold.info(first.ref.without_revision()).ref == second.ref
        assert _read_views(cold, second, names) == {name: f"second:{name}" for name in names}
        pinned = Graph(cold, first.ref.without_revision(), lockfile=locked).record
        assert isinstance(pinned, Record) and pinned.ref == first.ref
        assert _read_views(cold, pinned, names) == {name: f"block:{name}" for name in names}


def _second_release(env: Sandbox, names: tuple[str, ...]) -> Callable[[], Record]:
    graph, inputs = _project(env)
    files = env.package(graph.record.ref, files={name: f"second:{name}" for name in names}).files
    return partial(
        Outputs(env.catalog)._publish, graph, replace(inputs, commit="second"), files, {}
    )


def _interrupt_upload(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch, publish: Callable[[], Record]
) -> Ref:
    upload = catalog._upload
    uploaded: list[Ref] = []

    def fail_second(ref: Ref) -> None:
        if uploaded:
            raise RuntimeError("Second upload interrupted")
        upload(ref)
        uploaded.append(ref)

    with monkeypatch.context() as patch:
        patch.setattr(catalog, "_upload", fail_second)
        with pytest.raises(RuntimeError, match="Second upload interrupted"):
            publish()
    (first,) = uploaded
    return first


def _read_views(catalog: Catalog, record: Record, names: tuple[str, ...]) -> dict[str, str]:
    return {name: path.read_text() for name, path in Outputs(catalog).get(record, names).items()}


def test_zero_byte_drc_report_is_a_present_output(tmp_path: Path) -> None:
    with environment(tmp_path) as env:
        graph, inputs = _project(env)
        names = ("routing_drc.rpt", "timing.rpt")
        files = env.package(
            graph.record.ref, files={"routing_drc.rpt": "", "timing.rpt": "timing"}
        ).files
        record = Outputs(env.catalog)._publish(graph, inputs, files, {})
        cold = env.new_catalog()
        metadata = cold.info(record.ref)
        reader = Outputs(cold)
        assert reader.available(metadata) == set(names)
        assert _read_views(cold, metadata, names) == {"routing_drc.rpt": "", "timing.rpt": "timing"}
        with pytest.raises(ValueError, match=r"missing outputs.*absent\.rpt"):
            reader.get(metadata, ("absent.rpt",))
