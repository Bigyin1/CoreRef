"""A dependent repository can opt into fixtures with one pytest_plugins entry."""

import pytest

pytest_plugins = ["pytester"]
pytestmark = pytest.mark.conan_remote


def test_downstream_plugin_fixtures_marker_and_editable_project(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makepyfile("""
def test_flow(coreref_environment, coreref_catalog, coreref_project, coreref_server, monkeypatch):
    env = coreref_environment
    assert coreref_server is env.server
    assert coreref_catalog is env.catalog
    cpu = env.package(env.ref("cpu"), sources={"cpu.sv": "RTL"})
    published = env.publish(cpu)
    coreref_project.add(published.ref.without_revision())
    coreref_project.save()
    assert coreref_project.resolve(coreref_catalog).get_with_sources()[0].source is not None
    workspace = env.workspace(directory=coreref_project.path.parent)
    workspace.add(cpu.source, record=cpu)
    monkeypatch.chdir(coreref_project.path.parent)
    assert coreref_project.resolve(coreref_catalog).get_with_sources()[0].is_editable
    assert len(list(env.root.glob("client-*"))) == 1

def test_unrelated():
    assert True
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers", "-m", "coreref")
    result.assert_outcomes(passed=1, deselected=1)


def test_marker_options_are_relative_to_test_file_and_support_parametrization(
    pytester: pytest.Pytester,
) -> None:
    import shutil
    from pathlib import Path

    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    suite = pytester.path / "suite"
    shutil.copytree(Path(__file__).with_name("test_projects"), suite / "test_projects")
    (suite / "test_flow.py").write_text("""
import pytest

@pytest.mark.coreref(
    projects="test_projects", repositories=("stdlib",), namespace="demo", timeout=0.2
)
class TestFlow:
    @pytest.mark.parametrize("case", ["one", "two"])
    def test_projects(self, case, coreref_environment, coreref_catalog):
        env = coreref_environment
        assert env.timeout == 0.2
        assert str(env.ref("cpu")) == "demo.cpu.rtl.default/1.0"
        assert (env.projects / "stdlib/.git").exists()
        assert not (env.projects / "cpu/.git").exists()
        source = env.projects / "cpu/rtl/cpu.sv"
        assert source.read_text().startswith("module cpu;")
        source.write_text(case)
""")
    result = pytester.runpytest_inprocess(
        "suite/test_flow.py", "-q", "--strict-markers", "-m", "coreref"
    )
    result.assert_outcomes(passed=2)


def test_failed_test_report_contains_paths_and_published_refs(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makepyfile("""
def test_failure(coreref_environment, coreref_catalog):
    env = coreref_environment
    coreref_catalog.publish(env.package(env.ref("cpu")))
    assert False, "intended failure"
""")
    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*CoreRef environment*",
            "Root: *",
            "Projects: *",
            "Server URL: *",
            "Server log: *server.log",
            "Conan home: *",
            "Published: test.cpu.rtl.default/1.0#*",
        ]
    )


def test_setup_failure_still_reports_the_created_environment(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makepyfile("""
import pytest

@pytest.mark.coreref(projects="missing")
def test_failure(coreref_environment):
    pass
""")
    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*CoreRef environment*", "Root: *", "Server log: *server.log"])


def test_teardown_failure_reports_environment_after_cleanup(pytester: pytest.Pytester) -> None:
    pytester.makeconftest("""
import pytest
pytest_plugins = ["coreref.testing.pytest_plugin"]

@pytest.fixture
def bad_teardown(coreref_environment):
    yield
    raise RuntimeError("intended teardown failure")
""")
    pytester.makepyfile("""
def test_ok(bad_teardown):
    pass
""")
    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*CoreRef environment*", "Root: *", "Server log: *server.log"])


@pytest.mark.parametrize(
    "options",
    [
        "unknown=True",
        "timeout=-1",
        "repositories='cpu'",
        "'positional'",
        "projects_base='cwd'",
        "projects_base=42",
    ],
)
def test_invalid_marker_options_are_setup_errors(pytester: pytest.Pytester, options: str) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makepyfile(f"""
import pytest

@pytest.mark.coreref({options})
def test_failure(coreref_environment):
    pass
""")
    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*UsageError*coreref*"])


def test_parametrized_marker_selects_different_project_trees(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    for name in ("one", "two"):
        directory = pytester.path / name
        directory.mkdir()
        (directory / "name.txt").write_text(name)
    pytester.makepyfile("""
import pytest

@pytest.mark.parametrize("expected", [
    pytest.param("one", marks=pytest.mark.coreref(projects="one")),
    pytest.param("two", marks=pytest.mark.coreref(projects="two")),
])
def test_tree(expected, coreref_environment):
    assert (coreref_environment.projects / "name.txt").read_text() == expected
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers", "-m", "coreref")
    result.assert_outcomes(passed=2)


def test_root_relative_projects_are_shared_by_nested_test_modules(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makeini("[pytest]")
    common = pytester.path / "test_projects"
    common.mkdir()
    (common / "name.txt").write_text("shared fixture")
    for name in ("rtl/unit", "backend/regression"):
        suite = pytester.path / name
        (suite / "test_projects").mkdir(parents=True)
        (suite / "test_projects/name.txt").write_text("wrong base directory")
        (suite / f"test_{suite.name}.py").write_text("""
import pytest

pytestmark = pytest.mark.coreref(projects="test_projects", projects_base="root")

def test_shared_tree(coreref_environment):
    copied = coreref_environment.projects / "name.txt"
    assert copied.read_text() == "shared fixture"
    copied.write_text("local edit")
""")
    result = pytester.runpytest_inprocess(
        "rtl", "backend", "-q", "--strict-markers", "-m", "coreref"
    )
    result.assert_outcomes(passed=2)
    assert (common / "name.txt").read_text() == "shared fixture"


def test_absolute_project_path_is_independent_of_marker_base(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    source = pytester.path / "shared"
    source.mkdir()
    (source / "name.txt").write_text("absolute fixture")
    pytester.makepyfile(f"""
import pytest
from pathlib import Path

@pytest.mark.parametrize("base", [
    pytest.param("test", marks=pytest.mark.coreref(projects=Path({str(source)!r}))),
    pytest.param("root", marks=pytest.mark.coreref(
        projects=Path({str(source)!r}), projects_base="root"
    )),
])
def test_absolute(base, coreref_environment):
    assert (coreref_environment.projects / "name.txt").read_text() == "absolute fixture"
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers")
    result.assert_outcomes(passed=2)


def test_project_fixture_does_not_create_an_unused_catalog(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makepyfile("""
def test_project(coreref_project, coreref_environment):
    assert coreref_project.path.is_file()
    assert coreref_project.record.source == coreref_project.path.parent
    assert list(coreref_environment.root.glob("client-*")) == []
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers")
    result.assert_outcomes(passed=1)
