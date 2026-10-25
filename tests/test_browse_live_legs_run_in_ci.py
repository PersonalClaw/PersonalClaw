"""The behavioural clauses must be proven ON THE MERGE GATE, not on whoever's laptop.

``PERSONALCLAW_REQUIRE_BROWSE_PROOF`` (``tests/browse_chrome.py``) already turns a missing
browser from a skip into a failure. That lever protects nothing on its own: **nothing in the
repo set it**, and the ``test`` job installs no browser, so on the machine that gates every
merge the behavioural layer skipped. Measured on a browser-less tree: **20 skipped**, spread
over ``test_browse_safety_script.py`` (10), ``test_browse_cdp_live.py`` (9) and — the part that
made it invisible — ``test_browse_behavioural_proof_is_reachable.py``'s own load-bearing case
(1). A skipped surface reads exactly like a pass, so the green check said "the safety script
blocks ``fetch()``" while having never opened a browser.

The catalogue's ``browse-live`` job closes that, as the tier workflows run it through ``run-job``:
it installs
a Chromium and sets the require-env, so a browser that is missing, renamed by a Playwright bump,
or unlaunchable is a RED there instead of twenty quiet skips. This file is the rail over that
WIRING, because the wiring is now the load-bearing part — delete the env line, or drop a module
from the pytest invocation, and the proof silently stops running again with nothing to say so.

Deliberately derived, not pinned by name:

* **The leg is found by what it does**, not by its job id — "the job that runs pytest on the
  browse-proof modules". A rename stays green; deleting the job does not.
* **The module list comes from the source.** Every ``tests/test_*.py`` that imports
  ``browse_chrome`` can skip on a missing browser, so every one of them must be named on the
  leg. A new ``test_browse_..._live.py`` that nobody adds to the leg would otherwise skip
  forever, which is the one-sided-inventory failure this repo keeps meeting.
* **Every path the leg names must exist.** A typo'd or renamed path is the ``[0 items]`` trap:
  pytest can report a clean exit having collected nothing, and the count is the only tell. Here
  it is a red at lint-speed, before CI.

Read from the catalogue (``tests/ci_catalogue_steps.py``), whose steps are argvs, so no comment
or folded block can stand in for a step. Every helper takes the catalogue or a job as an argument
so the tests at the bottom can drive the SAME functions with a synthetic catalogue and watch each
answer change.
"""

from __future__ import annotations

import ast
import pathlib
import unicodedata

import browse_chrome
import pytest
from browse_live_legs_run_in_ci_shared import _ROOT
from ci_catalogue_steps import read

from tools.ci import catalogue
from tools.ci.catalogue import Catalogue, Env, Job, Step

_TESTS = _ROOT / "tests"

#: The job id as it stands today. Used only in failure messages — the assertions locate the leg
#: by behaviour, so renaming the job is not a regression and must not read as one.
EXPECTED_LEG = "browse-live"


# ── the parser ────────────────────────────────────────────────────────────────


def env_value(job: Job, key: str) -> str | None:
    """The value of ``key`` in the job's env or in any of its steps'.

    Either on purpose: whether the require-env sits on the job or on the one step is a style
    choice, and a rail that cared would break on a harmless move.
    """
    if key in job.env:
        return job.env[key]
    return next((step.env[key] for step in job.command if key in step.env), None)


def provisions_a_browser(job: Job) -> bool:
    """Whether whatever runs the job puts a Chromium on the runner before its steps: the
    ``chromium`` install in its toolchain, which run-job runs as ``npx playwright install
    chromium`` (``tools/ci/jobs.py``)."""
    return "chromium" in job.toolchain


