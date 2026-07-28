# Coverage of the running bot

Test coverage says which lines the suite exercises. This says which lines a
week of real conversations exercise, which is a different and more useful
question when the job is deciding whether a branch that handles some unlikely
case is worth keeping.

It is off by default. Turn it on for a stretch of ordinary use, read the
report, delete what nothing reached, turn it off again.

---

## Turning it on

Uncomment the line in `docker-compose.yml`:

```yaml
      SHENZHEN_COVERAGE: /data/coverage
```

and restart:

```console
$ docker compose up -d
$ docker compose logs bot | grep coverage
... INFO shenzhen.bot.coverage: restarting under coverage; data in
    /data/coverage/4f2ab1c9-lines (/data/coverage/current)
```

That log line is the confirmation. Without it, nothing is being measured --
the bot never refuses to start over coverage, so a bad path or a missing
`coverage` install shows up as a warning and an ordinary unmeasured run.

The data lives in a named Docker volume, so it survives restarts and
`docker compose pull`.

## Datasets, and why a deploy starts a new one

`4f2ab1c9` in that path is a fingerprint of the source being measured, and
`current` is a symlink to the dataset the running bot is writing. Restart the
bot as often as you like on the same image and everything accumulates in the
one dataset. Deploy code, and the bot starts a fresh one.

That is not tidiness, it is correctness. Coverage records an executed line as
a path and a line number, with no note of which revision it came from, so data
taken before a deploy describes *the current file's* lines by number after it.
Combine the two and the report does not go vague, it goes wrong: move two
functions past each other, and the one the bot actually runs is reported as
never executed while the dead one reads as covered. A report used to decide
what to delete is the last place that can be allowed to happen.

The same applies to branch data, which `coverage combine` refuses to merge
with statement data at all -- so `SHENZHEN_COVERAGE_BRANCH` gets its own
dataset too (`4f2ab1c9-branch`), and switching modes needs nothing cleaned up
first.

The practical consequence is worth planning around: **a deploy resets the
measuring period.** For a week-long reading, deploy before you start rather
than during. Old datasets stay where they are and can still be reported on
individually -- each is internally consistent, it is only the merging of two
that is not.

## What it costs

The bot answers under a wall-clock budget (`SHENZHEN_TIME_LIMIT`), so anything
that slows the search doesn't just cost CPU, it costs answers: a board that
would have been solved in 19 seconds is reported unsolved instead.

Measured on a deal that takes 26k nodes, Python 3.12:

| | solver time | vs unmeasured |
|---|---|---|
| off | 2.4 s | -- |
| line coverage (`sys.monitoring`) | 2.8 s | 1.2x |
| line coverage (traditional tracer) | 10.9 s | 4.6x |
| branch coverage | 14.3 s | 6.0x |

The default path is the first measured one: `sys.monitoring`, which Python
3.12 and up have and which costs about a fifth of the search. That is worth
paying for a week.

Branch coverage (`SHENZHEN_COVERAGE_BRANCH=1`) tells you not just which lines
ran but which way each `if` went -- the difference between "this line ran" and
"this `else` never once fired". `sys.monitoring` cannot do branches, so
turning it on drops back to the slow tracer and roughly a sixth of the search
budget. Worth it for a deliberate short pass, not for a background week.

## Reading it out

Each process writes its own data file -- the bot, and one per solver worker --
so they have to be merged first:

```console
$ rc=/data/coverage/current/.coveragerc
$ docker compose exec bot python -m coverage combine --keep --rcfile=$rc
$ docker compose exec bot python -m coverage report --rcfile=$rc
$ docker compose exec bot python -m coverage html --rcfile=$rc -d /data/coverage/current/html
$ docker compose cp bot:/data/coverage/current/html ./coverage-html
```

`current` is the dataset for the code that is deployed right now. To report on
an earlier one, name it instead of `current` -- but read it against the source
it was recorded against, not against today's, for the reason above.

`--keep` matters. Without it `combine` deletes the per-process files it read,
and since the bot keeps writing new ones, the *next* combine would report on
the days since rather than on everything. With `--keep` every combine sees the
whole period, and measuring can carry on around it -- there is no need to stop
the bot to look at the report.

The HTML report is the one to read: it shows the actual source with the
unexecuted lines highlighted, which is what you want when the question is
"can this go".

## What is lost when

* **Graceful stop** (`docker compose stop`, `up -d`, `restart`) -- nothing,
  as long as it is allowed to finish. The bot writes its own data immediately
  and then waits for the solver workers to write theirs, and a worker part-way
  through a search can take until `SHENZHEN_TIME_LIMIT` to get there. Docker
  kills the container ten seconds in by default, so while measuring, stop it
  with `docker compose stop -t 30` if you want the workers' last few hours.
* **Crash or OOM kill** -- the bot itself loses at most the last ten minutes
  (it flushes on a timer). The solver workers lose everything since they
  started, because nothing gets to run in a process that is killed outright.
* **`docker compose down -v`** -- all of it. That deletes the volume.

## Reading the report for a cleanup

A line at 0% means nothing reached it during the measured period. That is the
beginning of the argument for deleting it, not the end:

* **Some of it is not the bot.** `vision/calibrate.py` is a command-line tool
  for rebuilding the template bank; it never runs inside the bot and will read
  0% forever. `bot/main.py`'s startup warnings only fire on a broken install.
* **Some of it is error handling for a case that has not happened yet.** A
  week without a corrupt upload is not evidence that uploads are never
  corrupt. Weigh what the line does, not just how often it ran.
* **Some of it depends on who was using it.** A week in which nobody happened
  to type a position by hand says nothing about whether the text parser is
  worth keeping.

Where it earns its keep is none of those three: a fallback under a
fallback, a fourth escalation level in the screenshot reader that the first
three always beat it to, a branch that only exists because an earlier version
of the code could produce input that this version cannot. Those show up as
runs of untouched lines in the middle of code that is otherwise busy, and
those are the ones to pull on.

Cross-check against the suite before deleting: code that real traffic never
reaches but the tests cover heavily is usually one of the first two kinds
above, and code that neither reaches is the safest to remove.

## Turning it off

Comment the line back out and `docker compose up -d`. To throw away everything
that has been collected, including the older datasets:

```console
$ docker compose exec bot find /data/coverage -mindepth 1 -delete
```

## Running it outside Docker

Same switch, and it works from any launcher, because the bot re-executes
itself under `coverage run` at startup rather than expecting to have been
started that way:

```console
$ SHENZHEN_COVERAGE=./coverage-data python -m shenzhen.bot.main
```

Datasets work the same way -- `./coverage-data/current/.coveragerc` is the
rcfile to hand `coverage combine` and `coverage html`. Needs `coverage`
installed (it is in the `dev` extra and in the image).
