"""A CI job run by ``tools/ci/run.py`` starts from an empty environment (#3920).

Each case plants what a job must not see or reach. Where the plant is a state of the caller's (its
home, its variables, its global git file and the helper that file names), a control first runs the
same probe without the isolation, so the plant is shown to work before the job is shown not to
meet it. The other plants are jobs of the test's own, and the case's own assertions show they ran.
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from personalclaw.sdk.testing import neutral_git_env
from tools.ci import run

pytestmark = pytest.mark.security

REPO = Path(__file__).resolve().parents[1]
PATH = os.environ["PATH"]
SECRET = "a-planted-password-phrase-for-the-tripwire"
TOKEN = "a planted caller token phrase"


def _planted_real_home(monkeypatch, home: Path | str) -> None:
    """Make the user database name *home* as this account's home, for the test's duration."""
    real = pwd.getpwuid

    def planted(uid: int):
        fields = list(real(uid))
        fields[5] = str(home)
        return pwd.struct_passwd(fields)

    monkeypatch.setattr(pwd, "getpwuid", planted)


#: The home the user database names in every test here unless the test plants another: a folder
#: under the test's own, never made, so the runner's real-home check (j) never looks at the real
#: one. RUNNER's child plants the same.
_PLANTED_REAL_HOME = "the-real-home"


@pytest.fixture(autouse=True)
def _the_user_database_names_a_planted_home(tmp_path, monkeypatch):
    _planted_real_home(monkeypatch, tmp_path / _PLANTED_REAL_HOME)


#: Prints what a job sees of its home, its temporary folder and its environment.
PROBE = (
    "import json, os, pathlib, tempfile\n"
    "print(json.dumps({'env': dict(os.environ), 'home': str(pathlib.Path.home()),"
    " 'tmp': tempfile.gettempdir()}))\n"
)

#: Runs git with the argv after ``--`` and the stdin the job was given as its first argument.
GIT_WITH_INPUT = (
    "import subprocess, sys\n"
    "sys.exit(subprocess.run(['git', *sys.argv[2:]], input=sys.argv[1], text=True,"
    " capture_output=True).returncode)\n"
)

#: GIT_WITH_INPUT, writing what git answered to the file its first argument names.
GIT_ANSWER_TO_FILE = (
    "import subprocess, sys\n"
    "done = subprocess.run(['git', *sys.argv[3:]], input=sys.argv[2], text=True,"
    " capture_output=True)\n"
    "open(sys.argv[1], 'w').write(done.stdout)\n"
    "sys.exit(done.returncode)\n"
)

FILL = "protocol=https\nhost=example.invalid\n\n"


def _probe_job(out: Path) -> list[str]:
    """A job that writes what PROBE prints to *out*."""
    return [sys.executable, "-c", PROBE.replace("print(", f"open({str(out)!r}, 'w').write(")]


def _git_job(stdin: str, *git_args: str) -> list[str]:
    return [sys.executable, "-c", GIT_WITH_INPUT, stdin, *git_args]


def _scratch(tmp_path: Path, name: str = "scratch") -> run.Scratch:
    return run.Scratch.create(tmp_path / name)


def _run(
    tmp_path: Path, argv: list[str], *, scratch: run.Scratch | None = None, **kw
) -> run.Result:
    scratch = scratch or _scratch(tmp_path)
    kw.setdefault("timeout", 60.0)
    return run.run_job(argv, cwd=tmp_path, scratch=scratch, path=PATH, **kw)


def _fixture_helper(tmp_path: Path) -> tuple[str, Path]:
    """A credential helper of the machine's own, as a test plants one: it answers with a
    credential and leaves a marker. Returns git's helper value for it and the marker."""
    marker = tmp_path / "fixture-helper-ran"
    script = tmp_path / "pc-fixture-machine-helper.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text(sys.argv[-1])\n"
        "print('username=fixture-user')\nprint('password=fixture-password')\n"
    )
    return f"!{shlex.quote(sys.executable)} {shlex.quote(str(script))}", marker


def _caller_env(**extra: str) -> dict[str, str]:
    """The caller's environment with nothing on git's command line clearing the helpers a file
    names: what a job would inherit if the runner passed the caller's environment through. It
    keeps ``GIT_CONFIG_NOSYSTEM``, so a control's git never reaches the machine's own helper."""
    cleared = ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "GIT_CONFIG_PARAMETERS")
    env = {k: v for k, v in os.environ.items() if not k.startswith(cleared)}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", **extra)
    return env


def _gone(pgid: int) -> bool:
    """Whether no process is left running in group *pgid*. A killed job's child is reaped by
    the system, not by the runner, so this polls for that briefly rather than taking one look.
    On macOS a group whose processes have all exited, but are not reaped yet, answers EPERM."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.05)
    return False


def _kill_group(pgid: int) -> None:
    """Kill what is left of a job's group the test started, if the runner left any of it."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


# (a) the home and the temporary folder are the scratch ones, and the PersonalClaw home is the
# default one under that home


