"""The switch that measures the running bot.

The switch is only worth having if it is impossible to trip by accident and
impossible to be brought down by, so most of what follows is about staying out
of the way: unset, half-set, or set to somewhere unwritable.
"""

from __future__ import annotations

import asyncio
import sys

import coverage
import pytest

from shenzhen.bot import main, runtime_coverage


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Neither the test runner's own coverage nor a developer's env leaks in."""
    for name in ("SHENZHEN_COVERAGE", "SHENZHEN_COVERAGE_BRANCH", "COVERAGE_RUN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)
    monkeypatch.delenv("COVERAGE_CORE", raising=False)


def datasets(root):
    """The dataset directories under a data root, ignoring the `current` link."""
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink())


@pytest.fixture
def execs(monkeypatch):
    """Catch the re-exec instead of replacing the test runner with a bot."""
    calls = []
    monkeypatch.setattr(runtime_coverage.os, "execv", lambda path, argv: calls.append(argv))
    return calls


def test_nothing_happens_when_nobody_asked_for_coverage(execs, monkeypatch):
    runtime_coverage.reexec_if_requested()
    assert execs == []
    assert "COVERAGE_PROCESS_START" not in runtime_coverage.os.environ


def test_an_empty_setting_counts_as_off(execs, monkeypatch):
    # env_file lines like `SHENZHEN_COVERAGE=` arrive as an empty string, not
    # as an absent variable, and must not turn measurement on in a temp dir.
    monkeypatch.setenv("SHENZHEN_COVERAGE", "   ")
    runtime_coverage.reexec_if_requested()
    assert execs == []


def test_it_re_executes_itself_under_coverage(execs, monkeypatch, tmp_path):
    root = tmp_path / "data"
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(root))
    runtime_coverage.reexec_if_requested()

    (argv,) = execs
    config = runtime_coverage.dataset_dir(root, branch=False) / runtime_coverage.CONFIG_NAME
    assert config.exists()
    assert argv[1:] == ["-m", "coverage", "run", f"--rcfile={config}", "-m", "shenzhen.bot.main"]
    assert argv[0] == sys.executable
    # The solver's workers inherit no Python state, only the environment.
    assert runtime_coverage.os.environ["COVERAGE_PROCESS_START"] == str(config)
    # And the documented commands can name the dataset without knowing which
    # revision of the source is deployed.
    assert (root / runtime_coverage.CURRENT_LINK).resolve() == config.parent.resolve()


def test_the_measured_process_does_not_re_execute_again(execs, monkeypatch, tmp_path):
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))
    monkeypatch.setenv("COVERAGE_RUN", "true")
    runtime_coverage.reexec_if_requested()
    assert execs == []


def test_a_directory_it_cannot_write_leaves_the_bot_running(execs, monkeypatch, tmp_path):
    unwritable = tmp_path / "wall"
    unwritable.write_text("not a directory")
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(unwritable / "data"))

    runtime_coverage.reexec_if_requested()

    assert execs == []


def test_a_missing_coverage_install_leaves_the_bot_running(execs, monkeypatch, tmp_path):
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))
    real_import = __import__

    def refuse(name, *args, **kwargs):
        if name == "coverage":
            raise ImportError("no coverage here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", refuse)
    runtime_coverage.reexec_if_requested()

    assert execs == []
    assert list(tmp_path.iterdir()) == []


def test_the_generated_config_is_one_coverage_accepts(tmp_path):
    config = runtime_coverage.write_config(tmp_path, branch=False)

    cov = coverage.Coverage(config_file=str(config))
    assert cov.config.data_file == str(tmp_path / ".coverage")
    assert cov.config.parallel is True
    assert cov.config.branch is False
    # Without this the solver's worker processes go unmeasured, and the modules
    # only they exercise would read as dead code.
    assert "multiprocessing" in cov.config.concurrency


def test_branch_coverage_is_opt_in(execs, monkeypatch, tmp_path):
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))
    monkeypatch.setenv("SHENZHEN_COVERAGE_BRANCH", "1")

    runtime_coverage.reexec_if_requested()

    dataset = runtime_coverage.dataset_dir(tmp_path, branch=True)
    cov = coverage.Coverage(config_file=str(dataset / runtime_coverage.CONFIG_NAME))
    assert cov.config.branch is True
    # The cheap core cannot do branches; asking for it here would only earn a
    # warning and the slow one anyway.
    assert "COVERAGE_CORE" not in runtime_coverage.os.environ


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring needs 3.12")
def test_line_coverage_asks_for_the_cheap_core(execs, monkeypatch, tmp_path):
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))
    runtime_coverage.reexec_if_requested()
    assert runtime_coverage.os.environ["COVERAGE_CORE"] == "sysmon"


def test_a_core_chosen_by_hand_is_left_alone(execs, monkeypatch, tmp_path):
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))
    monkeypatch.setenv("COVERAGE_CORE", "ctrace")
    runtime_coverage.reexec_if_requested()
    assert runtime_coverage.os.environ["COVERAGE_CORE"] == "ctrace"


def test_a_deploy_that_changes_the_source_starts_a_new_dataset(execs, monkeypatch, tmp_path):
    # Coverage records a line as a path and a number, so data taken against
    # source that has since moved does not merely go stale -- it reads as
    # different lines, and the report can end up recommending that live code
    # be deleted and dead code kept.  Different source, different dataset.
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))

    monkeypatch.setattr(runtime_coverage, "source_fingerprint", lambda: "aaaaaaaa")
    runtime_coverage.reexec_if_requested()
    monkeypatch.setattr(runtime_coverage, "source_fingerprint", lambda: "bbbbbbbb")
    runtime_coverage.reexec_if_requested()

    assert datasets(tmp_path) == ["aaaaaaaa-lines", "bbbbbbbb-lines"]
    # ...and the report commands follow the deployed one.
    assert (tmp_path / runtime_coverage.CURRENT_LINK).readlink().name == "bbbbbbbb-lines"


def test_switching_to_branch_coverage_starts_a_new_dataset(execs, monkeypatch, tmp_path):
    # `coverage combine` refuses outright to merge branch data with statement
    # data, so the two cannot share a directory the way restarts do.
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))
    monkeypatch.setattr(runtime_coverage, "source_fingerprint", lambda: "aaaaaaaa")

    runtime_coverage.reexec_if_requested()
    monkeypatch.setenv("SHENZHEN_COVERAGE_BRANCH", "1")
    runtime_coverage.reexec_if_requested()

    assert datasets(tmp_path) == ["aaaaaaaa-branch", "aaaaaaaa-lines"]


def test_a_restart_on_the_same_code_keeps_adding_to_one_dataset(execs, monkeypatch, tmp_path):
    monkeypatch.setenv("SHENZHEN_COVERAGE", str(tmp_path))

    runtime_coverage.reexec_if_requested()
    runtime_coverage.reexec_if_requested()

    assert len(datasets(tmp_path)) == 1


def test_the_fingerprint_covers_the_whole_package(tmp_path, monkeypatch):
    before = runtime_coverage.source_fingerprint()
    assert before == runtime_coverage.source_fingerprint(), "same source, same fingerprint"
    assert len(before) == 8

    # A module the bot barely touches still counts: the line numbers coverage
    # recorded are only meaningful against the exact source they came from.
    scratch = tmp_path / "shenzhen" / "bot"
    scratch.mkdir(parents=True)
    (scratch / "runtime_coverage.py").write_text("x = 1\n")
    (scratch.parent / "notation.py").write_text("y = 2\n")
    monkeypatch.setattr(runtime_coverage, "__file__", str(scratch / "runtime_coverage.py"))
    with_notation = runtime_coverage.source_fingerprint()
    (scratch.parent / "notation.py").write_text("y = 3\n")

    assert runtime_coverage.source_fingerprint() != with_notation


def test_there_is_no_periodic_save_when_not_measuring():
    assert runtime_coverage.start_periodic_save() is None


@pytest.mark.asyncio
async def test_the_periodic_save_writes_and_stops_cleanly(monkeypatch):
    monkeypatch.setenv("COVERAGE_RUN", "true")
    monkeypatch.setattr(runtime_coverage, "SAVE_INTERVAL", 0.01)
    saved = []
    monkeypatch.setattr(runtime_coverage, "save_now", lambda: saved.append(1) or True)

    task = runtime_coverage.start_periodic_save()
    await asyncio.sleep(0.05)
    await runtime_coverage.stop_periodic_save(task)

    assert saved, "nothing was written between start and shutdown"
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_stopping_a_save_that_was_never_started_is_fine():
    await runtime_coverage.stop_periodic_save(None)


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data: dict = {}


class FakeExecutor:
    def __init__(self, log: list) -> None:
        self._log = log

    def shutdown(self, wait, cancel_futures):
        self._log.append(("executor", wait, cancel_futures))


@pytest.mark.asyncio
async def test_an_unmeasured_shutdown_does_not_wait_for_the_solver(monkeypatch):
    events: list = []
    application = FakeApplication()
    await main._startup(application)
    application.bot_data["executor"] = FakeExecutor(events)

    await main._shutdown(application)

    assert application.bot_data["coverage_task"] is None
    # Unchanged from before coverage existed: a stopping bot does not hang
    # around for a search nobody is waiting on any more.
    assert events == [("executor", False, True)]


@pytest.mark.asyncio
async def test_a_measured_shutdown_saves_first_then_waits_for_the_solver(monkeypatch):
    monkeypatch.setenv("COVERAGE_RUN", "true")
    events: list = []
    monkeypatch.setattr(runtime_coverage, "save_now", lambda: events.append(("save",)) or True)

    application = FakeApplication()
    await main._startup(application)
    application.bot_data["executor"] = FakeExecutor(events)

    await main._shutdown(application)

    # Our own data lands before the wait that a stop timeout may cut short,
    # and the workers get the chance to write theirs.
    assert events == [("save",), ("executor", True, True)]


def test_saving_with_no_coverage_running_says_so():
    # Guards the shutdown path: it calls this whether or not anything is
    # measuring, and must not raise when nothing is.
    if coverage.Coverage.current() is None:
        assert runtime_coverage.save_now() is False
