"""The install one-liner — the front door — gets a rail, and its header stops lying.

``deploy/website/install.sh`` is what ``curl -fsSL https://personalclaw.dev/install | sh``
executes on a stranger's machine. Until now **nothing in CI ran it, linted it, or compared
it to what we serve**, while its own header claimed all three:

    # ... it is staged here under deploy/website/ so it is version-controlled,
    # shellcheck-clean, and CI-smoke-tested (plan 33 full.yml runs it in a bare ubuntu
    # container weekly).

MEASURED (2026-09-06): ``full.yml`` had four jobs — ``matrix``, ``audit``,
``security-corpus``, ``coverage`` — and none touched the installer; grepping
``.github/workflows/`` for ``website/install``, ``install.sh`` or
``personalclaw.dev/install`` returned nothing. The advertised job had never been written.
That is this repo's ninth absent-vs-declared-false: the claim of coverage was the only
artifact, and a comment nobody can execute cannot go red.

The claim also hid a real regression. Three digests, measured the same day:

    staged  deploy/website/install.sh   d7a852c1…  "workspace directory + timezone"
    site    public/install (mirror)     8e06a1fd…  "your name + first model provider"
    served  https://personalclaw.dev/install  8e06a1fd…  (identical to the site mirror)

The staged copy is AUTHORITATIVE and the served bytes are the stale ones — the opposite of
what it looks like from the site. The site's ``public/install`` has exactly one commit
(``e21bdee``, 2026-07-21), the original hand-apply; core then corrected the line in
``72e43db3b`` (PR #1642, 2026-08-18), whose own message says the installer "claimed setup
collects a name + first provider credential — it collects neither, and cannot". Confirmed
against the wizard: ``cli_setup._setup`` calls ``_setup_workspace_dir`` (step 1) and
``_setup_timezone`` (step 4) and has no name or provider prompt at all — provider binding
moved to the dashboard (``_print_dashboard_pointer``: "the dashboard owns it"). So for
three weeks personalclaw.dev told every new user to run a command that would do neither of
the two things it advertised.

The sync path is a HUMAN COPY (``deploy/website/README.md`` §1: "**To apply:** copy
``deploy/website/install.sh`` into the website repo"). It is not broken code — there is no
code. An unenforced instruction drifted the moment someone edited one side, which is why
the fix here is a rail and not a re-copy.

What this module asserts, and why each half exists:

* **Offline, every PR** (rides the ``test`` job; the ``lint`` job re-runs it with
  ``PERSONALCLAW_REQUIRE_INSTALL_PROOF=1`` after installing shellcheck + dash, so a missing
  linter is a RED there rather than a quiet skip): POSIX syntax, the three argument paths
  that need no network, the truncation-safe structure that makes ``curl | sh`` fail closed,
  and ``sha256(install.sh) == install.sh.sha256``.
* **The digest pin is the forcing function.** Editing the installer without updating the
  pin reds at lint speed, and the pin line in the diff is where a reviewer sees "the site
  mirror needs re-applying". Offline and network-free, so it can gate every PR — the live
  comparison cannot.
* **Header truth.** Every workflow file and job id the header names must exist, and the
  jobs must really name the installer. This is the direct rail over the defect above: the
  next person who writes "CI-smoke-tested" without writing the job gets a red.
* **Rail wiring.** ``full.yml``'s live leg must run BOTH the staged file and the served
  bytes, redirect uv's tool dirs, and report ``unproven`` (never green) when the fetch
  fails. Asserted here because the wiring is now the load-bearing part.

Parsed with a line scan rather than PyYAML, which is not a test dependency — same
convention as ``test_browse_live_legs_run_in_ci.py`` and ``test_ci_tier_enforcement.py``.
Every helper takes its text as an argument so the vacuity tests at the bottom can drive the
same functions with synthetic workflows and watch each answer change.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_INSTALLER = _ROOT / "deploy" / "website" / "install.sh"
_PIN = _ROOT / "deploy" / "website" / "install.sh.sha256"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_FULL = _ROOT / ".github" / "workflows" / "full.yml"

#: The URL the one-liner is served from. The live legs in ``full.yml`` must name it.
SERVED_URL = "https://personalclaw.dev/install"

#: Turns "the tool this case needs is absent" from a skip into a failure. Set by the
#: ``lint`` job, which installs shellcheck and dash first. NOT set on a contributor's
#: laptop, where reding the suite over a missing linter would be hostile.
REQUIRE_ENV = "PERSONALCLAW_REQUIRE_INSTALL_PROOF"

#: The installer's single dispatching statement. Load-bearing for `curl | sh` safety: see
#: :class:`TestTruncatedDownloadFailsClosed`.
DISPATCH = 'main "$@"'

#: ``/bin/sh`` by absolute path, because the offline cases hand the child a PATH that holds only
#: :data:`SANDBOX_TOOLS` — which does not include a shell. Resolving the interpreter through that
#: PATH raised ``FileNotFoundError: 'sh'``, so the cases errored on the interpreter and their real
#: assertions never ran. Naming it absolutely keeps the restricted PATH a statement about what the
#: SCRIPT can reach rather than an accident that skips the measurement.
SH = "/bin/sh"

#: External commands the installer legitimately uses on its no-network paths. A sandbox PATH is
#: built from exactly these (:func:`sandbox_env`), which is what makes "no download was
#: attempted" a measurement rather than an assumption: ``curl``, ``wget`` and ``uv`` are absent
#: BY CONSTRUCTION, so any leg that tried to fetch dies visibly instead of quietly succeeding
#: because the runner happened to have a downloader.
#:
#: An empty PATH was the first attempt and was wrong twice over: ``subprocess`` could not find
#: the interpreter, and once that was fixed ``--container`` failed on ``cat`` — an honest but
#: uninteresting red about coreutils rather than about the installer. Allow the harmless
#: externals, deny only the downloaders, and the red means what it says.
SANDBOX_TOOLS = ("cat", "uname", "printf")

#: Commands that must NOT be reachable in the sandbox. Asserted, not merely omitted — a future
#: edit to :data:`SANDBOX_TOOLS` that let a downloader back in would make every "no network"
#: claim below vacuous, and nothing else would say so.
FORBIDDEN_TOOLS = ("curl", "wget", "uv")

#: Job ids long enough in these workflows that their absence means the parser broke rather
#: than that CI changed. The vacuity floor for :func:`jobs`.
LONG_STANDING_CI_JOBS = frozenset({"lint", "test", "web", "rails", "harness", "client"})
LONG_STANDING_FULL_JOBS = frozenset({"matrix", "audit", "security-corpus", "coverage"})


# ── parsers (text in, answer out — so the vacuity tests can drive them) ───────────────


def jobs(text: str) -> dict[str, str]:
    """Job id → the block of ``text`` belonging to it.

    A job id is the only key at two-space indent in a workflow: a job's own keys
    (``runs-on``, ``env``, ``steps``) sit at four and steps deeper still, so the scan needs
    no YAML.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line.strip() and not line.startswith(" ") and not line.startswith("#"):
            break  # back to a top-level key: the jobs mapping ended
        header = re.match(r"^ {2}([A-Za-z][\w-]*):\s*(#.*)?$", line)
        if header:
            current = header.group(1)
            found[current] = []
            continue
        if current is not None:
            found[current].append(line)
    return {k: "\n".join(v) for k, v in found.items()}


