"""Exercise local packing and exact artifact links with a real Conan registry."""

from dataclasses import replace
from pathlib import Path
from shutil import rmtree

import pytest

from coreref._internal.artifacts import ArtifactStore, OutputIndex
from coreref._internal.cache import StageCache, StageInvocation
from coreref._internal.hashing import file_digest
from coreref._internal.snapshot import SliceInputs
from coreref.api.build import CacheMode
from coreref.api.errors import InvalidPackage, NotFound
from coreref.api.packages import Catalog, LocalRecord, Ref, SearchQuery
from coreref.testing import LocalServer, configured_home


def test_pack_is_local_and_upload_requires_exact_cached_ref(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    publisher = Catalog(conan_server.configure(tmp_path / "publisher"))
    consumer = Catalog(conan_server.configure(tmp_path / "consumer"))
    path = tmp_path / "data"
    path.write_text("hello")
    record = LocalRecord(Ref("demo.package/1.0"), files={"data": path})

    installed = publisher._pack(record)
    assert installed.ref.revision is not None
    assert consumer.search_refs(SearchQuery(namespace="demo")) == ()
    assert publisher.get(installed.ref).files["data"].read_text() == "hello"
    with pytest.raises(InvalidPackage, match="exact"):
        publisher._upload(record.ref)
    with pytest.raises(NotFound):
        consumer._upload(installed.ref)

    publisher._upload(installed.ref)
    assert consumer.get(installed.ref).files["data"].read_text() == "hello"

    offline = Catalog(configured_home(tmp_path / "offline"))
    assert offline._pack(record).ref == installed.ref
    assert offline.get(installed.ref).files["data"].read_text() == "hello"


def test_stage_keys_use_staged_bytes_and_tool_identity(tmp_path: Path) -> None:
    directories = (tmp_path / "one", tmp_path / "two")
    for directory in directories:
        directory.mkdir()
        (directory / "rtl.v").write_text("module design; endmodule")
        (directory / "stage.tcl").write_text("synth")
    first, second = directories
    tool = StageInvocation(
        "synth", ("yosys", "-c", "stage.tcl"), f"tool@sha256:{'a' * 64}", "linux/amd64"
    )
    key = tool._key(first, ("rtl.v", "stage.tcl"))
    (second / "downstream.tcl").write_text("change only post-synthesis processing")
    assert key == tool._key(second, ("stage.tcl", "rtl.v"))
    assert key != replace(tool, platform="linux/arm64")._key(first, ("rtl.v", "stage.tcl"))
    assert key != replace(tool, image=f"tool@sha256:{'b' * 64}")._key(first, ("rtl.v", "stage.tcl"))
    (second / "stage.tcl").write_text("synth; opt")
    assert key != tool._key(second, ("rtl.v", "stage.tcl"))


def test_slice_key_tracks_inputs_and_ignores_output_selection_at_a_commit(tmp_path: Path) -> None:
    lockfile = tmp_path / "manifest.lock"
    lockfile.write_text("locked revisions")
    artifact = tmp_path / "netlist.v"
    artifact.write_text("first contents")
    inputs = SliceInputs(
        project=Ref("demo.soc/1.0"),
        commit="fixed",
        lockfile=lockfile,
        dependencies=(Ref(f"demo.block/1.0#{'a' * 32}"),),
        environment={"python": "3.13.14"},
    )
    committed = inputs.key()
    assert replace(inputs, local_outputs={"netlist.v": artifact}).key() == committed
    uncommitted = replace(inputs, commit=None, local_outputs={"netlist.v": artifact})
    previous = uncommitted.key()
    artifact.write_text("updated contents")
    assert uncommitted.key() != previous
    lockfile.write_text("updated revisions")
    assert inputs.key() != committed


def test_content_slice_keys_separate_declared_inputs_and_dirty_outputs(tmp_path: Path) -> None:
    lockfile = tmp_path / "manifest.lock"
    lockfile.write_text("locked revisions")
    rtl = tmp_path / "rtl.v"
    rtl.write_text("RTL")
    inputs = SliceInputs(
        project=Ref("demo.soc/1.0"),
        commit="first",
        lockfile=lockfile,
        dependencies=(),
        environment={},
        input_files={"rtl.v": rtl, "constraints.sdc": None},
    )
    assert inputs.key() == replace(inputs, commit="second").key()
    assert inputs.key() != replace(inputs, input_files={"rtl.v": rtl}).key()
    assert inputs.key() != replace(inputs, commit=None, local_outputs={"rtl.v": rtl}).key()
    previous = inputs.key()
    rtl.write_text("new RTL")
    assert inputs.key() != previous


def test_shared_stage_cache_and_content_deduplication(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    catalog = Catalog(conan_server.configure(tmp_path / "writer"))
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    (tmp_path / "netlist.v").write_text("netlist")
    (tmp_path / "another-name.v").write_text("netlist")
    files = {"netlist.v": tmp_path / "netlist.v", "other.v": tmp_path / "another-name.v"}
    index = ArtifactStore(catalog).pack_index("1" * 64, files)
    assert index.outputs["netlist.v"] == index.outputs["other.v"]

    restored = tmp_path / "restored"
    restored.mkdir()
    outputs = ("netlist.v",)
    invocation = StageInvocation("synth", ("yosys",), f"tool@sha256:{'a' * 64}", "linux/amd64")
    read = StageCache(reader, CacheMode.READ)
    write = StageCache(catalog, CacheMode.WRITE)

    def build() -> None:
        (restored / "netlist.v").write_text("netlist")

    def fail() -> None:
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        write.run(invocation, restored, inputs=(), outputs=outputs, action=fail)
    assert read.run(invocation, restored, inputs=(), outputs=outputs, action=build) is None
    assert not reader.search_refs(SearchQuery(namespace="coreref-stage"))
    assert write.run(invocation, restored, inputs=(), outputs=outputs, action=build) is None

    (restored / "netlist.v").unlink()
    assert read.run(invocation, restored, inputs=(), outputs=outputs, action=fail)
    assert (restored / "netlist.v").read_text() == "netlist"
    assert (
        StageCache(reader, CacheMode.OFF).run(
            invocation, restored, inputs=("absent",), outputs=outputs, action=build
        )
        is None
    )
    with pytest.raises(ValueError, match="Invalid shared cache index"):
        read.run(invocation, restored, inputs=(), outputs=("some-other-path",), action=fail)
    assert reader.search_refs(SearchQuery(namespace="coreref-artifact")) == (
        Ref(index.outputs["netlist.v"].ref),
    )


def test_reading_an_indexed_artifact_rejects_corruption(tmp_path: Path) -> None:
    catalog = Catalog(configured_home(tmp_path / "home"))
    path = tmp_path / "source"
    path.write_text("netlist")
    store = ArtifactStore(catalog)
    original = store.pack(path)
    previous = OutputIndex(key="1" * 64, outputs={"netlist.v": original})
    assert previous.outputs["netlist.v"].sha256 == file_digest(path)
    installed = catalog.get(Ref(original.ref))
    installed.files["data"].write_text("garbage")
    with pytest.raises(ValueError, match="Artifact verification failed"):
        store.get(original)


def test_shared_cache_refuses_multiple_revisions_for_one_key(
    catalog: Catalog, tmp_path: Path
) -> None:
    invocation = StageInvocation("synth", ("yosys",), f"tool@sha256:{'a' * 64}", "linux/amd64")
    output = tmp_path / "netlist.v"

    def build() -> None:
        output.write_text("cached result")

    StageCache(catalog, CacheMode.WRITE).run(
        invocation, tmp_path, inputs=(), outputs=("netlist.v",), action=build
    )
    (entry,) = catalog.search_refs(SearchQuery(namespace="coreref-stage"))
    catalog.publish(LocalRecord(entry.without_revision(), {"unexpected": "different revision"}))
    output.write_text("working result")

    def unexpected_build() -> None:
        pytest.fail("Conflicting cache entries must stop the operation")

    with pytest.raises(ValueError, match="Conflicting shared cache entries"):
        StageCache(catalog, CacheMode.READ).run(
            invocation, tmp_path, inputs=(), outputs=("netlist.v",), action=unexpected_build
        )
    assert output.read_text() == "working result"


def test_failed_multi_output_cache_restore_keeps_previous_files(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    publisher = Catalog(conan_server.configure(tmp_path / "publisher"))
    consumer = Catalog(conan_server.configure(tmp_path / "consumer"))
    invocation = StageInvocation("route", ("openroad",), f"tool@sha256:{'a' * 64}", "linux/amd64")
    names = ("abstract.lef", "timing.lib")

    def write_outputs(label: str) -> None:
        for name in names:
            (tmp_path / name).write_text(f"{label} {name}")

    def no_build() -> None:
        pytest.fail("An invalid cache hit must surface the verification failure")

    StageCache(publisher, CacheMode.WRITE).run(
        invocation, tmp_path, inputs=(), outputs=names, action=lambda: write_outputs("new")
    )
    (entry,) = consumer.search_refs(SearchQuery(namespace="coreref-stage"))
    index = OutputIndex.from_record(consumer.info(entry))
    assert index is not None
    installed = consumer.get(Ref(index.outputs[names[1]].ref))
    timing = installed.files["data"]
    timing.write_text("broken")
    write_outputs("old")

    cache = StageCache(consumer, CacheMode.READ)
    with pytest.raises(ValueError, match="Artifact verification failed"):
        cache.run(invocation, tmp_path, inputs=(), outputs=names, action=no_build)
    assert [(tmp_path / name).read_text() for name in names] == [f"old {name}" for name in names]
    timing.write_text("new timing.lib")
    assert cache.run(invocation, tmp_path, inputs=(), outputs=names, action=no_build)
    assert [(tmp_path / name).read_text() for name in names] == [f"new {name}" for name in names]


def test_stage_cache_survives_moving_between_checkouts(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    writer = Catalog(conan_server.configure(tmp_path / "writer"))
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer checkout"
    for checkout in (producer, consumer):
        checkout.mkdir()
        (checkout / "constraints.sdc").write_text("create_clock -period 10")
    invocation = StageInvocation(
        "synth", ("yosys", "synth.tcl"), f"tool@sha256:{'a' * 64}", "linux/amd64"
    )
    files = ("constraints.sdc",)
    names = ("netlist.v",)

    def synthesize() -> None:
        (producer / names[0]).write_bytes((producer / files[0]).read_bytes())

    def no_build() -> None:
        pytest.fail("The second checkout should reuse the stage")

    StageCache(writer, CacheMode.WRITE).run(
        invocation, producer, inputs=files, outputs=names, action=synthesize
    )
    rmtree(producer)
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    assert StageCache(reader, CacheMode.READ).run(
        invocation, consumer, inputs=files, outputs=names, action=no_build
    )
    assert (consumer / names[0]).read_text() == "create_clock -period 10"