def test_a_job_sees_the_scratch_home_and_temp_folder_not_the_callers(tmp_path, monkeypatch):
    planted = tmp_path / "home"
    (planted / ".personalclaw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(planted))

    control = json.loads(
        subprocess.run(
            [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True
        ).stdout
    )
    assert control["home"] == str(planted)

    scratch = _scratch(tmp_path)
    seen_path = tmp_path / "seen.json"
    result = _run(tmp_path, _probe_job(seen_path), scratch=scratch)
    assert result.code == run.OK, result
    seen = json.loads(seen_path.read_text())
    real = os.path.realpath
    assert real(seen["env"]["HOME"]) == real(scratch.home)
    assert real(seen["home"]) == real(scratch.home)
    assert "PERSONALCLAW_HOME" not in seen["env"], "the default home, under HOME, is the job's"
    assert real(seen["env"]["TMPDIR"].rstrip("/")) == real(scratch.tmp)
    assert real(seen["tmp"]) == real(scratch.tmp)
    assert (seen["env"]["LANG"], seen["env"]["LC_ALL"]) == ("C.UTF-8", "C.UTF-8")
    assert str(planted) not in json.dumps(seen)


def test_a_jobs_personalclaw_home_is_the_default_one_inside_its_scratch_folder(
    tmp_path, monkeypatch
):
    planted = tmp_path / "callers-home"
    monkeypatch.setenv("HOME", str(planted))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(planted / ".personalclaw"))
    scratch = _scratch(tmp_path)
    out = tmp_path / "default-home.txt"
    printer = f"import os; open({str(out)!r}, 'w').write(os.path.expanduser('~/.personalclaw'))"
    assert _run(tmp_path, [sys.executable, "-c", printer], scratch=scratch).code == run.OK
    real = os.path.realpath
    seen = real(out.read_text())
    assert seen == real(scratch.home / ".personalclaw")
    assert seen.startswith(real(scratch.root) + os.sep)
    assert str(planted) not in seen


# (b) the caller's variables are not passed through


def test_a_job_gets_none_of_the_callers_variables(tmp_path, monkeypatch):
    planted = {
        "PC_PLANTED_TOKEN": TOKEN,
        "GIT_DIR": str(tmp_path / "planted.git"),
        "GIT_INDEX_FILE": str(tmp_path / "planted-index"),
        "SSH_AUTH_SOCK": str(tmp_path / "planted-agent.sock"),
    }
    for key, value in planted.items():
        monkeypatch.setenv(key, value)
    control = json.loads(
        subprocess.run(
            [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True
        ).stdout
    )
    assert planted.items() <= control["env"].items()

    seen_path = tmp_path / "seen.json"
    assert _run(tmp_path, _probe_job(seen_path)).code == run.OK
    seen = json.loads(seen_path.read_text())["env"]
    assert not set(planted) & set(seen)
    assert TOKEN not in json.dumps(seen)


# (c) git reads no configuration file of the caller's


def test_a_jobs_git_reads_only_its_command_line_configuration(tmp_path, monkeypatch):
    planted = tmp_path / "caller.gitconfig"
    planted.write_text("[credential]\n\thelper = pc-fixture-machine-helper\n")
    alone = tmp_path / "not-a-repository"
    alone.mkdir()
    listing = tmp_path / "listing.txt"
    lister = (
        "import subprocess, sys\n"
        "out = subprocess.run(['git', 'config', '--show-origin', '--list'], capture_output=True,"
        " text=True)\n"
        f"open({str(listing)!r}, 'w').write(out.stdout)\n"
    )
    ceiling = str(tmp_path)

    subprocess.run(
        [sys.executable, "-c", lister],
        cwd=alone,
        env=_caller_env(GIT_CONFIG_GLOBAL=str(planted), GIT_CEILING_DIRECTORIES=ceiling),
        check=True,
    )
    assert "pc-fixture-machine-helper" in listing.read_text()

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(planted))
    result = run.run_job(
        [sys.executable, "-c", lister],
        cwd=alone,
        scratch=_scratch(tmp_path),
        path=PATH,
        timeout=60.0,
        env={"GIT_CEILING_DIRECTORIES": ceiling},
    )
    assert result.code == run.OK, result
    lines = [line for line in listing.read_text().splitlines() if line]
    assert lines, "git listed nothing: the probe did not run"
    assert all(line.startswith("command line:") for line in lines), lines
    assert "pc-fixture-machine-helper" not in listing.read_text()


# (d) a credential git asks for fails the run naming the tripwire, which answers nothing


def test_a_helper_in_the_callers_global_file_never_runs(tmp_path, monkeypatch):
    helper, marker = _fixture_helper(tmp_path)
    planted = tmp_path / "caller.gitconfig"
    planted.write_text(f"[credential]\n\thelper = {helper}\n")

    control = subprocess.run(
        _git_job(FILL, "credential", "fill"), env=_caller_env(GIT_CONFIG_GLOBAL=str(planted))
    )
    assert control.returncode == 0 and marker.exists()
    marker.unlink()

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(planted))
    result = _run(tmp_path, _git_job(FILL, "credential", "fill"))
    assert not marker.exists()
    assert result.code == run.RED
    assert any(
        "git asked for a credential (get, https, example.invalid)" in v for v in result.violations
    ), result


def test_the_tripwire_gives_git_no_credential(tmp_path):
    answer = tmp_path / "git-answer.txt"
    job = [sys.executable, "-c", GIT_ANSWER_TO_FILE, str(answer), FILL, "credential", "fill"]
    result = _run(tmp_path, job)
    assert answer.exists(), "the job did not run git"
    assert "password=" not in answer.read_text() and "username=" not in answer.read_text()
    assert result.returncode != 0, "git got a credential, so it did not fail"
    assert result.code == run.RED
    assert any("tripwire" in v and "(get, https, example.invalid)" in v for v in result.violations)


