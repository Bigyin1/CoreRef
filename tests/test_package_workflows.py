"""Multi-stage workflows keep snapshots usable across failures and new publications."""

import shutil
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from dulwich import porcelain
from dulwich.refs import Ref as GitRef
from dulwich.repo import Repo

from examples.local_server import LocalServer
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, Conflict, InvalidPackage
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import InstalledRecord, LocalRecord, Ref

pytestmark = pytest.mark.conan_remote


@dataclass
class SourceHistory:
    """One package can advance while readers retain its previous files and Git commit."""

    publisher: Catalog
    local: LocalRecord
    first: InstalledRecord
    origin: Path

    def advance(self) -> InstalledRecord:
        source = self.local.source
        assert source is not None
        (source / "cpu.sv").write_text("module cpu; wire updated; endmodule\n")
        porcelain.add(source, paths=["cpu.sv"])
        porcelain.commit(
            source,
            message=b"Update RTL",
            author=b"Tests <tests@example.invalid>",
            committer=b"Tests <tests@example.invalid>",
            sign=False,
        )
        porcelain.push(source, str(self.origin), refspecs="refs/heads/main", errstream=BytesIO())
        with Repo(source) as repo:
            repo.refs[GitRef(b"refs/remotes/origin/main")] = repo.head()
        self.local.files["report"].write_text("second")
        self.local.payload = {"generation": 2}
        return self.publisher.publish(self.local)


@pytest.fixture
def history(catalog: Catalog, checkout: Path, git_origin: Path, tmp_path: Path) -> SourceHistory:
    report = tmp_path / "report"
    report.write_text("first")
    local = LocalRecord(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
        {"generation": 1},
        source=checkout,
        files={"report": report},
    )
    return SourceHistory(catalog, local, catalog.publish(local), git_origin)


@pytest.fixture(params=["local", "published"])
def snapshot(
    history: SourceHistory,
    conan_server: LocalServer,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> Graph:
    reader = Catalog(conan_server.configure(tmp_path / "reader", local_sources=True))
    meta = history.publisher.publish(
        LocalRecord(
            Ref.from_fields("soc", "meta", "run", "default", "1.0", catalog=history.publisher)
        )
    )
    root = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=reader))
    requirements = (history.first.ref, meta.ref)
    if request.param == "local":
        return Graph(reader, root, dependencies=requirements)
    published = history.publisher.publish(root, dependencies=requirements)
    return Ref.from_fields(**published.ref.to_dict(), catalog=reader).graph()


def test_file_then_source_upgrade_survives_new_revision_and_lost_publisher(
    history: SourceHistory, snapshot: Graph, tmp_path: Path
) -> None:
    lock = snapshot.create_lockfile(tmp_path / "project.lock")
    before = snapshot.records
    with patch.object(snapshot.catalog._client, "run", wraps=snapshot.catalog._client.run) as run:
        files = snapshot.get((history.first.ref.without_revision(), history.first.ref))
        assert len(files) == 1 and files[0].source is None
        assert files[0].files["report"].read_text() == "first"
        second = history.advance()
        assert history.local.source is not None
        shutil.rmtree(history.local.source)
        shutil.rmtree(history.publisher._client.home)
        lock.write_text("The project lockfile is no longer usable")
        ready = snapshot.get_with_sources((history.first.ref.without_revision(),))[0]
        assert ready.ref == history.first.ref and ready.payload == {"generation": 1}
        assert ready.source is not None
        assert "updated" not in (ready.source / "cpu.sv").read_text()
        assert ready.files["report"].read_text() == "first"
        assert files[0].source is None  # The earlier InstalledRecord remains a snapshot too.
        assert snapshot.get((history.first.ref,))[0] is ready
        assert snapshot.records == before
        assert sum(call.args[0] == "install" for call in run.call_args_list) == 2
    latest = Ref.from_fields(
        **second.ref.without_revision().to_dict(), catalog=snapshot.catalog
    ).get_with_sources()
    assert latest.ref == second.ref and latest.source is not None
    assert "updated" in (latest.source / "cpu.sv").read_text()
    assert latest.files["report"].read_text() == "second"
    snapshot.create_lockfile()
    assert snapshot.lockfile == lock
    assert ready.ref.graph(lockfile=lock).record.ref == history.first.ref