def modules_named(job: Job) -> set[str]:
    """Repo-relative test paths handed to pytest by a job's steps."""
    named: set[str] = set()
    for step in job.command:
        args = catalogue.pytest_args(step.argv)
        named |= {w for w in args or () if w.startswith("tests/") and w.endswith(".py")}
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

    A call counts only when it resolves to ``browse_chrome``: an attribute of a name the module
    is imported as, or a name imported from it. ``missing`` is a common name, and matching it on
    any receiver put ``grants.missing(trigger)`` on the browser leg.
    """
    tree = ast.parse(source)
    module_names: set[str] = set()
    gate_names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == browse_chrome.__name__:
                    module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == browse_chrome.__name__:
            for alias in node.names:
                if alias.name in _GATE_CALLS:
                    gate_names[alias.asname or alias.name] = alias.name
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _GATE_CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id in module_names
        ):
            hits.append(f"{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id in gate_names:
            hits.append(f"{gate_names[func.id]}() at line {node.lineno}")
    return hits


def may_call_the_browser_gate(source: str) -> bool:
    """Whether *source* names ``browse_chrome`` at all, read as Python reads an identifier.

    :func:`calls_the_browser_gate` finds a call only through a name the module imported from
    ``browse_chrome``, and an import names the module. So a module whose text never names it calls
    no gate, and is not parsed: the walk over every test module parses the few that do. The text
    is read in its NFKC form because the parser reads identifiers that way, so an import spelled
    in compatibility characters still names the module."""
    return browse_chrome.__name__ in unicodedata.normalize("NFKC", source)


def browse_proof_modules() -> set[str]:
    """Test modules that can skip on a missing browser — derived, never listed.

    Conservative by construction: a module that only drives the helpers synthetically (inside a
    ``pytest.raises``) is still named on the leg. That costs a second of runtime and removes a
    judgement call from a rail whose whole job is to not need one.
    """
    found: set[str] = set()
    for path in sorted(_TESTS.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if may_call_the_browser_gate(source) and calls_the_browser_gate(source):
            found.add(f"tests/{path.name}")
    return found


def the_leg(cat: Catalogue) -> Job:
    """The job that runs the browse proof, located by behaviour."""
    wanted = browse_proof_modules()
    matches = [job for job in cat.jobs if modules_named(job) & wanted]
    assert matches, (
        "no catalogue job runs pytest on any browse-proof module "
        f"({sorted(wanted)}), so the behavioural clauses are proven on no gate — their "
        f"green is 20 skips. The job that owned this was `{EXPECTED_LEG}`."
    )
    assert len(matches) == 1, (
        f"more than one job runs the browse proof: {[job.id for job in matches]}. Two legs "
        "means two places to keep the require-env and the browser install in step, and the "
        "cheapest way to satisfy both is to weaken one."
    )
    return matches[0]


# ── the rails ─────────────────────────────────────────────────────────────────


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
    leg = the_leg(read())
    unrun = browse_proof_modules() - modules_named(leg)
    assert not unrun, (
        f"these modules can skip on a missing browser but job `{leg.id}` never names them: "
        f"{sorted(unrun)}. Add them to that job's pytest invocation — on the merge gate their "
        "skip is indistinguishable from a pass."
    )


def test_the_leg_actually_puts_a_browser_on_the_runner() -> None:
    """The require-env would turn the whole leg red without this — loud, but not a proof."""
    leg = the_leg(read())
    assert provisions_a_browser(leg), (
        f"job `{leg.id}` runs the browse proof but installs no Chromium, so every "
        "behavioural case either skips (no proof) or fails (no signal). Its install step is "
        "the `chromium` install in its toolchain."
    )


def test_the_leg_demands_the_proof_actually_ran() -> None:
    """The whole point: on this leg, "no browser" must be a FAILURE, not a skip.

    Truthiness is judged by ``browse_chrome``'s own falsey rule rather than a second copy of the
    list. A copy here could drift from the one that decides, and then this rail would certify a
    value the code reads as "off".
    """
    leg = the_leg(read())
    value = env_value(leg, browse_chrome.REQUIRE_ENV)
    assert value is not None, (
        f"job `{leg.id}` runs the browse proof without setting {browse_chrome.REQUIRE_ENV}, so a "
        "missing or renamed browser skips 20 cases and the job goes GREEN having proven nothing."
    )
    assert not browse_chrome._falsey(value), (
        f"{browse_chrome.REQUIRE_ENV} is set to {value!r} on job `{leg.id}`, which browse_chrome "
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

    A renamed module leaves a path on the leg that resolves to nothing. Under xdist that can
    collect zero items and still exit clean; the collected COUNT is the only tell, and nobody
    reads it on a green check.
    """
    leg = the_leg(read())
    absent = sorted(path for path in modules_named(leg) if not (_ROOT / path).is_file())
    assert not absent, f"job `{leg.id}` names test paths that do not exist: {absent}"


# ── the detectors, both directions ────────────────────────────────────────────

_PYTEST = Step(("uv", "run", "pytest", "tests/test_browse_cdp_live.py", "-n0"))
_REQUIRE = {browse_chrome.REQUIRE_ENV: "1"}