def test_a_git_that_asks_for_a_credential_fails_the_run_naming_the_tripwire(tmp_path):
    helper, _ = _fixture_helper(tmp_path)
    result = _run(
        tmp_path, _git_job(FILL, "-c", f"credential.helper={helper}", "credential", "fill")
    )
    assert result.returncode == 0, "the job's own helper answered, so only the tripwire fails it"
    assert result.code == run.RED
    assert any(
        "tripwire" in v and "(get, https, example.invalid)" in v for v in result.violations
    ), result


def test_the_tripwire_never_records_a_password_it_is_handed(tmp_path):
    scratch = _scratch(tmp_path)
    approve = f"protocol=https\nhost=example.invalid\nusername=u\npassword={SECRET}\n\n"
    result = _run(tmp_path, _git_job(approve, "credential", "approve"), scratch=scratch)
    assert result.code == run.RED
    assert any("(store, https, example.invalid)" in v for v in result.violations), result
    assert scratch.tripwire_log.exists()
    assert (scratch.tripwire_log.stat().st_mode & 0o777) == 0o600
    assert SECRET not in scratch.tripwire_log.read_text()
    assert SECRET not in repr(result)


def test_the_tripwire_records_at_most_200_characters_of_a_field(tmp_path):
    scratch = _scratch(tmp_path)
    host = "h" * 500 + ".invalid"
    result = _run(
        tmp_path,
        _git_job(f"protocol=https\nhost={host}\n\n", "credential", "fill"),
        scratch=scratch,
    )
    assert result.code == run.RED
    records = [json.loads(line) for line in scratch.tripwire_log.read_text().splitlines()]
    assert records and all(record["host"] == host[:200] for record in records), records


def test_a_declared_python_variable_does_not_reach_the_tripwire(tmp_path):
    # The job's own interpreter runs with -I, so only the tripwire's would meet PYTHONHOME.
    job = [sys.executable, "-I", "-c", GIT_WITH_INPUT, FILL, "credential", "fill"]
    result = _run(tmp_path, job, env={"PYTHONHOME": str(tmp_path / "no-python-home-here")})
    assert result.code == run.RED
    assert any(
        "tripwire" in v and "(get, https, example.invalid)" in v for v in result.violations
    ), result


def test_a_tripwire_record_that_is_not_a_json_object_still_fails_the_run(tmp_path):
    scratch = _scratch(tmp_path)
    writer = f"open({str(scratch.tripwire_log)!r}, 'a').write('not json\\n[1]\\n')"
    result = _run(tmp_path, [sys.executable, "-c", writer], scratch=scratch)
    assert result.code == run.RED
    assert result.violations == ("the tripwire: git asked for a credential (?, ?, ?)",) * 2


def test_a_job_whose_git_never_asks_for_a_credential_passes(tmp_path):
    before = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    result = _run(tmp_path, ["git", "--version"])
    assert result == run.Result(code=run.OK, returncode=0, violations=())
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before


def test_the_tripwire_runs_from_a_scratch_folder_whose_path_has_a_space(tmp_path):
    scratch = _scratch(tmp_path, "a scratch folder")
    result = _run(tmp_path, _git_job(FILL, "credential", "fill"), scratch=scratch)
    assert result.code == run.RED
    assert any("(get, https, example.invalid)" in v for v in result.violations), result


def test_a_job_that_exits_non_zero_is_red(tmp_path):
    result = _run(tmp_path, [sys.executable, "-c", "raise SystemExit(3)"])
    assert result.code == run.RED and result.returncode == 3
    assert result.violations == ("the job exited 3",)


# (d2) a job's output, kept when asked: for the lint remediation (#3915)

_SPEAKS = "import sys; print('to stdout', flush=True); print('to stderr', file=sys.stderr)"


def test_a_jobs_kept_output_holds_its_stdout_and_stderr_and_still_reaches_the_log(tmp_path, capfd):
    kept = tmp_path / "kept.out"
    result = _run(tmp_path, [sys.executable, "-c", _SPEAKS + "; raise SystemExit(1)"], output=kept)
    assert result.violations == ("the job exited 1",)
    assert kept.read_bytes().splitlines() == [b"to stdout", b"to stderr"]
    log = capfd.readouterr().out
    assert "to stdout" in log and "to stderr" in log, "the log reads as it would"


def test_a_kept_output_past_the_limit_is_cut_and_the_log_still_has_it_whole(
    tmp_path, capfd, monkeypatch
):
    monkeypatch.setattr(run, "OUTPUT_LIMIT", 10)
    kept = tmp_path / "kept.out"
    _run(tmp_path, [sys.executable, "-c", "print('x' * 100)"], output=kept)
    assert kept.read_bytes() == b"x" * 10 + run.OUTPUT_CUT
    assert "x" * 100 in capfd.readouterr().out


def test_without_an_output_file_the_job_writes_to_the_runners_own_output(tmp_path, capfd):
    _run(tmp_path, [sys.executable, "-c", _SPEAKS])
    captured = capfd.readouterr()
    assert "to stdout" in captured.out and "to stderr" in captured.err, "nothing is piped"


# (e) a declared env can't undo the scrub


