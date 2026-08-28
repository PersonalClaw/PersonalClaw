"""BA-2's behavioural clauses must be proven ON THE MERGE GATE, not on whoever's laptop.

``PERSONALCLAW_REQUIRE_BROWSE_PROOF`` (``tests/browse_chrome.py``) already turns a missing
browser from a skip into a failure. That lever protects nothing on its own: **nothing in the
repo set it**, and the ``test`` job installs no browser, so on the machine that gates every
merge the behavioural layer skipped. Measured on a browser-less tree: **20 skipped**, spread
over ``test_browse_safety_script.py`` (10), ``test_browse_cdp_live.py`` (9) and — the part that
made it invisible — ``test_browse_behavioural_proof_is_reachable.py``'s own load-bearing case
(1). A skipped surface reads exactly like a pass, so the green check said "the safety script
blocks ``fetch()``" while having never opened a browser.

``ci.yml``'s ``browse-live`` job closes that: it installs a Chromium and sets the require-env,
so a browser that is missing, renamed by a Playwright bump, or unlaunchable is a RED there
instead of twenty quiet skips. This file is the rail over that WIRING, because the wiring is
now the load-bearing part — delete the env line, or drop a module from the pytest invocation,
and the proof silently stops running again with nothing to say so.

Deliberately derived, not pinned by name:

* **The leg is found by what it does**, not by its job id — "the job that runs pytest on the
  browse-proof modules". A rename stays green; deleting the job does not.
* **The module list comes from the source.** Every ``tests/test_*.py`` that imports
  ``browse_chrome`` can skip on a missing browser, so every one of them must be named on the
  leg. A new ``test_browse_..._live.py`` that nobody adds to ``ci.yml`` would otherwise skip
  forever, which is the one-sided-inventory failure this repo keeps meeting.
* **Every path the leg names must exist.** A typo'd or renamed path is the ``[0 items]`` trap:
  pytest can report a clean exit having collected nothing, and the count is the only tell. Here
  it is a red at lint-speed, before CI.

Parsed with a line scan rather than PyYAML, which is not a test dependency (same convention as
``test_ci_tier_enforcement.py`` and ``test_e2e_specs_are_executed.py``). Every helper takes its
text as an argument so the vacuity tests at the bottom can drive the SAME functions with
synthetic workflows and watch each answer change.
"""

from __future__ import annotations

import ast
import pathlib
import re

import browse_chrome
import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TESTS = _ROOT / "tests"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"

#: The job id as it stands today. Used only in failure messages — the assertions locate the leg
#: by behaviour, so renaming the job is not a regression and must not read as one.
EXPECTED_LEG = "browse-live"

#: Job ids that have been in this workflow long enough that their absence means the parser
#: broke rather than that CI changed. The vacuity floor for :func:`jobs`.
LONG_STANDING_JOBS = frozenset({"lint", "test", "web", "rails", "harness", "client"})


# ── the parser ────────────────────────────────────────────────────────────────


def jobs(text: str) -> dict[str, str]:
    """Job id → the block of ``text`` belonging to it.

    A job id is the only key at two-space indent in this file: a job's own keys (``runs-on``,
    ``env``, ``steps``) sit at four, and steps deeper still. So the scan needs no YAML.
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
    return {name: "\n".join(body) for name, body in found.items()}


def run_commands(block: str) -> list[str]:
    """Every shell command a job's steps run — single-line ``run:`` values and scalar bodies.

    Block scalars matter here: an install step is exactly the sort of thing someone later folds
    into a ``run: |``, and a scan that only saw single lines would then report "this leg does not
    install a browser" (a false red) or, worse, miss a moved pytest invocation.
    """
    out: list[str] = []
    body_indent: int | None = None
    for line in block.splitlines():
        if body_indent is not None:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped and indent < body_indent:
                body_indent = None
            elif stripped and not stripped.startswith("#"):
                out.append(stripped)
                continue
        head = re.match(r"^(\s*)-?\s*run:\s*(.*?)\s*$", line)
        if not head:
            continue
        value = head.group(2)
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            body_indent = len(head.group(1)) + 2
        elif value and not value.startswith("#"):
            out.append(value)
            body_indent = None
    return out


def env_value(block: str, key: str) -> str | None:
    """The value assigned to ``key`` anywhere in a job block, job-level or step-level.

    "Anywhere" on purpose: whether the require-env sits on the job or on the one step is a
    style choice, and a rail that cared would break on a harmless move.
    """
    for line in block.splitlines():
        if line.strip().startswith("#"):
            continue
        hit = re.match(rf"^\s*{re.escape(key)}:\s*(.*?)\s*$", line)
        if hit:
            return hit.group(1).strip().strip('"').strip("'")
    return None


def provisions_a_browser(block: str) -> bool:
    """Whether a job's own steps put a Chromium on the runner.

    Asserted on the RUN line, not on the job text: ``chromium`` appears in this workflow's prose
    in several places, and treating a comment as an install is how a gate ends up certifying an
    intention. Matched by shape (``playwright install`` … ``chromium``) so the flags may change.
    """
    return any("playwright install" in cmd and "chromium" in cmd for cmd in run_commands(block))


def modules_named(block: str) -> set[str]:
    """Repo-relative test paths handed to pytest by a job's steps."""
    named: set[str] = set()
    for cmd in run_commands(block):
        if "pytest" not in cmd:
            continue
        for word in cmd.split():
            if word.startswith("tests/") and word.endswith(".py"):
                named.add(word)
    return named


