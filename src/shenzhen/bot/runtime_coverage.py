"""Coverage of the *running* bot, for finding code that real traffic never
reaches.

The point is not test coverage -- that says which lines the suite exercises.
This says which lines a week of actual conversations exercise, which is what
you want in hand before deleting a branch that looks like it handles a case
nobody ever hits.

Off unless ``SHENZHEN_COVERAGE`` names a directory to keep the data in.  When
it is set, ``main()`` re-executes itself under ``coverage run`` before it has
imported anything worth measuring: starting coverage from inside an already
running process leaves every ``def`` and ``import`` line of the modules that
are already loaded looking unexecuted, which is exactly the noise that would
make the report untrustworthy for the one job it has.

The re-exec also means the solver's worker processes are measured.  Their data
lands in separate files (``parallel = true``) that ``coverage combine`` merges
later, so a restart adds to the picture rather than replacing it.

What may *not* be merged is data recorded against different source.  Coverage
identifies a line by path and number, so a deploy that moves code around while
measuring is on leaves the earlier files describing lines that now belong to
something else -- and it fails quietly, as a report that swaps which functions
look dead.  Data therefore lands in a subdirectory named for a fingerprint of
the source it was recorded against, plus the mode it was recorded in, since
statement and branch data cannot be combined either.  Same code, same mode:
the picture accumulates.  Anything else: a new dataset, and the old one is
still there to report on separately.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("shenzhen.bot.coverage")

#: directory for the data files.  Unset means no measurement at all.
DATA_DIR_ENV = "SHENZHEN_COVERAGE"
#: opt in to branch coverage.  See the note on the solver below.
BRANCH_ENV = "SHENZHEN_COVERAGE_BRANCH"

CONFIG_NAME = ".coveragerc"
#: symlink kept pointing at the dataset the running bot is writing to, so that
#: the commands in docs/coverage.md do not have to name a fingerprint.
CURRENT_LINK = "current"

#: how often the long-running process writes what it has collected so far.
#: Without this a container that is OOM-killed on day six takes the whole
#: week's reading with it; the write is small and off the hot path.
SAVE_INTERVAL = 600.0

_CONFIG_TEMPLATE = """\
# Written by shenzhen.bot.runtime_coverage on startup -- edits are lost on the
# next restart.  Set SHENZHEN_COVERAGE / SHENZHEN_COVERAGE_BRANCH instead.
[run]
data_file = {data_file}
source_pkgs = shenzhen
# One file per process, merged by `coverage combine`, so the solver's workers
# and every restart across the measuring period all land in the same picture.
parallel = true
concurrency = multiprocessing,thread
sigterm = true
branch = {branch}
# Plumbing that runs before and around the measurement; reporting on it would
# only ever be reporting on itself.
omit = */shenzhen/bot/runtime_coverage.py
# Every worker process inherits a loaded `shenzhen` from the parent and says
# so.  Here that is expected rather than a mistake: the parent started under
# coverage and already recorded the import, so the warning is only noise in
# the log, twice per worker, for as long as measurement is on.
disable_warnings = module-not-measured

[report]
skip_empty = true

