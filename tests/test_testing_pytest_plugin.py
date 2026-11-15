"""Dependent flows configure ordinary pytest fixtures without marker hooks."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]
pytestmark = pytest.mark.conan_remote


def test_downstream_plugin_fixtures_and_editable_project(pytester: pytest.Pytester) -> None:
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
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers")
    result.assert_outcomes(passed=1)


def test_options_fixture_configures_projects_and_parametrized_tests(
    pytester: pytest.Pytester,
) -> None:
    import shutil

    pytester.makeconftest("""
from pathlib import Path
import pytest
from coreref.testing.pytest_plugin import EnvironmentOptions

pytest_plugins = ["coreref.testing.pytest_plugin"]

@pytest.fixture
def coreref_options() -> EnvironmentOptions:
    return {
        "projects": Path("suite/test_projects"), "repositories": ("stdlib",),
        "namespace": "demo", "timeout": 0.2,
    }
""")
    suite = pytester.path / "suite"
    shutil.copytree(Path(__file__).with_name("test_projects"), suite / "test_projects")
    (suite / "test_flow.py").write_text("""
import pytest

@pytest.mark.parametrize("case", ["one", "two"])
def test_projects(case, coreref_environment, coreref_catalog):
    env = coreref_environment
    assert env.timeout == 0.2
    assert str(env.ref("cpu")) == "demo.cpu.rtl.default/1.0"
    assert (env.projects / "stdlib/.git").exists()
    assert not (env.projects / "cpu/.git").exists()
    source = env.projects / "cpu/rtl/cpu.sv"
    assert source.read_text().startswith("module cpu;")
    source.write_text(case)
""")
    result = pytester.runpytest_inprocess("suite/test_flow.py", "-q", "--strict-markers")
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
    pytester.makeconftest("""
from pathlib import Path
import pytest

pytest_plugins = ["coreref.testing.pytest_plugin"]

@pytest.fixture
def coreref_options():
    return {"projects": Path("missing")}
""")
    pytester.makepyfile("""
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
    ("options", "error"),
    [
        ("{'unknown': True}", "*TypeError*unexpected keyword*unknown*"),
        ("{'projects_base': 'root'}", "*TypeError*unexpected keyword*projects_base*"),
        ("{'repositories': ('cpu',)}", "*ValueError*repositories require projects*"),
    ],
)
def test_invalid_options_fail_during_setup(
    pytester: pytest.Pytester, options: str, error: str
) -> None:
    pytester.makeconftest(f"""
import pytest
pytest_plugins = ["coreref.testing.pytest_plugin"]

@pytest.fixture
def coreref_options():
    return {options}
""")
    pytester.makepyfile("""
def test_failure(coreref_environment):
    pass
""")
    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines([error])


def test_parametrized_options_fixture_selects_different_project_trees(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest("""
from pathlib import Path
import pytest

pytest_plugins = ["coreref.testing.pytest_plugin"]

@pytest.fixture(params=["one", "two"])
def coreref_options(request):
    return {"projects": Path(request.param), "namespace": request.param}
""")
    for name in ("one", "two"):
        directory = pytester.path / name
        directory.mkdir()
        (directory / "name.txt").write_text(name)
    pytester.makepyfile("""
def test_tree(coreref_environment):
    env = coreref_environment
    assert (env.projects / "name.txt").read_text() == env.namespace
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers")
    result.assert_outcomes(passed=2)


def test_root_relative_projects_and_session_options_are_shared_by_nested_tests(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest("""
from pathlib import Path
import pytest

pytest_plugins = ["coreref.testing.pytest_plugin"]

@pytest.fixture(scope="session")
def coreref_options():
    return {"projects": Path("test_projects")}
""")
    pytester.makeini("[pytest]")
    common = pytester.path / "test_projects"
    common.mkdir()
    (common / "name.txt").write_text("shared fixture")
    for name in ("rtl/unit", "backend/regression"):
        suite = pytester.path / name
        (suite / "test_projects").mkdir(parents=True)
        (suite / "test_projects/name.txt").write_text("wrong base directory")
        (suite / f"test_{suite.name}.py").write_text("""
from pathlib import Path

def test_shared_tree(coreref_environment, coreref_options):
    assert coreref_options == {"projects": Path("test_projects")}
    copied = coreref_environment.projects / "name.txt"
    assert copied.read_text() == "shared fixture"
    copied.write_text("local edit")
""")
    result = pytester.runpytest_inprocess("rtl", "backend", "-q", "--strict-markers")
    result.assert_outcomes(passed=2)
    assert (common / "name.txt").read_text() == "shared fixture"


def test_absolute_project_path_can_be_configured_by_a_module_fixture(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    source = pytester.path / "shared"
    source.mkdir()
    (source / "name.txt").write_text("absolute fixture")
    pytester.makepyfile(f"""
import pytest
from pathlib import Path

@pytest.fixture
def coreref_options():
    return {{"projects": Path({str(source)!r})}}

def test_absolute(coreref_environment):
    assert (coreref_environment.projects / "name.txt").read_text() == "absolute fixture"
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers")
    result.assert_outcomes(passed=1)


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


def test_user_markers_only_select_tests(pytester: pytest.Pytester) -> None:
    pytester.makeconftest("""
import pytest
pytest_plugins = ["coreref.testing.pytest_plugin"]

@pytest.fixture
def coreref_options():
    return {"namespace": "configured", "timeout": 0.2}
""")
    pytester.makeini("[pytest]\nmarkers = flow: downstream test group")
    pytester.makepyfile("""
import pytest

@pytest.mark.flow(namespace="ignored")
def test_flow(coreref_environment):
    assert coreref_environment.namespace == "configured"
    assert coreref_environment.timeout == 0.2

def test_unmarked(coreref_environment):
    raise AssertionError("An unmarked test must be deselected by -m flow")
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers", "-m", "flow")
    result.assert_outcomes(passed=1, deselected=1)


def test_default_options_need_no_markers_or_catalog(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makepyfile("""
def test_defaults(coreref_environment, coreref_options, request):
    env = coreref_environment
    assert coreref_options == {}
    assert env.namespace == "test"
    assert env.timeout == 5
    assert request.node.get_closest_marker("coreref") is None
    assert list(env.root.glob("client-*")) == []
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers")
    result.assert_outcomes(passed=1)