def test_failed_git_upgrade_preserves_files_and_retries_after_origin_returns(
    history: SourceHistory, snapshot: Graph, tmp_path: Path
) -> None:
    with patch.object(snapshot.catalog._client, "run", wraps=snapshot.catalog._client.run) as run:
        files = snapshot.get((history.first.ref,))[0]
        history.origin.rename(tmp_path / "offline.git")
        with pytest.raises(CatalogError):
            snapshot.get_with_sources((history.first.ref,))
        assert snapshot.get((history.first.ref,))[0] is files
        assert files.source is None and files.files["report"].read_text() == "first"
        assert sum(call.args[0] == "install" for call in run.call_args_list) == 2
        (tmp_path / "offline.git").rename(history.origin)
        ready = snapshot.get_with_sources((history.first.ref, history.first.ref.without_revision()))
        assert len(ready) == 1 and ready[0].source is not None
        assert (ready[0].source / "cpu.sv").is_file()
        assert ready[0].files == files.files
        assert snapshot.get_with_sources((history.first.ref,))[0] is ready[0]
        assert sum(call.args[0] == "install" for call in run.call_args_list) == 3


@pytest.mark.parametrize("invalid", ["unknown", "revision"])
def test_mixed_valid_and_invalid_batch_does_no_io_and_does_not_poison_retry(
    history: SourceHistory, snapshot: Graph, invalid: str
) -> None:
    coordinates = history.first.ref.without_revision().to_dict()
    if invalid == "unknown":
        coordinates["component"] = "absent"
    else:
        coordinates["revision"] = "a" * 32
    bad = Ref.from_fields(**coordinates, catalog=snapshot.catalog)
    with patch.object(
        snapshot.catalog._client, "run", side_effect=AssertionError("Unexpected I/O")
    ):
        with pytest.raises(InvalidPackage):
            snapshot.get_with_sources(
                (history.first.ref, bad, history.first.ref.without_revision())
            )
        assert snapshot.get(()) == ()
    fetched = snapshot.get_with_sources((history.first.ref, history.first.ref.without_revision()))
    assert len(fetched) == 1 and fetched[0].ref == history.first.ref
    assert fetched[0].source is not None


def test_retry_publication_after_upload_failure_keeps_the_original_lock_choice(
    history: SourceHistory, tmp_path: Path
) -> None:
    local = LocalRecord(
        Ref.from_fields("soc", "result", "be", "lib-lef", "1.0", catalog=history.publisher)
    )
    graph = Graph(history.publisher, local, dependencies=(history.first.ref.without_revision(),))
    lock = graph.create_lockfile(tmp_path / "result.lock")
    with (
        patch.object(history.publisher._client, "upload", side_effect=CatalogError("offline")),
        pytest.raises(CatalogError, match="offline"),
    ):
        history.publisher.publish(
            local, dependencies=(history.first.ref.without_revision(),), lockfile=lock
        )
    assert history.publisher.search(SearchQuery(component="result")) == ()
    second = history.advance()
    published = history.publisher.publish(
        local, dependencies=(history.first.ref.without_revision(),), lockfile=lock
    )
    assert published.ref.dependencies() == (history.first,)
    assert second.ref not in {record.ref for record in published.ref.graph().records}
    assert len(history.publisher.search(SearchQuery(component="result"), all_revisions=True)) == 1
    assert graph.get_with_sources((history.first.ref,))[0].payload == {"generation": 1}


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_diamond_revisions_do_not_damage_independent_snapshots(
    history: SourceHistory, reverse: bool
) -> None:
    left = history.publisher.publish(
        LocalRecord(
            Ref.from_fields("soc", "left", "rtl", "default", "1.0", catalog=history.publisher)
        ),
        dependencies=(history.first.ref,),
    )
    existing = left.ref.graph()
    second = history.advance()
    right = history.publisher.publish(
        LocalRecord(
            Ref.from_fields("soc", "right", "rtl", "default", "1.0", catalog=history.publisher)
        ),
        dependencies=(second.ref,),
    )
    requirements = (right.ref, left.ref) if reverse else (left.ref, right.ref)
    with pytest.raises(Conflict):
        Graph(
            history.publisher,
            LocalRecord(
                Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=history.publisher)
            ),
            dependencies=requirements,
        )
    assert history.first.ref in {record.ref for record in existing.records}
    assert second.ref in {record.ref for record in right.ref.graph().records}
    assert existing.get_with_sources((history.first.ref,))[0].source is not None