[html]
title = shenzhen bot -- coverage of the running bot
"""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def data_dir() -> Path | None:
    """Where the caller asked for coverage data, or ``None`` if they did not."""
    raw = os.environ.get(DATA_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def branch_requested() -> bool:
    return _truthy(os.environ.get(BRANCH_ENV))


def already_measuring() -> bool:
    """Are we the process ``coverage run`` started?

    ``coverage run`` exports ``COVERAGE_RUN``; checking it is what keeps the
    re-exec below from looping, and also means running the bot under coverage
    by hand does the sensible thing instead of nesting.
    """
    return _truthy(os.environ.get("COVERAGE_RUN"))


def source_fingerprint() -> str:
    """Eight hex digits standing for the code about to be measured.

    Every ``.py`` under the package, by name and by content, because that is
    the granularity coverage records at: any edit at all can shift the line
    numbers that the data files are written in terms of.
    """
    package = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    try:
        for path in sorted(package.rglob("*.py")):
            digest.update(path.relative_to(package).as_posix().encode())
            digest.update(path.read_bytes())
    except OSError as err:
        # Unreadable source is somebody else's problem -- but two different
        # revisions must not both end up here, so refuse to name a dataset
        # after a fingerprint that means nothing.
        log.warning("cannot fingerprint the source (%s); keeping data apart by time", err)
        return "unknown"
    return digest.hexdigest()[:8]


def dataset_dir(root: Path, *, branch: bool) -> Path:
    """The subdirectory this run's data belongs in.

    Two runs share one only when their source and their mode agree, which is
    exactly when coverage can merge them into a report that means anything.
    """
    return root / f"{source_fingerprint()}-{'branch' if branch else 'lines'}"


def link_current(root: Path, dataset: Path) -> None:
    """Point ``current`` at the dataset being written, best effort."""
    link = root / CURRENT_LINK
    temporary = root / f".{CURRENT_LINK}.new"
    try:
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(dataset.name, target_is_directory=True)
        os.replace(temporary, link)
    except OSError as err:
        log.warning("could not point %s at %s (%s)", link, dataset.name, err)


def write_config(directory: Path, *, branch: bool) -> Path:
    """Write the rcfile that both this process and its children will use.

    A file on disk rather than arguments, because ``concurrency =
    multiprocessing`` has to hand the settings to processes that inherit no
    Python state -- coverage refuses the setting outright without one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / CONFIG_NAME
    config.write_text(
        _CONFIG_TEMPLATE.format(
            data_file=directory / ".coverage",
            branch="true" if branch else "false",
        )
    )
    return config


def reexec_if_requested() -> None:
    """Restart this process under ``coverage run``, if coverage was asked for.

    Returns normally when there is nothing to do -- coverage was not asked
    for, we are already the measured process, or the setup failed.  A bot that
    cannot write its coverage data is still a working bot, so every failure
    here degrades to a warning rather than taking the deployment down with it.
    """
    directory = data_dir()
    if directory is None or already_measuring():
        return

    try:
        import coverage  # noqa: F401
    except ImportError:
        log.warning(
            "%s is set but coverage is not installed -- running unmeasured", DATA_DIR_ENV
        )
        return

    branch = branch_requested()
    dataset = dataset_dir(directory, branch=branch)
    try:
        config = write_config(dataset, branch=branch)
        link_current(directory, dataset)
    except OSError as err:
        log.warning("cannot write coverage data to %s (%s) -- running unmeasured", dataset, err)
        return

    # Picked up by the solver's worker processes, which coverage starts
    # measuring from this file rather than from anything they inherit.
    os.environ["COVERAGE_PROCESS_START"] = str(config)
    if not branch and sys.version_info >= (3, 12):
        # sys.monitoring costs the solver about 20% against roughly 4x for the
        # traditional tracer, and the bot answers under a wall-clock budget:
        # the slower core would cut the search short and change the answers
        # we are measuring.  It cannot do branches, hence only on this path.
        os.environ.setdefault("COVERAGE_CORE", "sysmon")

    log.info("restarting under coverage; data in %s (%s/%s)", dataset, directory, CURRENT_LINK)
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--rcfile={config}",
            "-m",
            "shenzhen.bot.main",
        ],
    )


def save_now() -> bool:
    """Flush what has been collected so far.  True if there was anything to flush."""
    try:
        import coverage
    except ImportError:  # pragma: no cover -- unreachable once measuring
        return False
    current = coverage.Coverage.current()
    if current is None:
        return False
    current.save()
    return True


async def _save_periodically() -> None:
    while True:
        await asyncio.sleep(SAVE_INTERVAL)
        try:
            save_now()
        except Exception:  # pragma: no cover -- never worth losing the bot over
            log.exception("could not write coverage data")


def start_periodic_save() -> asyncio.Task | None:
    """Keep the data on disk roughly current, or ``None`` if not measuring.

    Only covers this process: the solver's workers write theirs when they
    exit, which a graceful shutdown gives them the chance to do.
    """
    if not already_measuring():
        return None
    return asyncio.create_task(_save_periodically())


async def stop_periodic_save(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    save_now()