@pytest.mark.parametrize(
    "key",
    [
        "HOME",
        "PERSONALCLAW_HOME",
        "TMPDIR",
        "PATH",
        "LANG",
        "LC_ALL",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
        "GIT_CONFIG_KEY_2",
        "GIT_CONFIG_VALUE_2",
        "GIT_CONFIG_PARAMETERS",
    ],
)
def test_a_declared_env_that_would_undo_the_scrub_is_refused(tmp_path, key):
    with pytest.raises(ValueError, match=key):
        run.job_env(_scratch(tmp_path), path=PATH, env={key: str(tmp_path)})


def test_a_declared_env_of_the_jobs_own_is_kept(tmp_path):
    env = run.job_env(_scratch(tmp_path), path=PATH, env={"PC_JOB_SETTING": "kept"})
    assert env["PC_JOB_SETTING"] == "kept"


def test_every_variable_the_runner_sets_or_refuses_is_one_it_says_it_owns(tmp_path):
    # The catalogue's loader refuses what run.runner_owns names, so a job env it loads is one
    # job_env accepts.
    assert all(run.runner_owns(name) for name in run.job_env(_scratch(tmp_path), path=PATH))
    for name in ("PERSONALCLAW_HOME", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_KEY_9"):
        assert run.runner_owns(name), name
        with pytest.raises(ValueError, match="the runner owns it"):
            run.job_env(_scratch(tmp_path, name), path=PATH, env={name: "x"})
    assert not run.runner_owns("PC_JOB_SETTING") and not run.runner_owns("CI")


# (f) an interrupted run is partial, and its whole process group is gone

#: A job that starts a child of its own, says both pids, and waits to be stopped.
BLOCKED = (
    "import os, signal, subprocess, sys\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'])\n"
    "print('ready', os.getpid(), child.pid, flush=True)\n"
    "signal.pause()\n"
)

#: BLOCKED, with the job and its child both ignoring SIGTERM.
DEAF = BLOCKED.replace(
    "import os, signal, subprocess, sys\n",
    "import os, signal, subprocess, sys\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
)

#: A job whose git asks for a credential before it says its pid and waits to be stopped.
ASKS_THEN_BLOCKED = (
    "import os, signal, subprocess\n"
    f"subprocess.run(['git', 'credential', 'fill'], input={FILL!r}, text=True,"
    " capture_output=True)\n"
    "print('ready', os.getpid(), flush=True)\n"
    "signal.pause()\n"
)

#: A job whose own process dies at once on SIGTERM, while its child, on SIGTERM, takes 0.3 s to
#: write the marker the job's first argument names before it exits.
CLEANING_CHILD = """\
import os, signal, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", '''
import signal, sys, time
def clean_up(*_):
    time.sleep(0.3)
    open(sys.argv[1], "w").write("cleaned up")
    sys.exit(0)
signal.signal(signal.SIGTERM, clean_up)
print("armed", flush=True)
signal.pause()
''', sys.argv[1]], stdout=subprocess.PIPE, text=True)
child.stdout.readline()
print("ready", os.getpid(), child.pid, flush=True)
signal.pause()
"""

#: The runner in a child interpreter, so the test can signal it the way a terminal would. Its
#: arguments: the repository, the run's folder, the job's argv and run_job's keywords as JSON,
#: then, optionally, when the runner signals itself and with which signal: ``started`` as Popen
#: returns the job, ``ended`` as the runner kills what is left of the group of a job that ended.
#: Its user database names ``the-real-home`` under the run's folder (:data:`_PLANTED_REAL_HOME`).
RUNNER = """\
import json, os, pwd, signal, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from tools.ci import run

base, argv, kw = Path(sys.argv[2]), json.loads(sys.argv[3]), json.loads(sys.argv[4])
_entry = pwd.getpwuid
pwd.getpwuid = lambda uid: pwd.struct_passwd(
    [*_entry(uid)[:5], str(base / "the-real-home"), *_entry(uid)[6:]]
)
when, signum = (sys.argv[5], int(sys.argv[6])) if len(sys.argv) > 6 else (None, 0)
if when == "started":
    class Started(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            print("started", self.pid, flush=True)
            os.kill(os.getpid(), signum)
    subprocess.Popen = Started
elif when == "ended":
    killpg, fired = os.killpg, []
    def killpg_then_signal(pgid, sig):
        try:
            killpg(pgid, sig)
        finally:
            if sig == signal.SIGKILL and not fired:
                fired.append(sig)
                os.kill(os.getpid(), signum)
    os.killpg = killpg_then_signal
began = time.monotonic()
r = run.run_job(argv, cwd=base, scratch=run.Scratch.create(base / "scratch"),
                path=os.environ["PATH"], **kw)
print("result", r.code, f"{time.monotonic() - began:.2f}", *r.violations, sep="|", flush=True)
sys.exit(r.code)
"""


@contextlib.contextmanager
def _runner(
    tmp_path: Path,
    job: list[str],
    kw: dict[str, float],
    *,
    when: str | None = None,
    signum: int = 0,
    stdin: int | None = None,
) -> Iterator[tuple[subprocess.Popen[str], Path, list[int]]]:
    """Start RUNNER on *job*; yield it, the file its stdout and the job's go to (a file, not a
    pipe, so a process left behind can't hang the test on it), and a list the test adds the
    job's group to. Afterwards whatever the runner left of the group, and the runner, are
    killed."""
    out = tmp_path / "runner.out"
    extra = [when, str(int(signum))] if when else []
    with open(out, "w") as sink:
        runner = subprocess.Popen(
            [sys.executable, "-c", RUNNER, str(REPO), str(tmp_path), json.dumps(job)]
            + [json.dumps(kw), *extra],
            stdout=sink,
            stdin=stdin,
            text=True,
        )
    groups: list[int] = []
    try:
        yield runner, out, groups
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
        for pgid in groups:
            _kill_group(pgid)


def _line(out: Path, first: str, seconds: float = 30.0) -> list[str]:
    """The first line in *out* whose first word is *first*, split into words."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for line in out.read_text().splitlines():
            if line.split(" ", 1)[0] == first:
                return line.split()
        time.sleep(0.05)
    raise AssertionError(f"no {first!r} line in {out.read_text()!r}")


def _result(out: Path) -> tuple[int, float, list[str]]:
    """The runner's result line: its code, how long run_job took, and the violations."""
    for line in out.read_text().splitlines():
        if line.startswith("result|"):
            _, code, took, *violations = line.split("|")
            return int(code), float(took), violations
    raise AssertionError(f"the runner wrote no result: {out.read_text()!r}")


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_an_interrupted_run_is_partial_and_leaves_no_process_of_its_group(tmp_path, signum):
    kw = {"timeout": 120.0, "grace": 0.5}
    with _runner(tmp_path, [sys.executable, "-c", BLOCKED], kw) as (runner, out, groups):
        ready = _line(out, "ready")
        job, child = int(ready[1]), int(ready[2])
        groups.append(job)
        assert os.getpgid(child) == job, "the job's child is in the job's group"
        runner.send_signal(signum)
        assert runner.wait(timeout=60) == run.PARTIAL
        assert _result(out)[2] == [f"interrupted by {signal.Signals(signum).name}"]
        assert _gone(job), "a process of the job's group (the job or its child) survived"


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_a_signal_while_popen_starts_the_job_still_stops_its_group(tmp_path, signum):
    kw = {"timeout": 120.0, "grace": 0.5}
    job_argv = [sys.executable, "-c", BLOCKED]
    with _runner(tmp_path, job_argv, kw, when="started", signum=signum) as (runner, out, groups):
        job = int(_line(out, "started")[1])
        groups.append(job)
        assert runner.wait(timeout=60) == run.PARTIAL
        assert _result(out)[2] == [f"interrupted by {signal.Signals(signum).name}"]
        assert _gone(job), "a process of the job's group survived a signal during Popen"


def test_a_signal_after_the_job_has_ended_makes_the_run_partial(tmp_path):
    kw = {"timeout": 60.0}
    job = [sys.executable, "-c", "pass"]
    with _runner(tmp_path, job, kw, when="ended", signum=signal.SIGINT) as (runner, out, _):
        assert runner.wait(timeout=60) == run.PARTIAL
        assert _result(out)[2] == ["interrupted by SIGINT"]


def test_an_interrupted_run_whose_git_asked_for_a_credential_is_red(tmp_path):
    kw = {"timeout": 120.0, "grace": 0.5}
    with _runner(tmp_path, [sys.executable, "-c", ASKS_THEN_BLOCKED], kw) as (runner, out, groups):
        job = int(_line(out, "ready")[1])
        groups.append(job)
        runner.send_signal(signal.SIGINT)
        assert runner.wait(timeout=60) == run.RED, "an isolation breach outranks an interrupt"
        violations = _result(out)[2]
        assert "interrupted by SIGINT" in violations
        assert any("tripwire" in v and "(get, https, example.invalid)" in v for v in violations)
        assert _gone(job)


def test_an_interrupted_job_that_ignores_sigterm_is_killed_after_the_grace(tmp_path):
    kw = {"timeout": 120.0, "grace": 0.5}
    with _runner(tmp_path, [sys.executable, "-c", DEAF], kw) as (runner, out, groups):
        job = int(_line(out, "ready")[1])
        groups.append(job)
        runner.send_signal(signal.SIGINT)
        assert runner.wait(timeout=30) == run.PARTIAL
        assert _result(out)[2] == ["interrupted by SIGINT"]
        assert _gone(job), "a process of the job's group outlived its SIGKILL"


def test_a_second_signal_while_the_group_is_stopped_kills_it_at_once(tmp_path):
    kw = {"timeout": 120.0, "grace": 60.0}
    with _runner(tmp_path, [sys.executable, "-c", DEAF], kw) as (runner, out, groups):
        job = int(_line(out, "ready")[1])
        groups.append(job)
        runner.send_signal(signal.SIGINT)
        time.sleep(0.3)
        runner.send_signal(signal.SIGTERM)
        assert runner.wait(timeout=30) == run.PARTIAL, "the run waited out the 60 s grace"
        assert any(v.startswith("interrupted by ") for v in _result(out)[2])
        assert _gone(job)


def test_a_stopped_group_gets_the_grace_not_only_the_jobs_own_process(tmp_path):
    assert run.GRACE == 5.0, "docs/maintainers/ci.md says a group gets 5 seconds after SIGTERM"
    marker = tmp_path / "cleaned-up"
    job_argv = [sys.executable, "-c", CLEANING_CHILD, str(marker)]
    with _runner(tmp_path, job_argv, {"timeout": 120.0}) as (runner, out, groups):
        job = int(_line(out, "ready")[1])
        groups.append(job)
        runner.send_signal(signal.SIGINT)
        assert runner.wait(timeout=30) == run.PARTIAL
        assert marker.exists(), "the job's child got SIGKILL before it could clean up"
        assert _gone(job)


def test_a_job_reads_nothing_of_the_runners_stdin(tmp_path):
    seen = tmp_path / "stdin.txt"
    reader = [sys.executable, "-c", f"import sys; open({str(seen)!r}, 'w').write(sys.stdin.read())"]
    kw = {"timeout": 60.0}
    with _runner(tmp_path, reader, kw, stdin=subprocess.PIPE) as (runner, _out, _):
        assert runner.stdin is not None
        runner.stdin.write("the caller's stdin\n")
        runner.stdin.close()
        assert runner.wait(timeout=60) == run.OK
    assert seen.read_text() == ""


def test_run_job_refuses_to_start_a_job_off_the_main_thread(tmp_path):
    marker = tmp_path / "started"
    job = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('started')"]
    raised: list[BaseException] = []

    def off_the_main_thread() -> None:
        try:
            _run(tmp_path, job)
        except BaseException as exc:  # noqa: BLE001 - the test reports whatever it raised
            raised.append(exc)

    worker = threading.Thread(target=off_the_main_thread)
    worker.start()
    worker.join(timeout=60)
    assert [type(exc) for exc in raised] == [RuntimeError], raised
    assert "main thread" in str(raised[0])
    assert not marker.exists(), "the job ran with no handler to stop its group"


# (h) a job past its time is red, and its group is gone


#: BLOCKED, with its pids written to the file its first argument names.
BLOCKED_WRITING_PIDS = BLOCKED.replace(
    "print('ready', os.getpid(), child.pid, flush=True)",
    "open(sys.argv[1], 'w').write(f'{os.getpid()} {child.pid}')",
)


def test_a_job_past_its_time_is_red_and_its_group_is_killed(tmp_path):
    pids = tmp_path / "pids"
    job = [sys.executable, "-c", BLOCKED_WRITING_PIDS, str(pids)]
    result = _run(tmp_path, job, timeout=5.0, grace=0.5)
    assert result.code == run.RED
    assert result.violations == ("the job timed out after 5s",)
    job_pid = int(pids.read_text().split()[0])
    try:
        assert _gone(job_pid), "a process of the job's group (the job or its child) survived"
    finally:
        _kill_group(job_pid)


def test_before_stop_is_given_the_group_of_a_job_past_its_time_while_it_still_runs(tmp_path):
    pids = tmp_path / "pids"
    job = [sys.executable, "-c", BLOCKED_WRITING_PIDS, str(pids)]
    seen: list[tuple[int, bool]] = []

    def before_stop(pgid: int) -> None:
        seen.append((pgid, not _gone_now(pgid)))

    result = _run(tmp_path, job, timeout=3.0, grace=0.5, before_stop=before_stop)
    job_pid = int(pids.read_text().split()[0])
    try:
        assert seen == [(job_pid, True)], "before_stop never saw the live group"
        assert result.code == run.RED
        assert result.violations == ("the job timed out after 3s",)
        assert _gone(job_pid), "a process of the job's group outlived the stop"
    finally:
        _kill_group(job_pid)


def test_a_before_stop_that_raises_still_leaves_no_process_of_the_group(tmp_path):
    pids = tmp_path / "pids"
    job = [sys.executable, "-c", BLOCKED_WRITING_PIDS, str(pids)]

    def before_stop(pgid: int) -> None:
        raise OSError("the lister broke")

    with pytest.raises(OSError, match="the lister broke"):
        _run(tmp_path, job, timeout=3.0, grace=0.5, before_stop=before_stop)
    job_pid = int(pids.read_text().split()[0])
    try:
        assert _gone(job_pid), "a raising before_stop left the group running"
    finally:
        _kill_group(job_pid)


#: A job that writes its pid to the file its first argument names and, on SIGTERM, writes
#: "terminated" to the file its second argument names before it exits.
MARKS_SIGTERM = (
    "import os, signal, sys\n"
    "def marked(*_):\n"
    "    open(sys.argv[2], 'w').write('terminated')\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGTERM, marked)\n"
    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
    "signal.pause()\n"
)


def test_a_before_stop_that_raises_is_followed_by_the_stop_sigterm_first(tmp_path):
    pids, marker = tmp_path / "pids", tmp_path / "terminated"
    job = [sys.executable, "-c", MARKS_SIGTERM, str(pids), str(marker)]

    def before_stop(pgid: int) -> None:
        raise OSError("the lister broke")

    with pytest.raises(OSError, match="the lister broke"):
        _run(tmp_path, job, timeout=3.0, grace=5.0, before_stop=before_stop)
    job_pid = int(pids.read_text())
    try:
        assert marker.read_text() == "terminated", "the group got SIGKILL with no SIGTERM first"
    finally:
        _kill_group(job_pid)


def test_before_stop_is_not_called_for_a_job_that_ends_in_time(tmp_path):
    seen: list[int] = []
    result = _run(tmp_path, [sys.executable, "-c", "pass"], before_stop=seen.append)
    assert result.code == run.OK
    assert seen == []


def _gone_now(pgid: int) -> bool:
    """One look: whether group *pgid* holds no process the test can signal."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    return False


def test_a_job_that_ignores_sigterm_past_its_time_is_killed_after_the_grace(tmp_path):
    kw = {"timeout": 3.0, "grace": 0.5}
    with _runner(tmp_path, [sys.executable, "-c", DEAF], kw) as (runner, out, groups):
        job = int(_line(out, "ready")[1])
        groups.append(job)
        assert runner.wait(timeout=30) == run.RED, "the runner never killed the job"
        _, took, violations = _result(out)
        assert violations == ["the job timed out after 3s"]
        assert took < 3.0 + 0.5 + 5.0, took
        assert _gone(job), "a process of the job's group outlived its SIGKILL"


def test_a_job_that_ends_on_its_own_leaves_no_process_of_its_group(tmp_path):
    pids = tmp_path / "pids"
    leaves_a_child = (
        "import os, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(sys.argv[1], 'w').write(f'{os.getpid()} {child.pid}')\n"
    )
    result = _run(tmp_path, [sys.executable, "-c", leaves_a_child, str(pids)])
    job = int(pids.read_text().split()[0])
    try:
        assert result == run.Result(code=run.OK, returncode=0, violations=())
        assert _gone(job), "the child the job left running survived the run"
    finally:
        _kill_group(job)


@pytest.mark.parametrize(
    "timing",
    [
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"timeout": None},
        {"timeout": 0},
        {"timeout": -1.0},
        {"grace": float("nan")},
        {"grace": float("inf")},
        {"grace": -1.0},
    ],
    ids=lambda timing: ",".join(f"{k}={v}" for k, v in timing.items()),
)
def test_a_timeout_or_grace_that_is_not_a_finite_number_is_refused(tmp_path, timing):
    marker = tmp_path / "started"
    job = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('started')"]
    with pytest.raises(ValueError, match=next(iter(timing))):
        _run(tmp_path, job, **timing)
    assert not marker.exists()


# (g) the git variables are the suite's neutral ones, plus the tripwire


def test_the_jobs_git_variables_are_the_suites_neutral_ones_plus_the_tripwire(tmp_path):
    scratch = _scratch(tmp_path)
    env = run.job_env(scratch, path=PATH)
    git = {k: v for k, v in env.items() if k.startswith("GIT_CONFIG")}
    neutral = neutral_git_env(scratch.gitconfig)
    tripwire = {"GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1"}
    assert {k: v for k, v in git.items() if k not in tripwire} == {
        k: v for k, v in neutral.items() if k != "GIT_CONFIG_COUNT"
    }
    assert git["GIT_CONFIG_COUNT"] == "2"
    assert git["GIT_CONFIG_KEY_1"] == "credential.helper"
    assert str(scratch.tripwire) in git["GIT_CONFIG_VALUE_1"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert not os.path.exists(scratch.gitconfig), "git makes the global file on its first write"


# (i) each job gets a fresh scratch folder that only its owner can open


def test_a_scratch_folder_is_new_and_private(tmp_path):
    scratch = _scratch(tmp_path)
    assert (scratch.root.stat().st_mode & 0o777) == 0o700
    assert not hasattr(scratch, "personalclaw_home"), "the PersonalClaw home is the default one"
    for folder in (scratch.home, scratch.tmp):
        assert folder.is_dir()
        assert (folder.stat().st_mode & 0o777) == 0o700, folder
    with pytest.raises(FileExistsError):
        run.Scratch.create(scratch.root)


@pytest.mark.parametrize("left_behind", [None, "gitconfig"])
def test_a_folder_an_earlier_job_left_is_refused(tmp_path, left_behind):
    earlier = tmp_path / "earlier"
    earlier.mkdir()
    if left_behind:
        (earlier / left_behind).write_text("[user]\n\tname = an-earlier-job\n")
    with pytest.raises(FileExistsError):
        run.Scratch.create(earlier)


# (j) the real home is the user database's, and a job that makes its .personalclaw fails the run


def _made_by_path(target: Path, how: str) -> list[str]:
    """A job that makes *target* by its absolute path, as *how* says."""
    make = {
        "a folder": f"os.makedirs({str(target)!r})",
        "a file": f"open({str(target)!r}, 'w').close()",
        "a dangling link": f"os.symlink({str(target.parent / 'nowhere')!r}, {str(target)!r})",
    }[how]
    return [sys.executable, "-c", f"import os; {make}"]


def test_the_real_home_is_the_user_databases_not_the_HOME_variable(tmp_path, monkeypatch):
    planted = tmp_path / "real-home"
    _planted_real_home(monkeypatch, planted)
    monkeypatch.setenv("HOME", str(tmp_path / "another-home"))
    assert run.real_home() == planted


@pytest.mark.parametrize("how", ["a folder", "a file", "a dangling link"])
def test_a_job_that_makes_the_real_homes_personalclaw_fails_the_run(tmp_path, monkeypatch, how):
    planted = tmp_path / "real-home"
    planted.mkdir()
    (planted / "keep.txt").write_text("the owner's own file")
    _planted_real_home(monkeypatch, planted)
    # The runner's own HOME is another folder: a runner that took it from there watches that one.
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    made = planted / ".personalclaw"

    result = _run(tmp_path, _made_by_path(made, how))

    assert result.code == run.RED, result
    assert result.violations == (
        f"the job made the real home's {made}, which was absent as it started",
    )
    # The runner only looks: what the job made stays as it made it, and nothing else changed.
    assert sorted(p.name for p in planted.iterdir()) == [".personalclaw", "keep.txt"]
    assert (planted / "keep.txt").read_text() == "the owner's own file"
    assert made.is_symlink() == (how == "a dangling link")
    if how == "a folder":
        assert list(made.iterdir()) == []


def test_a_job_that_makes_a_personalclaw_in_its_own_home_passes(tmp_path, monkeypatch):
    planted = tmp_path / "real-home"
    planted.mkdir()
    _planted_real_home(monkeypatch, planted)
    scratch = _scratch(tmp_path)
    job = [sys.executable, "-c", "import os; os.makedirs(os.path.expanduser('~/.personalclaw'))"]

    result = _run(tmp_path, job, scratch=scratch)

    assert result == run.Result(code=run.OK, returncode=0, violations=())
    assert (scratch.home / ".personalclaw").is_dir()
    assert list(planted.iterdir()) == []


@pytest.mark.parametrize("what", ["a folder", "a dangling link"])
def test_a_real_homes_personalclaw_that_was_there_before_the_job_passes(
    tmp_path, monkeypatch, what
):
    planted = tmp_path / "real-home"
    planted.mkdir()
    if what == "a folder":
        (planted / ".personalclaw").mkdir()
    else:
        (planted / ".personalclaw").symlink_to(planted / "nowhere")
    _planted_real_home(monkeypatch, planted)
    result = _run(tmp_path, [sys.executable, "-c", "pass"])
    assert result == run.Result(code=run.OK, returncode=0, violations=())


@pytest.mark.parametrize(
    ("ending", "first"),
    [("exits 1", "the job exited 1"), ("times out", "the job timed out after 3s")],
)
def test_a_job_that_made_the_real_homes_personalclaw_and_failed_is_named_for_both(
    tmp_path, monkeypatch, ending, first
):
    planted = tmp_path / "real-home"
    planted.mkdir()
    _planted_real_home(monkeypatch, planted)
    made = planted / ".personalclaw"
    then = {"exits 1": "sys.exit(1)", "times out": "signal.pause()"}[ending]
    job = [sys.executable, "-c", f"import os, signal, sys; os.makedirs({str(made)!r}); {then}"]

    result = _run(tmp_path, job, timeout=3.0, grace=0.5)

    assert result.code == run.RED, result
    assert result.violations == (
        first,
        f"the job made the real home's {made}, which was absent as it started",
    )


@pytest.mark.parametrize(
    "entry", [None, "", "relative/home"], ids=["no entry", "an empty home", "a relative home"]
)
def test_a_runner_whose_user_database_entry_names_no_home_starts_no_job(
    tmp_path, monkeypatch, entry
):
    def no_entry(uid: int):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    if entry is None:
        monkeypatch.setattr(pwd, "getpwuid", no_entry)
    else:
        _planted_real_home(monkeypatch, entry)
    # A runner that fell back to $HOME would find one, and run the job; one that took a relative
    # home as it stands would watch a folder under wherever it was started.
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "the-job-ran"
    why = "no entry in the user database" if entry is None else f"names {entry!r} as user id"
    with pytest.raises(RuntimeError, match=rf"{why}.*runs no job"):
        _run(tmp_path, [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])
    assert not marker.exists()


def test_an_interrupted_run_whose_job_made_the_real_homes_personalclaw_is_red(tmp_path):
    planted = tmp_path / _PLANTED_REAL_HOME  # the home RUNNER's user database names
    planted.mkdir()
    made = planted / ".personalclaw"
    kw = {"timeout": 60.0}
    job = _made_by_path(made, "a folder")
    with _runner(tmp_path, job, kw, when="ended", signum=signal.SIGINT) as (runner, out, _):
        assert runner.wait(timeout=60) == run.RED, "an isolation breach outranks an interrupt"
        assert _result(out)[2] == [
            "interrupted by SIGINT",
            f"the job made the real home's {made}, which was absent as it started",
        ]


#: A job that makes the folder its first argument names, says it is ready, and blocks.
MAKES_THEN_BLOCKED = (
    "import os, signal, sys\n"
    "os.makedirs(sys.argv[1])\n"
    "print('ready', os.getpid(), flush=True)\n"
    "signal.pause()\n"
)


def test_a_run_interrupted_while_its_job_runs_after_making_the_real_homes_personalclaw_is_red(
    tmp_path,
):
    planted = tmp_path / _PLANTED_REAL_HOME  # the home RUNNER's user database names
    planted.mkdir()
    made = planted / ".personalclaw"
    kw = {"timeout": 120.0, "grace": 0.5}
    job = [sys.executable, "-c", MAKES_THEN_BLOCKED, str(made)]
    with _runner(tmp_path, job, kw) as (runner, out, groups):
        group = int(_line(out, "ready")[1])
        groups.append(group)
        runner.send_signal(signal.SIGINT)
        assert runner.wait(timeout=60) == run.RED, "an isolation breach outranks an interrupt"
        assert _result(out)[2] == [
            "interrupted by SIGINT",
            f"the job made the real home's {made}, which was absent as it started",
        ]
        assert _gone(group)