#: The three ``browse_chrome`` entry points that can end a test without running it. Calling one
#: is what makes a module skippable on a missing browser — and therefore what obliges it to be
#: named on the leg. Merely importing the module is NOT enough: this very file imports it (for
#: ``REQUIRE_ENV`` and the falsey rule) and can never skip for want of a browser.
_GATE_CALLS = frozenset({"chrome_or_skip", "websockets_or_skip", "missing"})


def calls_the_browser_gate(source: str) -> list[str]:
    """Names of :data:`_GATE_CALLS` this module actually CALLS, with line numbers.

    AST rather than a text scan, for the reason ``test_browser_gate_stays_out_of_pytest.py``
    spells out one level up: this file's own prose names all three helpers, and a substring
    search would call that a gate. Asking the syntax tree "is this a call" separates the module
    that can skip from the module that merely talks about skipping.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _GATE_CALLS:
            hits.append(f"{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id in _GATE_CALLS:
            hits.append(f"{func.id}() at line {node.lineno}")
    return hits


def browse_proof_modules() -> set[str]:
    """Test modules that can skip on a missing browser — derived, never listed.

    Conservative by construction: a module that only drives the helpers synthetically (inside a
    ``pytest.raises``) is still named on the leg. That costs a second of runtime and removes a
    judgement call from a rail whose whole job is to not need one.
    """
    return {
        f"tests/{path.name}"
        for path in sorted(_TESTS.glob("test_*.py"))
        if calls_the_browser_gate(path.read_text(encoding="utf-8"))
    }


def the_leg(text: str) -> tuple[str, str]:
    """The job that runs the browse proof, located by behaviour. ``(job_id, block)``."""
    wanted = browse_proof_modules()
    matches = [(name, block) for name, block in jobs(text).items() if modules_named(block) & wanted]
    assert matches, (
        "no job in ci.yml runs pytest on any browse-proof module "
        f"({sorted(wanted)}), so BA-2's behavioural clauses are proven on no gate — their "
        f"green is 20 skips. The job that owned this was `{EXPECTED_LEG}`."
    )
    assert len(matches) == 1, (
        f"more than one job runs the browse proof: {[name for name, _ in matches]}. Two legs "
        "means two places to keep the require-env and the browser install in step, and the "
        "cheapest way to satisfy both is to weaken one."
    )
    return matches[0]


# ── the rails ─────────────────────────────────────────────────────────────────


def test_the_job_scan_is_not_vacuous() -> None:
    """If :func:`jobs` returned nothing, every assertion below would pass."""
    parsed = jobs(_CI.read_text(encoding="utf-8"))
    missing = LONG_STANDING_JOBS - set(parsed)
    assert not missing, f"the job scan lost long-standing jobs {sorted(missing)} — parser broke"
    assert "uv run pytest" in run_commands(parsed["test"]), "the run: scan broke, not ci.yml"


def test_the_module_walk_is_not_vacuous() -> None:
    """The derived inventory must find the proof modules — and must NOT find this one.

    Both directions, because the two mistakes have opposite costs. Too narrow and a live module
    runs on no leg (the defect this file closes). Too wide and it demands the leg run static
    rails, which is how a rail acquires a reputation for being wrong and gets deleted.
    """
    modules = browse_proof_modules()
    assert modules >= {
        "tests/test_browse_safety_script.py",
        "tests/test_browse_cdp_live.py",
        "tests/test_browse_behavioural_proof_is_reachable.py",
    }, f"the gate-call walk found {sorted(modules)} — the walk is wrong, not the repo"
    assert pathlib.Path(__file__).name not in {
        pathlib.PurePosixPath(m).name for m in modules
    }, "this file imports browse_chrome but calls no gate, so it must not be on the browser leg"
    assert "tests/browse_chrome.py" not in modules, "the lookup module is not a test module"


def test_every_browse_proof_module_runs_on_the_merge_gate() -> None:
    """A module that imports the browser gate but runs on no leg skips forever."""
    name, block = the_leg(_CI.read_text(encoding="utf-8"))
    unrun = browse_proof_modules() - modules_named(block)
    assert not unrun, (
        f"these modules can skip on a missing browser but job `{name}` never names them: "
        f"{sorted(unrun)}. Add them to that job's pytest invocation — on the merge gate their "
        "skip is indistinguishable from a pass."
    )


def test_the_leg_actually_puts_a_browser_on_the_runner() -> None:
    """The require-env would turn the whole leg red without this — loud, but not a proof."""
    name, block = the_leg(_CI.read_text(encoding="utf-8"))
    assert provisions_a_browser(block), (
        f"job `{name}` runs the browse proof but no step installs a Chromium, so every "
        "behavioural case either skips (no proof) or fails (no signal). Its install step is "
        "`npx playwright install --with-deps chromium`."
    )


def test_the_leg_demands_the_proof_actually_ran() -> None:
    """The whole point: on this leg, "no browser" must be a FAILURE, not a skip.

    Truthiness is judged by ``browse_chrome``'s own falsey rule rather than a second copy of the
    list. A copy here could drift from the one that decides, and then this rail would certify a
    value the code reads as "off".
    """
    name, block = the_leg(_CI.read_text(encoding="utf-8"))
    value = env_value(block, browse_chrome.REQUIRE_ENV)
    assert value is not None, (
        f"job `{name}` runs the browse proof without setting {browse_chrome.REQUIRE_ENV}, so a "
        "missing or renamed browser skips 20 cases and the job goes GREEN having proven nothing."
    )
    assert not browse_chrome._falsey(value), (
        f"{browse_chrome.REQUIRE_ENV} is set to {value!r} on job `{name}`, which browse_chrome "
        "reads as OFF — the skip is silent again."
    )


def test_the_strictness_is_confined_to_ci() -> None:
    """A contributor without a Chromium must still be able to run the suite.

    So the require-env belongs to one CI job and nowhere a local run would pick it up. Checked
    against the surfaces a developer actually executes, because "CI-only" is a claim about where
    the value is NOT.
    """
    for surface in (
        _ROOT / "Makefile",
        _ROOT / "pyproject.toml",
        _ROOT / "tests" / "conftest.py",
        _ROOT / "scripts" / "run_prepush.sh",
        _ROOT / ".env.example",
    ):
        if not surface.is_file():
            continue
        assert browse_chrome.REQUIRE_ENV not in surface.read_text(encoding="utf-8"), (
            f"{surface.relative_to(_ROOT)} sets or reads {browse_chrome.REQUIRE_ENV}. That makes "
            "a missing browser a hard failure for every contributor, which is exactly the "
            "outcome browse_chrome refuses: the strictness is CI's, not every laptop's."
        )


def test_every_path_the_leg_names_exists() -> None:
    """The ``[0 items]`` trap, closed at pytest time instead of on the runner.

    A renamed module leaves a path in ci.yml that resolves to nothing. Under xdist that can
    collect zero items and still exit clean; the collected COUNT is the only tell, and nobody
    reads it on a green check.
    """
    name, block = the_leg(_CI.read_text(encoding="utf-8"))
    absent = sorted(path for path in modules_named(block) if not (_ROOT / path).is_file())
    assert not absent, f"job `{name}` names test paths that do not exist: {absent}"


def test_the_leg_runs_on_the_pull_request_gate() -> None:
    """ci.yml, not full.yml: a proof that only runs after merge is not a merge gate."""
    header = _CI.read_text(encoding="utf-8").split("jobs:")[0]
    assert re.search(r"^\s*pull_request:", header, re.M), (
        "ci.yml no longer triggers on pull_request, so the browse proof moved off the merge "
        "gate. Re-derive this rail against whatever gates a merge now."
    )


# ── the detectors, both directions ────────────────────────────────────────────

_INSTALL = "        run: npx playwright install --with-deps chromium"
_REQUIRE = '      PERSONALCLAW_REQUIRE_BROWSE_PROOF: "1"'
_PYTEST = "uv run pytest tests/test_browse_cdp_live.py -n0"

_SYNTHETIC = f"""\
name: CI
on:
  pull_request:
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: uv run black --check src
  browse-live:
    runs-on: ubuntu-latest
    env:
{_REQUIRE}
    steps:
      - name: install chromium
{_INSTALL}
      - run: {_PYTEST}
  rails:
    runs-on: ubuntu-latest
    steps:
      - run: uv run pytest tests/test_provider_boundary_residue.py -n0
