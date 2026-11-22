"""Exercise local packing and exact artifact links with a real Conan registry."""

from dataclasses import replace
from pathlib import Path

import pytest

from coreref._internal.artifacts import ArtifactStore, OutputIndex
from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage, NotFound
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref
from coreref.testing import LocalServer, configured_home
from coreref.tools.artifacts import SliceInputs, file_digest
from coreref.tools.stage_cache import CacheMode, StageCache, StageInvocation


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


def test_index_rejects_inconsistent_and_corrupted_artifact(tmp_path: Path) -> None:
    catalog = Catalog(configured_home(tmp_path / "home"))
    path = tmp_path / "source"
    path.write_text("netlist")
    store = ArtifactStore(catalog)
    original = store.pack(path)
    previous = OutputIndex(key="1" * 64, outputs={"netlist.v": original})
    assert previous.outputs["netlist.v"].sha256 == file_digest(path)
    path.write_text("updated")
    changed = store.pack(path)
    next_index = OutputIndex(key=previous.key, outputs={"netlist.v": changed})
    with pytest.raises(ValueError, match="Previously published outputs changed"):
        next_index.extend(previous)

    installed = catalog.get(Ref(original.ref))
    installed.files["data"].write_text("garbage")
    with pytest.raises(ValueError, match="Artifact verification failed"):
        store.get(original)