def header_block(text: str) -> str:
    """The leading comment block of a shell script (the shebang and every ``#`` line after).

    Stops at the first line that is neither a comment nor blank, so only the header — the
    part that makes claims about CI — is returned.
    """
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            out.append(line)
            continue
        break
    return "\n".join(out)


def header_claims(text: str) -> set[tuple[str, str]]:
    """``(workflow filename, job id)`` pairs claimed by a script's header.

    A claim is a ``*.yml`` filename and a backtick-quoted identifier on the SAME header
    line — the shape the header uses to tabulate its coverage. Derived rather than pinned:
    rewording the prose is free, naming a job that does not exist is not.
    """
    claims: set[tuple[str, str]] = set()
    for line in header_block(text).splitlines():
        workflows = re.findall(r"\b([A-Za-z][\w.-]*\.yml)\b", line)
        ids = re.findall(r"`([A-Za-z][\w-]*)`", line)
        for wf in workflows:
            for job in ids:
                claims.add((wf, job))
    return claims


def top_level_calls(text: str, funcs: set[str]) -> list[tuple[int, str]]:
    """``(line number, line)`` for every column-0 line that invokes one of ``funcs``.

    Column 0 is the only depth at which the script's own statements run at load time; calls
    inside a function body are indented, and this script's one heredoc body holds prose
    whose first word is never a function name. So the first token of a column-0 line being
    a defined function name means "this runs as the file is read".
    """
    hits: list[tuple[int, str]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line or line[0] in " \t#":
            continue
        first = line.split()[0] if line.split() else ""
        if first in funcs:
            hits.append((n, line))
    return hits


def defined_funcs(text: str) -> set[str]:
    """Every function name the script defines (``name() {`` at column 0)."""
    return set(re.findall(r"^([A-Za-z_][\w]*)\s*\(\)", text, flags=re.MULTILINE))


def _tool_or_skip(tool: str) -> str:
    """Path to ``tool``, or skip — unless :data:`REQUIRE_ENV` says absence is a failure."""
    path = shutil.which(tool)
    if path:
        return path
    msg = (
        f"{tool} is not installed, so this claim was NOT proven. "
        f"CI sets {REQUIRE_ENV}=1 (ci.yml `lint`) to make this a failure instead of a skip."
    )
    if os.environ.get(REQUIRE_ENV) == "1":
        pytest.fail(msg)
    pytest.skip(msg)
    raise AssertionError("unreachable")  # pragma: no cover


@pytest.fixture(scope="module")
def installer_text() -> str:
    return _INSTALLER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sandbox_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """A child environment whose PATH holds :data:`SANDBOX_TOOLS` and nothing else.

    ``HOME`` points at a path that does not exist, so a leg that tried to write into the
    user's home — installing uv, editing a shell profile — fails loudly instead of leaving
    real state on whoever ran the suite.
    """
    bindir = tmp_path_factory.mktemp("installer-sandbox-bin")
    for tool in SANDBOX_TOOLS:
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    home = tmp_path_factory.mktemp("installer-sandbox-home") / "nonexistent"
    return {"PATH": str(bindir), "HOME": str(home)}


def test_sandbox_really_has_no_downloader(sandbox_env: dict[str, str]) -> None:
    """The floor under every "no network was touched" claim in this module.

    If ``curl``/``wget``/``uv`` were reachable, the offline cases could pass while quietly
    downloading, and :class:`TestOfflineArgumentPaths`' whole point would evaporate.
    """
    for tool in FORBIDDEN_TOOLS:
        assert shutil.which(tool, path=sandbox_env["PATH"]) is None, (
            f"{tool} is reachable inside the sandbox PATH, so the offline cases below prove "
            "nothing about staying offline."
        )
    assert shutil.which("cat", path=sandbox_env["PATH"]), "sandbox lost cat — --container needs it"


# ── the file exists at all ───────────────────────────────────────────────────────────


def test_installer_and_pin_are_present() -> None:
    """The whole module is vacuous if these two paths move without it noticing."""
    assert _INSTALLER.is_file(), f"{_INSTALLER} is missing — the served one-liner is unstaged"
    assert _PIN.is_file(), f"{_PIN} is missing — nothing pins the bytes the site must serve"
    # If /bin/sh ever moves, the offline cases would raise FileNotFoundError on the interpreter
    # and their real assertions would never be reached. Fail on the cause, not the symptom.
    assert pathlib.Path(SH).exists(), f"{SH} is missing — the offline cases cannot run"


# ── POSIX + linter contract (the header's "shellcheck-clean") ────────────────────────


class TestPosixShellContract:
    """It runs under whatever ``/bin/sh`` is: dash on Debian/Ubuntu, ash on Alpine."""

    def test_sh_n_is_clean(self) -> None:
        """``sh`` always exists, so this case can never be skipped into a false pass."""
        proc = subprocess.run(
            ["sh", "-n", str(_INSTALLER)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, f"sh -n rejected the installer:\n{proc.stderr}"

    def test_dash_n_is_clean(self) -> None:
        """dash is the strictest common ``/bin/sh`` — it catches bashisms ``sh`` may allow."""
        dash = _tool_or_skip("dash")
        proc = subprocess.run(
            [dash, "-n", str(_INSTALLER)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, f"dash -n rejected the installer:\n{proc.stderr}"

    def test_shellcheck_is_clean(self) -> None:
        """The header has claimed "shellcheck-clean" since the file landed. Now it is checked."""
        shellcheck = _tool_or_skip("shellcheck")
        proc = subprocess.run(
            [shellcheck, "-s", "sh", str(_INSTALLER)], capture_output=True, text=True, timeout=120
        )
        assert proc.returncode == 0, f"shellcheck -s sh findings:\n{proc.stdout}{proc.stderr}"


# ── the argument paths that need no network ──────────────────────────────────────────


class TestOfflineArgumentPaths:
    """``--help``, ``--container`` and a bad argument all return before any download.

    Each runs in the :func:`sandbox_env` PATH, where ``curl``, ``wget`` and ``uv`` do not
    exist. A regression that moved network work ahead of argument parsing fails here instead
    of on a stranger's machine.
    """

    @staticmethod
    def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [SH, str(_INSTALLER), *args],
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(env),
        )

    def test_help_exits_zero_and_documents_container(self, sandbox_env: dict[str, str]) -> None:
        proc = self._run(sandbox_env, "--help")
        assert proc.returncode == 0, proc.stderr
        assert "--container" in proc.stdout

    def test_container_prints_the_compose_snippet(self, sandbox_env: dict[str, str]) -> None:
        proc = self._run(sandbox_env, "--container")
        assert proc.returncode == 0, proc.stderr
        assert "docker compose" in proc.stdout

    def test_unknown_argument_fails_closed(self, sandbox_env: dict[str, str]) -> None:
        """An unrecognised flag must not fall through into an install."""
        proc = self._run(sandbox_env, "--bogus")
        assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}"
        assert "unknown argument" in proc.stderr

    def test_no_downloader_available_refuses_rather_than_half_installing(
        self, sandbox_env: dict[str, str]
    ) -> None:
        """The real install path, run where nothing can download: it must DIE, not limp on.

        This is the one offline case that enters ``main`` for real. With no ``uv`` and no
        ``curl``/``wget``, ``ensure_uv`` has to stop with a nonzero status and a message
        naming the missing prerequisite. A version that warned and continued would reach
        ``uv tool install`` with no uv and leave the user with a half-configured PATH and an
        exit code saying everything was fine.
        """
        proc = self._run(sandbox_env)
        assert proc.returncode != 0, (
            "the installer exited 0 on a machine with no uv and no downloader. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        assert "curl or wget" in proc.stderr, (
            "it failed, but without naming the missing prerequisite: " f"{proc.stderr!r}"
        )


# ── curl | sh truncation safety ──────────────────────────────────────────────────────


class TestTruncatedDownloadFailsClosed:
    """A dropped connection mid-``curl | sh`` must install nothing.

    ``sh`` reads a pipe incrementally and executes as it goes, so a truncated body runs
    whatever prefix arrived. This script is safe by SHAPE: everything is a function
    definition, and the single call that does anything — ``main "$@"`` — is the last line.
    Truncate anywhere and you get definitions and an EOF, never a half-install.

    That property is invisible and one careless append destroys it, which is why it is
    railed rather than trusted.
    """

    def test_dispatch_is_the_last_line(self, installer_text: str) -> None:
        last = [ln for ln in installer_text.splitlines() if ln.strip()][-1]
        assert last.strip() == DISPATCH, (
            f"the last non-blank line is {last.strip()!r}, not {DISPATCH!r}. Anything after "
            "the dispatch runs from a truncated download, so `curl | sh` stops failing closed."
        )

    def test_nothing_the_script_defines_runs_before_the_dispatch(self, installer_text: str) -> None:
        funcs = defined_funcs(installer_text)
        assert "main" in funcs, "no main() — the parser or the script shape changed"
        calls = top_level_calls(installer_text, funcs)
        assert calls, "found no top-level call at all, not even the dispatch — parser broke"
        assert len(calls) == 1, (
            "more than one of the script's own functions runs at load time: "
            f"{calls!r}. Only the final dispatch may."
        )
        assert calls[0][1].strip() == DISPATCH

    def test_every_truncation_prefix_installs_nothing(
        self, installer_text: str, sandbox_env: dict[str, str]
    ) -> None:
        """Behavioural half: feed ``sh`` every line-prefix and assert no step ever begins.

        ``==>`` — what ``step()`` prints — is the tell that the installer started work, and
        the first ``step()`` on the install path (``ensure_uv``) fires before anything is
        fetched, so it trips even inside the sandbox where nothing can download. A prefix
        that ends mid-definition is a syntax error, which is equally fine: failing closed is
        the contract, exiting zero is not required of a truncated file.

        Every prefix rather than a sample, because the interesting ones are unguessable —
        the boundary that matters is wherever a future edit happens to put a statement.
        """
        lines = installer_text.splitlines(keepends=True)
        started: list[int] = []
        for count in range(1, len(lines)):
            proc = subprocess.run(
                [SH, "-s"],
                input="".join(lines[:count]),
                capture_output=True,
                text=True,
                timeout=60,
                env=dict(sandbox_env),
            )
            if "==>" in proc.stdout:
                started.append(count)
        assert not started, (
            "a truncated download began installing at these line counts: "
            f"{started!r}. `curl | sh` no longer fails closed on a partial transfer."
        )

    def test_the_complete_file_does_reach_a_step(self, sandbox_env: dict[str, str]) -> None:
        """Vacuity floor for the sweep above: the WHOLE file must trip the ``==>`` tell.

        Without this, a rename of ``step()`` or a change to its marker would make the
        truncation sweep pass by looking for a string the installer can no longer print —
        the sweep would be measuring nothing and reporting green.
        """
        proc = subprocess.run(
            [SH, "-s"],
            input=_INSTALLER.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            timeout=120,
            env=dict(sandbox_env),
        )
        assert "==>" in proc.stdout, (
            "the complete installer never printed the `==>` marker the truncation sweep "
            f"looks for, so that sweep cannot fail. stdout={proc.stdout!r}"
        )


# ── the digest pin: the offline half of the drift rail ───────────────────────────────


class TestServedDigestPin:
    """``install.sh.sha256`` is the contract between this repo and personalclaw.dev.

    The website repo's ``public/install`` is a hand-applied mirror with no automation
    behind it, so "staged and served agree" cannot be checked without the network — and a
    PR gate that reaches a third-party host on every push is a gate that goes red when
    someone else's CDN hiccups. The pin splits the check in two: this half is offline and
    gates every PR, and ``full.yml``'s live leg compares the pin to the real bytes.
    """

    def test_pin_matches_the_staged_installer(self, installer_text: str) -> None:
        actual = hashlib.sha256(_INSTALLER.read_bytes()).hexdigest()
        recorded = _PIN.read_text(encoding="utf-8").split()[0]
        assert actual == recorded, (
            f"install.sh has changed but install.sh.sha256 was not updated "
            f"(file {actual}, pin {recorded}).\n"
            "Update the pin AND re-apply the file to the website repo's public/install in "
            "the same change — see deploy/website/README.md §1. The pin is deliberately in "
            "your way: it is the only moment anyone is reminded the mirror exists."
        )

    def test_pin_is_shasum_check_format(self) -> None:
        """``shasum -a 256 -c`` in the workflow consumes this file; keep it parseable."""
        text = _PIN.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert len(lines) == 1, f"expected exactly one digest line, got {lines!r}"
        digest, _, name = lines[0].partition(" ")
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"not a sha256 digest: {digest!r}"
        assert name.strip() == "install.sh", f"the pin must name install.sh, not {name.strip()!r}"


# ── header truth: the rail over the defect this file was written for ─────────────────


class TestHeaderClaimsAreTrue:
    """Every workflow and job the installer's header advertises must actually exist.

    The header used to promise a weekly ``full.yml`` container smoke that had never been
    written. A comment claiming coverage is worse than no comment: it answers the question
    a reader would otherwise go and check. These cases make the claim executable.
    """

    def test_header_makes_at_least_one_checkable_claim(self, installer_text: str) -> None:
        claims = header_claims(installer_text)
        assert claims, (
            "the header names no workflow/job pair, so nothing about its CI claims is "
            "checkable. Either tabulate the real jobs or make no claim."
        )

    def test_every_workflow_the_header_names_exists(self, installer_text: str) -> None:
        for wf, _job in sorted(header_claims(installer_text)):
            assert (
                _ROOT / ".github" / "workflows" / wf
            ).is_file(), f"the header claims coverage in {wf}, which does not exist"

    def test_every_job_the_header_names_exists(self, installer_text: str) -> None:
        for wf, job in sorted(header_claims(installer_text)):
            text = (_ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
            present = jobs(text)
            assert job in present, (
                f"the header claims {wf} job `{job}` covers the installer, but {wf} has "
                f"no such job (it has: {sorted(present)}). This is exactly the defect the "
                "file was carrying: an advertised job that was never written."
            )


# ── rail wiring: the live leg must exist, and must not be able to pass vacuously ─────


def code_only(body: str) -> str:
    """``body`` with whole-line ``#`` comments removed.

    Every "the leg does X" assertion below must read the leg's actual YAML and shell, not its
    prose. This job is heavily commented and those comments NAME the things being asserted —
    ``UV_TOOL_DIR``, ``install.sh.sha256``, even ``continue-on-error`` (in a comment
    explaining why it must not be used). Matching against the raw body let the commentary
    satisfy the assertions: measured, ``test_it_redirects_uv_tool_dirs`` passed on a comment,
    and the ``continue-on-error`` guard failed on one. Documenting a rail is not having it.
    """
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))


def fetch_guards(body: str) -> list[str]:
    """Every ``if ! curl …; then … fi`` block in a job body, as text.

    The unit that matters for "an inconclusive run must not be green" is not the job and not
    a word — it is each individual guard around a network fetch. A whole-body substring check
    is too loose to notice a regression *inside* one guard: renaming the message while an
    ``::error title=… unproven::`` annotation survived elsewhere in the same job left the
    assertion passing, which is how this helper came to exist.

    Nesting is not handled because these guards do not nest; the first ``fi`` at the guard's
    own indent closes it.
    """
    lines = body.splitlines()
    guards: list[list[str]] = []
    current: list[str] | None = None
    indent = 0
    for line in lines:
        if current is None:
            if re.search(r"if\s+!\s+curl\b", line):
                current = [line]
                indent = len(line) - len(line.lstrip())
            continue
        current.append(line)
        if line.strip() == "fi" and (len(line) - len(line.lstrip())) == indent:
            guards.append(current)
            current = None
    if current is not None:  # unterminated — hand it back so the caller can fail on it
        guards.append(current)
    return ["\n".join(g) for g in guards]


def _live_leg(full_text: str) -> tuple[str, str]:
    """The ``full.yml`` job that exercises the served one-liner — located by BEHAVIOUR.

    Found by "the job that mentions the served URL", not by its id, so renaming it stays
    green while deleting it does not.
    """
    matches = {jid: body for jid, body in jobs(full_text).items() if SERVED_URL in body}
    assert matches, (
        f"no job in full.yml mentions {SERVED_URL}. The installer's live rail is gone, "
        "and the header's smoke claim is false again."
    )
    assert len(matches) == 1, f"expected one live installer leg, found {sorted(matches)}"
    return next(iter(matches.items()))


class TestLiveInstallerLegIsWired:
    """The network half — and the reason a network failure here cannot read as a pass."""

    @pytest.fixture(scope="class")
    @classmethod
    def leg(cls) -> tuple[str, str]:
        return _live_leg(_FULL.read_text(encoding="utf-8"))

    def test_full_yml_has_a_live_installer_leg(self, leg: tuple[str, str]) -> None:
        jid, _body = leg
        assert jid, "unreachable — _live_leg asserts this"

    def test_it_exercises_the_staged_file_too(self, leg: tuple[str, str]) -> None:
        """Served-only would not catch a broken edit until after it was hand-applied.

        Running both separates "our next installer is broken" (staged red) from "the site
        or PyPI regressed" (served red) — two different pages for two different people.

        Asserted as EXECUTION, not as a mention. The drift leg legitimately names the staged
        path twice — ``sha256sum deploy/website/install.sh`` and a ``diff`` against it — so a
        substring check passed with the staged smoke leg deleted outright: measured, the leg
        was reduced to served-only and the case stayed green. Hashing a file is not running
        it.
        """
        _jid, body = leg
        assert re.search(
            r"(?:^|\s)(?:/bin/)?(?:sh|dash|bash)\s+\S*deploy/website/install\.sh",
            code_only(body),
        ), (
            "nothing in the live leg EXECUTES deploy/website/install.sh (hashing or diffing "
            "it does not count). A staged edit that breaks the installer would go green "
            "until someone copied it to the website repo."
        )

    def test_it_pipes_the_served_bytes_into_a_shell(self, leg: tuple[str, str]) -> None:
        """The served leg must run the documented pipeline, not just download the file.

        ``curl … | sh`` is itself part of what is being asserted — no TTY on stdin, and a
        partial transfer must install nothing. Fetching to a file and running that file
        would exercise a path no user takes, and would quietly drop the pipe semantics.
        """
        _jid, body = leg
        assert re.search(
            rf"curl[^\n]*{re.escape(SERVED_URL)}[^\n]*\|\s*(?:/bin/)?sh\b",
            code_only(body).replace("\\\n", " "),
        ), (
            "the served bytes are never piped into a shell, so the leg does not exercise "
            "the one-liner users are actually given."
        )

    def test_it_compares_served_against_the_pin(self, leg: tuple[str, str]) -> None:
        _jid, body = leg
        assert "install.sh.sha256" in code_only(body), (
            "the live leg does not compare the served bytes to install.sh.sha256, so the "
            "drift this rail exists for would still go unnoticed."
        )

    def test_every_fetch_failure_exits_nonzero_and_says_unproven(
        self, leg: tuple[str, str]
    ) -> None:
        """The ninth absent-vs-declared-false is what this case is here to prevent.

        A curl that cannot reach the host proves nothing. If that path exits zero — or
        merely warns — the job reports success for having tested nothing, which is how a
        missing tmux got read as a passing test class in this repo last month.

        Asserted per GUARD, not per job: the first version of this case checked the whole
        job body for the word "unproven", and a mutant that reworded the message inside a
        guard still passed because an annotation title elsewhere in the job carried the word.
        ``exit`` is the load-bearing half — the vocabulary only makes the red legible.
        """
        _jid, body = leg
        guards = fetch_guards(code_only(body))
        assert len(guards) >= 2, (
            "expected a guarded fetch for both the served smoke and the drift comparison; "
            f"found {len(guards)}. An unguarded curl cannot report `unproven` at all."
        )
        for guard in guards:
            assert re.search(r"^\s*exit\s+[1-9]", guard, flags=re.MULTILINE), (
                "a fetch-failure branch does not exit nonzero, so an unreachable host would "
                f"be reported as a passing check:\n{guard}"
            )
            assert "unproven" in guard, (
                "a fetch-failure branch reds without saying it proved nothing. The red must "
                f"be legible as `unproven`, not as a generic failure:\n{guard}"
            )

    def test_the_leg_is_not_continue_on_error(self, leg: tuple[str, str]) -> None:
        """One line would make every red above invisible, and this workflow already uses it.

        ``full.yml``'s ``audit`` job is deliberately ``continue-on-error: true`` (report-only
        supply-chain scan). Copying that line onto this job — a plausible edit the first time
        a flaky network annoys someone — turns the whole installer rail into decoration that
        reports success while failing, which is the defect this file exists to end.
        """
        _jid, body = leg
        assert "continue-on-error" not in code_only(body), (
            "the live installer leg is marked continue-on-error, so its failures do not fail "
            "the workflow. A rail that cannot go red is a comment. If network flakiness is "
            "the problem, retry the fetch — do not silence the verdict."
        )

    def test_it_redirects_uv_tool_dirs(self, leg: tuple[str, str]) -> None:
        """The smoke must install into a scratch dir, not over whatever the runner has."""
        _jid, body = leg
        code = code_only(body)
        assert "UV_TOOL_DIR" in code and "UV_TOOL_BIN_DIR" in code, (
            "the live leg does not redirect uv's tool dirs, so the smoke writes into the "
            "runner's real tool directories."
        )

    def test_both_smoke_legs_assert_the_installed_binary_runs(self, leg: tuple[str, str]) -> None:
        """Installing without running proves the wheel downloaded, not that it works.

        Counted, not merely found: with one occurrence the assertion is satisfied by whichever
        leg happens to have it, and the other leg can install into its own tool dir and never
        check that anything came out. Two legs, two verdicts.
        """
        _jid, body = leg
        found = code_only(body).count("personalclaw --version")
        assert found >= 2, (
            f"`personalclaw --version` appears {found} time(s) in the live leg; both the "
            "staged and served smokes must run the installed binary, or one of them proves "
            "only that a download happened."
        )


class TestLintJobChecksTheInstaller:
    """The every-PR half of the linter claim, which cannot skip."""

    @pytest.fixture(scope="class")
    @classmethod
    def lint_body(cls) -> str:
        present = jobs(_CI.read_text(encoding="utf-8"))
        assert "lint" in present, f"ci.yml has no lint job (has: {sorted(present)})"
        return present["lint"]

    def test_lint_runs_shellcheck_on_the_installer(self, lint_body: str) -> None:
        """The INVOCATION, not the word.

        The step installs its tools with ``for tool in shellcheck dash``, so "shellcheck
        appears in this job" is true even with every actual check deleted: measured, removing
        both ``shellcheck --version`` and ``shellcheck -s sh …`` left this case green because
        the apt loop still named the tool. Naming a linter is not running it.
        """
        assert re.search(
            r"shellcheck\s+(?:-\S+\s+)*-s\s+sh\s+\S*deploy/website/install\.sh",
            code_only(lint_body),
        ), (
            "ci.yml's lint job never invokes `shellcheck -s sh deploy/website/install.sh`, "
            "so the header's shellcheck-clean claim rests on nothing again."
        )

    def test_lint_runs_both_posix_syntax_checks(self, lint_body: str) -> None:
        """``dash -n`` and ``sh -n`` are not interchangeable, and neither replaces shellcheck.

        Measured while mutating this rail: ``[[ -t 0 ]]`` — a bashism that breaks the script
        under a real dash — passes ``dash -n`` cleanly, because dash parses ``[[`` as an
        ordinary command name and only fails at RUNTIME. shellcheck is the only member of the
        set that rejects it at check time. All three are named here so that dropping one is a
        red rather than a silent narrowing of what "POSIX-clean" means.
        """
        code = code_only(lint_body)
        for tool in ("sh -n", "dash -n"):
            assert re.search(
                rf"{re.escape(tool)}\s+\S*deploy/website/install\.sh", code
            ), f"ci.yml's lint job does not run `{tool}` on the installer"

    def test_lint_sets_the_require_proof_lever(self, lint_body: str) -> None:
        """Without the lever, a runner image that drops shellcheck turns red into skip."""
        assert REQUIRE_ENV in code_only(lint_body), (
            f"ci.yml's lint job does not set {REQUIRE_ENV}, so a missing shellcheck or "
            "dash would silently skip the cases that prove the installer lints."
        )


# ── vacuity: the parsers must be able to say NO ──────────────────────────────────────


class TestParsersAreNotVacuous:
    """Every assertion above is only worth its ability to fail. Drive it and watch."""

    def test_jobs_finds_the_real_workflows(self) -> None:
        ci = set(jobs(_CI.read_text(encoding="utf-8")))
        full = set(jobs(_FULL.read_text(encoding="utf-8")))
        assert LONG_STANDING_CI_JOBS <= ci, f"parser lost ci.yml jobs: {LONG_STANDING_CI_JOBS - ci}"
        assert (
            LONG_STANDING_FULL_JOBS <= full
        ), f"parser lost full.yml jobs: {LONG_STANDING_FULL_JOBS - full}"

    def test_jobs_ignores_step_level_keys(self) -> None:
        parsed = jobs("jobs:\n  only:\n    runs-on: x\n    steps:\n      - name: not-a-job\n")
        assert set(parsed) == {"only"}

    def test_jobs_stops_at_the_end_of_the_mapping(self) -> None:
        parsed = jobs("jobs:\n  a:\n    runs-on: x\nconcurrency:\n  group: g\n")
        assert set(parsed) == {"a"}

    def test_header_block_stops_at_the_first_statement(self) -> None:
        assert "set -eu" not in header_block("#!/bin/sh\n# claim\n\nset -eu\n# later\n")
        assert "claim" in header_block("#!/bin/sh\n# claim\n\nset -eu\n")

    def test_header_claims_pairs_only_within_a_line(self) -> None:
        assert header_claims("# ci.yml `lint` does it\n") == {("ci.yml", "lint")}
        # A workflow on one line and a job id on another is not a claim about that pair.
        assert header_claims("# ci.yml runs\n# `lint` separately\n") == set()

    def test_header_claims_finds_nothing_in_an_unclaiming_header(self) -> None:
        assert header_claims("#!/bin/sh\n# just a script\n") == set()

    def test_missing_job_is_detected(self) -> None:
        """The exact defect: a header naming a job the workflow does not have."""
        claimed = header_claims("#!/bin/sh\n# full.yml `install-smoke` runs it weekly\n")
        assert claimed == {("full.yml", "install-smoke")}
        present = jobs("jobs:\n  matrix:\n    runs-on: x\n  audit:\n    runs-on: x\n")
        assert not {job for _wf, job in claimed} <= set(present)

    def test_live_leg_absent_is_an_error(self) -> None:
        with pytest.raises(AssertionError, match="At least one|no job in full.yml"):
            _live_leg("jobs:\n  matrix:\n    runs-on: x\n")

    def test_code_only_strips_prose_but_keeps_script(self) -> None:
        """The measured hole: commentary that names the very thing being asserted."""
        body = (
            "    # UV_TOOL_DIR is redirected, and continue-on-error must never appear\n"
            "    steps:\n"
            "      - run: export UV_TOOL_DIR=/tmp/x\n"
        )
        code = code_only(body)
        assert "continue-on-error" not in code, "a comment still satisfies the negative check"
        assert "UV_TOOL_DIR" in code, "stripped too much — the real assignment is gone"

    def test_code_only_leaves_a_comment_only_body_empty_of_claims(self) -> None:
        """A leg that only TALKS about the rail must not satisfy the rail."""
        assert (
            code_only("      # runs personalclaw --version and pins install.sh.sha256\n").strip()
            == ""
        )

    def test_fetch_guards_finds_the_real_ones(self) -> None:
        """The live leg must really contain the guards the assertions iterate over."""
        _jid, body = _live_leg(_FULL.read_text(encoding="utf-8"))
        guards = fetch_guards(body)
        assert len(guards) >= 2, f"parser found {len(guards)} fetch guards in the live leg"
        assert all("curl" in g for g in guards)

    def test_fetch_guards_isolates_each_block(self) -> None:
        """Two guards must come back as two strings, not one run-together blob.

        If they merged, an ``exit 1`` in the first would satisfy the assertion for a second
        guard that had none — the per-guard check would silently become a per-job check
        again, which is the looseness it was written to remove.
        """
        body = (
            "        run: |\n"
            "          if ! curl -o a URL; then\n"
            "            echo first unproven\n"
            "            exit 1\n"
            "          fi\n"
            "          echo between\n"
            "          if ! curl -o b URL; then\n"
            "            echo second only warns\n"
            "          fi\n"
        )
        guards = fetch_guards(body)
        assert len(guards) == 2, f"expected 2 guards, got {len(guards)}: {guards!r}"
        assert "between" not in guards[0], "guards ran together — the isolation is fake"
        assert "exit 1" in guards[0] and "exit 1" not in guards[1]

    def test_fetch_guards_reports_a_warn_only_branch(self) -> None:
        """The mutant that matters: a fetch failure that warns instead of exiting."""
        body = "          if ! curl -o a URL; then\n            echo oh well\n          fi\n"
        (guard,) = fetch_guards(body)
        assert not re.search(r"^\s*exit\s+[1-9]", guard, flags=re.MULTILINE)

    def test_live_leg_found_by_url_not_by_name(self) -> None:
        jid, body = _live_leg(
            "jobs:\n  renamed-anything:\n    steps:\n      - run: curl " + SERVED_URL + "\n"
        )
        assert jid == "renamed-anything" and SERVED_URL in body

    def test_top_level_calls_ignores_indented_calls(self) -> None:
        script = "f() {\n    say hi\n}\nsay() { :; }\nf\n"
        hits = top_level_calls(script, {"f", "say"})
        assert [h[1] for h in hits] == ["f"]

    def test_top_level_calls_sees_an_appended_dispatch(self) -> None:
        """Appending after the dispatch is precisely what breaks truncation safety."""
        script = 'main() { :; }\nmain "$@"\nmain --again\n'
        assert len(top_level_calls(script, defined_funcs(script))) == 2

    def test_defined_funcs_finds_the_real_ones(self, installer_text: str) -> None:
        found = defined_funcs(installer_text)
        assert {"main", "ensure_uv", "install_personalclaw", "offer_setup"} <= found