def _job(job_id: str, *steps: Step, toolchain=("uv-sync", "chromium"), env=None) -> Job:
    return Job(
        id=job_id,
        tiers=("merge",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=tuple(toolchain),
        cache=(),
        security="security",
        timeout_min=10,
        command=steps,
        env=Env(_REQUIRE if env is None else env),
    )


def _synthetic(leg: Job | None = None) -> Catalogue:
    """A lint job, the browser leg (or *leg*) and a rails job."""
    lint = _job("lint", Step(("uv", "run", "black", "--check", "src")), toolchain=(), env={})
    rails = _job(
        "rails",
        Step(("uv", "run", "pytest", "tests/rails/test_provider_boundary_residue.py", "-n0")),
        toolchain=("uv-sync",),
        env={},
    )
    return Catalogue(jobs=(lint, leg or _job("browse-live", _PYTEST), rails))


class TestTheDetectors:
    """Each helper must be able to answer NO. A predicate that only ever says yes is a rail
    that matches nothing, and this whole file exists because of one of those."""

    def test_the_leg_is_found_by_what_it_runs(self) -> None:
        assert the_leg(_synthetic()).id == "browse-live"
        renamed = _job("browser-proof", _PYTEST)
        assert the_leg(_synthetic(renamed)).id == "browser-proof", "a rename stays green"

    def test_a_leg_with_no_browser_install_is_caught(self) -> None:
        assert provisions_a_browser(_job("browse-live", _PYTEST))
        assert not provisions_a_browser(_job("browse-live", _PYTEST, toolchain=("uv-sync",)))

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_a_falsey_require_value_is_rejected(self, value: str) -> None:
        weakened = _job("browse-live", _PYTEST, env={browse_chrome.REQUIRE_ENV: value})
        read_back = env_value(weakened, browse_chrome.REQUIRE_ENV)
        assert browse_chrome._falsey(read_back), f"{value!r} read as {read_back!r}, not falsey"

    def test_the_require_value_counts_on_the_step_as_on_the_job(self) -> None:
        on_step = Step(_PYTEST.argv, env=Env(_REQUIRE))
        assert env_value(_job("browse-live", on_step, env={}), browse_chrome.REQUIRE_ENV) == "1"

    def test_a_deleted_require_value_reads_as_absent(self) -> None:
        without = _job("browse-live", _PYTEST, env={})
        assert env_value(without, browse_chrome.REQUIRE_ENV) is None

    def test_a_pytest_step_without_paths_names_no_modules(self) -> None:
        whole = _job("browse-live", Step(("uv", "run", "pytest")))
        assert modules_named(whole) == set()

    def test_a_non_pytest_step_does_not_count_as_running_a_module(self) -> None:
        echoed = _job("browse-live", Step(("echo", "tests/test_browse_cdp_live.py")))
        assert modules_named(echoed) == set()

    def test_the_leg_lookup_fails_when_no_job_runs_the_proof(self) -> None:
        elsewhere = Step(("uv", "run", "pytest", "tests/test_browse_compress.py", "-n0"))
        with pytest.raises(AssertionError) as caught:
            the_leg(_synthetic(_job("browse-live", elsewhere)))
        assert "no gate" in str(caught.value)

    @pytest.mark.parametrize(
        "source",
        [
            'import browse_chrome\nchrome = browse_chrome.chrome_or_skip("P")\n',
            'import browse_chrome\nbrowse_chrome.websockets_or_skip("P")\n',
            'import browse_chrome\nbrowse_chrome.missing("P", "no browser")\n',
            'from browse_chrome import chrome_or_skip\nchrome = chrome_or_skip("P")\n',
            'import browse_chrome as bc\nbc.chrome_or_skip("P")\n',
            'from browse_chrome import missing as gone\ngone("P", "no browser")\n',
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
            # Another module's function that shares a gate's name.
            "from personalclaw.triggers import grants\nassert grants.missing(row) == []\n",
            "def missing():\n    return []\n\nmissing()\n",
        ],
    )
    def test_a_mere_mention_is_not_a_gate(self, source: str) -> None:
        assert not calls_the_browser_gate(
            source
        ), f"the detector invented a gate from a mention:\n{source}"


# ── the walk reads only the modules that can call the gate ────────────────────
#
# A module calls a gate only through a name it imported from ``browse_chrome``, so a module whose
# text never names ``browse_chrome`` calls none, and the walk does not parse it. Python reads an
# identifier as its NFKC form, so the text is read that way too: an import spelled in compatibility
# characters is still the module's name.

_GATED = "import browse_chrome as bc\n\n\ndef test_it():\n    bc.chrome_or_skip()\n"


def test_the_walk_parses_only_the_modules_that_name_the_browser_module(monkeypatch) -> None:
    parsed: list[str] = []
    parse = ast.parse

    def counting(source, *args, **kwargs):
        parsed.append(source)
        return parse(source, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting)
    modules = browse_proof_modules()
    naming = [
        path
        for path in sorted(_TESTS.glob("test_*.py"))
        if "browse_chrome" in unicodedata.normalize("NFKC", path.read_text(encoding="utf-8"))
    ]
    assert modules and len(parsed) == len(
        naming
    ), f"{len(parsed)} modules parsed for {len(naming)} that name browse_chrome"


def test_a_module_that_never_names_the_browser_module_is_not_parsed() -> None:
    assert not may_call_the_browser_gate("def test_it(:\n    chrome_or_skip()\n")


def test_a_module_that_imports_the_browser_module_is_read() -> None:
    assert may_call_the_browser_gate(_GATED)
    assert calls_the_browser_gate(_GATED) == ["chrome_or_skip() at line 5"]


def test_an_import_spelled_in_compatibility_characters_is_read() -> None:
    # A fullwidth "b" (U+FF42): the parser reads the identifier as browse_chrome, so must the walk.
    spelled = _GATED.replace("import browse_chrome", "import \uff42rowse_chrome")
    assert "browse_chrome" not in spelled
    assert calls_the_browser_gate(spelled) == ["chrome_or_skip() at line 5"]
    assert may_call_the_browser_gate(spelled)