"""


class TestTheDetectors:
    """Each helper must be able to answer NO. A predicate that only ever says yes is a rail
    that matches nothing, and this whole file exists because of one of those."""

    def test_the_job_split_finds_each_job_separately(self) -> None:
        parsed = jobs(_SYNTHETIC)
        assert set(parsed) == {"lint", "browse-live", "rails"}
        assert "test_browse_cdp_live.py" in parsed["browse-live"]
        assert "test_browse_cdp_live.py" not in parsed["rails"], "job blocks bled into each other"

    def test_a_leg_with_no_install_step_is_caught(self) -> None:
        assert provisions_a_browser(jobs(_SYNTHETIC)["browse-live"])
        stripped = _SYNTHETIC.replace(_INSTALL + "\n", "")
        assert not provisions_a_browser(jobs(stripped)["browse-live"])

    def test_a_commented_out_install_is_not_an_install(self) -> None:
        commented = _SYNTHETIC.replace(_INSTALL, _INSTALL.replace("run:", "# run:"))
        assert not provisions_a_browser(jobs(commented)["browse-live"])

    def test_an_install_inside_a_block_scalar_still_counts(self) -> None:
        body = _INSTALL.split("run: ", 1)[1]
        folded = _SYNTHETIC.replace(_INSTALL, f"        run: |\n          {body}")
        assert provisions_a_browser(jobs(folded)["browse-live"])

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", '""'])
    def test_a_falsey_require_value_is_rejected(self, value: str) -> None:
        weakened = _SYNTHETIC.replace(_REQUIRE, f"      {browse_chrome.REQUIRE_ENV}: {value}")
        read = env_value(jobs(weakened)["browse-live"], browse_chrome.REQUIRE_ENV)
        assert browse_chrome._falsey(read), f"{value!r} read as {read!r}, which is not falsey"

    def test_a_deleted_require_line_reads_as_absent(self) -> None:
        without = _SYNTHETIC.replace(_REQUIRE + "\n", "")
        assert env_value(jobs(without)["browse-live"], browse_chrome.REQUIRE_ENV) is None

    def test_a_pytest_line_without_paths_names_no_modules(self) -> None:
        whole = _SYNTHETIC.replace(_PYTEST, "uv run pytest")
        assert modules_named(jobs(whole)["browse-live"]) == set()

    def test_a_non_pytest_step_does_not_count_as_running_a_module(self) -> None:
        echoed = _SYNTHETIC.replace(_PYTEST, "echo tests/test_browse_cdp_live.py")
        assert modules_named(jobs(echoed)["browse-live"]) == set()

    def test_the_leg_lookup_fails_when_no_job_runs_the_proof(self) -> None:
        elsewhere = _PYTEST.replace("test_browse_cdp_live", "test_browse_compress")
        with pytest.raises(AssertionError) as caught:
            the_leg(_SYNTHETIC.replace(_PYTEST, elsewhere))
        assert "no gate" in str(caught.value)

    @pytest.mark.parametrize(
        "source",
        [
            'import browse_chrome\nchrome = browse_chrome.chrome_or_skip("P")\n',
            'import browse_chrome\nbrowse_chrome.websockets_or_skip("P")\n',
            'import browse_chrome\nbrowse_chrome.missing("P", "no browser")\n',
            'from browse_chrome import chrome_or_skip\nchrome = chrome_or_skip("P")\n',
        ],
    )
    def test_a_real_gate_call_is_caught(self, source: str) -> None:
        assert calls_the_browser_gate(source), f"the detector missed a real gate:\n{source}"

    @pytest.mark.parametrize(
        "source",
        [
            # The shape of THIS file, which must not be dragged onto the browser leg.
            "import browse_chrome\nassert browse_chrome.REQUIRE_ENV\n",
            '"""A docstring naming chrome_or_skip and websockets_or_skip."""\n',
            'HELPERS = ["chrome_or_skip", "websockets_or_skip", "missing"]\n',
            "missing = {1, 2} - {1}\nassert not missing\n",
        ],
    )
    def test_a_mere_mention_is_not_a_gate(self, source: str) -> None:
        assert not calls_the_browser_gate(
            source
        ), f"the detector invented a gate from a mention:\n{source}"
