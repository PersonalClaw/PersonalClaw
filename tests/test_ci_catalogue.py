"""tools/ci/catalogue.py reads the catalogue of CI's jobs into typed jobs, and refuses a bad one.

Every catalogue here is built from inline text or under tmp_path, so this module reads nothing of
the tree; the real catalogue is held to ci.yml by the CI contract table's row "every catalogue
command is the workflow's".
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from tools.ci import catalogue
from tools.ci.__main__ import main

#: The smallest catalogue the rules accept: a merge tier holding the rails job, and the suite on
#: each platform as its security evidence.
_MINIMAL = """
[tier.merge]
jobs = ["rails", "python-suite", "python-suite-macos"]

[[job]]
id = "rails"
tiers = ["change", "merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
reuse = "tree"
timeout_min = 10
command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]

[[job]]
id = "python-suite"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 55
shards = 4
command = [{ argv = ["uv", "run", "pytest", "--shard", "{shard}/4"] }]

[[job]]
id = "python-suite-macos"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
platform = "macos-26"
timeout_min = 50
command = [{ argv = ["uv", "run", "pytest", "tests"] }]
"""

#: The merge tier's security evidence, the suite on each platform.
_SUITES = ("python-suite", "python-suite-macos")


def _refused(text: str) -> list[str]:
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load(text)
    return caught.value.problems


def _with(old: str, new: str) -> str:
    assert old in _MINIMAL, old
    return _MINIMAL.replace(old, new, 1)


def _in_job(job_id: str, old: str, new: str) -> str:
    """_MINIMAL with one edit inside the entry ``job_id``."""
    head, *entries = _MINIMAL.split("\n[[job]]\n")
    edited = [
        entry.replace(old, new, 1) if entry.startswith(f'id = "{job_id}"\n') else entry
        for entry in entries
    ]
    assert edited != entries, (job_id, old)
    return "\n[[job]]\n".join([head, *edited])


def test_a_minimal_catalogue_loads_into_typed_jobs() -> None:
    cat = catalogue.load(_MINIMAL)
    rails = cat.job("rails")
    assert isinstance(rails, catalogue.Job)
    assert rails.tiers == ("change", "merge")
    assert rails.reuse == catalogue.Reuse("tree")
    assert rails.command == (catalogue.Step(("uv", "run", "pytest", "tests/rails")),)
    assert cat.job("python-suite").shards == 4
    assert [j.id for j in cat.tier("merge")] == ["rails", *_SUITES]
    assert catalogue.validate(cat) == []


@pytest.mark.parametrize("name", catalogue.REQUIRED)
def test_each_required_field_removed_is_refused_by_name(name: str) -> None:
    head, rails, *rest = _MINIMAL.split("\n[[job]]\n")
    lines = rails.splitlines()
    kept = [line for line in lines if not line.startswith(f"{name} =")]
    assert len(kept) == len(lines) - 1, name
    text = "\n[[job]]\n".join([head, "\n".join(kept) + "\n", *rest])
    assert f"rails: missing {name}" in _refused(text)


@pytest.mark.parametrize("tier", ["batch", "nightly"])
def test_an_unknown_tier_is_refused(tier: str) -> None:
    problems = _refused(_with('tiers = ["change", "merge"]', f'tiers = ["change", "{tier}"]'))
    assert any(p.startswith(f"rails: tiers names '{tier}'") for p in problems), problems


def test_a_duplicate_id_is_refused() -> None:
    problems = _refused(_with('id = "python-suite"', 'id = "rails"'))
    assert "rails: the id is used by 2 entries" in problems


def test_a_misspelt_key_is_refused() -> None:
    problems = _refused(_with("timeout_min = 10", "timout_min = 10"))
    assert "rails: unknown key 'timout_min'" in problems
    assert "rails: missing timeout_min" in problems


def test_an_absent_reuse_reads_as_never() -> None:
    assert catalogue.load(_MINIMAL).job("python-suite").reuse == catalogue.NEVER
    assert catalogue.parse_reuse(None) == catalogue.NEVER
    assert catalogue.parse_reuse("never") == catalogue.NEVER


def test_a_ttl_parses_into_hours() -> None:
    assert catalogue.parse_reuse("ttl:24") == catalogue.Reuse("ttl", 24)
    assert str(catalogue.parse_reuse("ttl:24")) == "ttl:24"


@pytest.mark.parametrize("value", ["ttl:0", "ttl:x", "ttl:-1", "ttl:", "forever", "TREE", 24])
def test_another_reuse_value_is_refused(value: object) -> None:
    with pytest.raises(ValueError, match="never, tree or ttl:<hours>"):
        catalogue.parse_reuse(value)
    spelt = f'"{value}"' if isinstance(value, str) else str(value)
    problems = _refused(_with('reuse = "tree"', f"reuse = {spelt}"))
    assert any(p.startswith("rails: reuse is") for p in problems), problems


@pytest.mark.parametrize("reuse", ["tree", "ttl:24"])
def test_a_security_job_is_never_reused(reuse: str) -> None:
    problems = _refused(_with("timeout_min = 55", f'reuse = "{reuse}"\ntimeout_min = 55'))
    assert (
        f"python-suite: reuse is {reuse}, but a security job's evidence is never reused" in problems
    )


@pytest.mark.parametrize("reuse", ["tree", "ttl:24"])
def test_a_live_job_is_never_reused(reuse: str) -> None:
    problems = _refused(_with('reuse = "tree"', f'reuse = "{reuse}"\nlive = true'))
    assert f"rails: reuse is {reuse}, but a live job's evidence is never reused" in problems


def test_a_command_and_a_script_together_are_refused() -> None:
    problems = _refused(
        _with("timeout_min = 10", 'timeout_min = 10\nscript = "x.sh"\ncontainer = "img"')
    )
    assert "rails: has either a command or a script and container, not both" in problems


def test_neither_a_command_nor_a_script_is_refused() -> None:
    problems = _refused(_with('command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]', ""))
    assert "rails: has either a command or a script and container, not both" in problems


def test_a_script_without_its_container_is_refused() -> None:
    problems = _refused(
        _with('command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]', 'script = "x.sh"')
    )
    assert "rails: a script runs in a container, so it names both" in problems


def test_a_script_in_a_container_loads() -> None:
    text = _with(
        'command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]',
        'script = "deploy/docker/smoke.sh"\ncontainer = "personalclaw:test"',
    )
    job = catalogue.load(text).job("rails")
    assert (job.command, job.script, job.container) == (
        (),
        "deploy/docker/smoke.sh",
        "personalclaw:test",
    )


def test_a_merge_tier_without_the_rails_job_is_refused() -> None:
    problems = _refused(_with('tiers = ["change", "merge"]', 'tiers = ["change"]'))
    assert any("the merge tier lacks the rails job" in p for p in problems), problems


#: The rails in two halves, as ci.yml runs them, and the job that runs `rails check`.
_HALVED_RAILS = (
    'command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]',
    'shards = 2\ncommand = [{ argv = ["uv", "run", "pytest", "tests/rails", "--shard-list",'
    ' "tools/ci/rails-half-{shard}.txt"] }]',
)
_RAILS_CHECKS = """
[[job]]
id = "rails-checks"
tiers = ["change", "merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
reuse = "tree"
timeout_min = 10
command = [{ argv = ["uv", "run", "python", "-m", "tools.ci", "rails", "check"] }]
"""


def test_halved_rails_without_their_check_in_the_merge_tier_are_refused() -> None:
    """With the rails in halves, `rails check` is what holds every rail to one of them: a merge
    tier without the job that runs it could pass with a rail in neither half."""
    halved = _with(*_HALVED_RAILS)
    problems = _refused(halved)
    assert any(
        "lacks rails-checks, so a rail can be in neither half" in p for p in problems
    ), problems
    held = halved.replace('jobs = ["rails",', 'jobs = ["rails", "rails-checks",', 1) + _RAILS_CHECKS
    assert catalogue.load(held).job("rails").shards == 2
    off_merge = held.replace(
        'id = "rails-checks"\ntiers = ["change", "merge"]',
        'id = "rails-checks"\ntiers = ["change"]',
    ).replace('"rails", "rails-checks",', '"rails",', 1)
    assert any("lacks rails-checks" in p for p in _refused(off_merge))


@pytest.mark.parametrize("job_id", _SUITES)
def test_the_suite_marked_ordinary_is_refused(job_id: str) -> None:
    """The merge tier's security evidence is the suite's own run of its security-marked
    modules, on each platform, so neither run is ordinary (and so reusable)."""
    problems = _refused(_in_job(job_id, 'security = "security"', 'security = "ordinary"'))
    assert any(p.startswith(f"{job_id}: security is ordinary") for p in problems), problems


@pytest.mark.parametrize("job_id", _SUITES)
def test_the_suite_out_of_the_merge_tier_is_refused(job_id: str) -> None:
    problems = _refused(_in_job(job_id, 'tiers = ["merge"]', 'tiers = ["breadth"]'))
    assert any(f"the merge tier lacks {job_id}" in p for p in problems), problems


def test_another_security_job_does_not_stand_in_for_the_suite() -> None:
    """A security job of the merge tier that is not the suite leaves the suite's run required."""
    corpus = (
        '\n[[job]]\nid = "corpus"\ntiers = ["merge"]\ninputs = ["tests/security/"]\n'
        'external_inputs = []\ntoolchain = ["uv-sync"]\ncache = ["uv"]\nsecurity = "security"\n'
        'timeout_min = 25\ncommand = [{ argv = ["uv", "run", "pytest", "tests/security"] }]\n'
    )
    text = _in_job("python-suite", 'security = "security"', 'security = "ordinary"\nreuse = "tree"')
    problems = _refused(text + corpus)
    assert any(p.startswith("python-suite: security is ordinary") for p in problems), problems


def test_a_step_that_reads_the_environment_is_refused() -> None:
    problems = _refused(_with('"tests/rails"]', '"$RUNNER_TEMP/x"]'))
    assert any("reads the environment: use a placeholder" in p for p in problems), problems


def test_an_unknown_placeholder_is_refused() -> None:
    problems = _refused(_with('"tests/rails"]', '"{home}/x"]'))
    assert any("uses unknown placeholder {home}" in p for p in problems), problems


def test_a_sharded_job_names_its_shard_and_an_unsharded_one_does_not() -> None:
    problems = _refused(_with("shards = 4\n", ""))
    assert any(p.startswith("python-suite: shards is 1") for p in problems), problems


def test_a_step_sets_its_own_env_on_top_of_its_jobs() -> None:
    """A workflow step's own env, such as a flag that turns a skip into a failure, is the step's:
    without it the same argv proves less."""
    text = _with(
        '"tests/rails"] }',
        '"tests/rails"], env = { PERSONALCLAW_REQUIRE_NPM_TREE = "1" } }',
    )
    step = catalogue.load(text).job("rails").command[0]
    assert step.env == {"PERSONALCLAW_REQUIRE_NPM_TREE": "1"}
    assert step.render() == ".$ PERSONALCLAW_REQUIRE_NPM_TREE=1 uv run pytest tests/rails"
    assert catalogue.load(_MINIMAL).job("rails").command[0].env == {}


@pytest.mark.parametrize("env", ["{ X = 1 }", '"X=1"', "[]"])
def test_a_step_env_that_is_not_a_table_of_strings_is_refused(env: str) -> None:
    problems = _refused(_with('"tests/rails"] }', f'"tests/rails"], env = {env} }}'))
    assert "rails: command step 1 env is a table of string values" in problems


def test_a_pathspec_that_leaves_the_repository_is_refused() -> None:
    problems = _refused(_with('inputs = ["src/", "tests/"]', 'inputs = ["../elsewhere"]'))
    assert "python-suite: inputs pathspec '../elsewhere' leaves the repository" in problems


def test_an_unknown_external_input_is_refused() -> None:
    problems = _refused(_with('external_inputs = ["shard-history"]', 'external_inputs = ["web"]'))
    assert any(
        p.startswith("python-suite: external_inputs names 'web'") for p in problems
    ), problems


def test_an_install_exemption_needs_its_reason() -> None:
    problems = _refused(
        _with("timeout_min = 10", 'timeout_min = 10\ninstall_exemptions = [{ install = "pip x" }]')
    )
    assert "rails: install_exemptions entries each name an install and a reason" in problems


def test_text_that_is_not_toml_is_refused() -> None:
    assert _refused("[[job]\n")[0].startswith("tools/ci/catalogue.toml is not TOML")


def _catalogue_file(tmp_path: Path, text: str = _MINIMAL) -> str:
    path = tmp_path / "catalogue.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_list_prints_every_job(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", "--catalogue", _catalogue_file(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == ["rails", *_SUITES]
    assert "reuse tree" in lines[0] and "reuse never" in lines[1]


def test_explain_merge_says_what_it_proves_and_lists_its_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["explain", "merge", "--catalogue", _catalogue_file(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("merge: proves ")
    assert "It does not prove " in out
    assert "3 job(s)." in out
    assert ".$ uv run pytest tests/rails" in out
    assert "security security" in out


def test_explain_refuses_an_unknown_tier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["explain", "batch", "--catalogue", _catalogue_file(tmp_path)]) == 2
    assert "'batch' is not a tier" in capsys.readouterr().err


def test_list_refuses_a_bad_catalogue_naming_the_fault(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _catalogue_file(tmp_path, _with("timeout_min = 10", "timout_min = 10"))
    assert main(["list", "--catalogue", bad]) == 1
    assert "rails: unknown key 'timout_min'" in capsys.readouterr().err


# ── every refusal the reader names, one row per rule ─────────────────────────────────────────

_RAILS_STEP = '"tests/rails"] }'


def _step(extra: str) -> str:
    """_MINIMAL with ``extra`` added to the rails job's one step."""
    return _with(_RAILS_STEP, f'"tests/rails"], {extra} }}')


@pytest.mark.parametrize(
    "text, problem",
    [
        pytest.param(
            _in_job("python-suite-macos", "timeout_min = 50", "timeout_min = 50\nshards = 4"),
            "python-suite-macos: shards is 4, and a sharded job's steps name {shard}",
            id="a sharded job whose steps do not name its shard",
        ),
        pytest.param(
            _with('inputs = ["src/", "tests/"]', 'inputs = ["/etc"]'),
            "python-suite: inputs pathspec '/etc' leaves the repository",
            id="an absolute pathspec",
        ),
        pytest.param(
            _with(
                "timeout_min = 10",
                'timeout_min = 10\ninstall_exemptions = [{ install = "pip x", reason = " " }]',
            ),
            "rails: install_exemptions entries each name an install and a reason",
            id="an install exemption with a blank reason",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\ninstall_exemptions = 1"),
            "rails: install_exemptions is a list of tables",
            id="install exemptions that are not a list",
        ),
        pytest.param(
            _step('workdir = "../outside"'),
            "rails: command step 1 workdir is a path inside the repository",
            id="a workdir outside the repository",
        ),
        pytest.param(
            _step('workdir = "/tmp"'),
            "rails: command step 1 workdir is a path inside the repository",
            id="an absolute workdir",
        ),
        pytest.param(
            _step('cwd = "web"'),
            "rails: command step 1 has unknown key 'cwd'",
            id="an unknown step key",
        ),
        pytest.param(
            _with('command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]', "command = []"),
            "rails: command is a non-empty list of steps",
            id="an empty command",
        ),
        pytest.param(
            _with(
                'command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]',
                'command = ["uv run pytest"]',
            ),
            "rails: command step 1 is a table with argv and an optional workdir",
            id="a step that is not a table",
        ),
        pytest.param(
            _with('["uv", "run", "pytest", "tests/rails"]', '["uv", 1]'),
            "rails: command step 1 argv is a non-empty list of strings",
            id="an argument that is not a string",
        ),
        pytest.param(
            _with('["uv", "run", "pytest", "tests/rails"]', '["", "run"]'),
            "rails: command step 1 argv[0] is the program to run, so it is not empty",
            id="an empty program",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\nafter = ["nope"]'),
            "rails: after names 'nope', which is not another job",
            id="after naming no job",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\nafter = ["rails"]'),
            "rails: after names 'rails', which is not another job",
            id="after naming itself",
        ),
        pytest.param(
            "[settings]\nx = 1\n" + _MINIMAL,
            "tools/ci/catalogue.toml: unknown table 'settings'",
            id="an unknown top-level table",
        ),
        pytest.param(
            "x = 1\n",
            "tools/ci/catalogue.toml has no [[job]] entries",
            id="no entries",
        ),
        pytest.param(
            "job = []\n",
            "tools/ci/catalogue.toml has no [[job]] entries",
            id="an empty list of entries",
        ),
        pytest.param(
            "job = [1]\n",
            "job 1: is not a table",
            id="an entry that is not a table",
        ),
        pytest.param(
            _with('id = "rails"', 'id = "Rails"'),
            "job 1: id is lower-case letters, digits and dashes, starting a letter",
            id="an id outside the pattern",
        ),
        pytest.param(
            _with('tiers = ["change", "merge"]', "tiers = []"),
            "rails: tiers names at least one tier",
            id="no tiers",
        ),
        pytest.param(
            _with('inputs = ["src/", "tests/"]', "inputs = []"),
            "python-suite: inputs names at least one pathspec",
            id="no inputs",
        ),
        pytest.param(
            _with('tiers = ["change", "merge"]', 'tiers = ["change", "change", "merge"]'),
            "rails: tiers names a value twice",
            id="a value listed twice",
        ),
        pytest.param(
            _with('tiers = ["change", "merge"]', 'tiers = ["change", ""]'),
            "rails: tiers is a list of non-empty strings",
            id="an empty string in a list",
        ),
        pytest.param(
            _with('inputs = [":(glob)**"]', 'inputs = [":(nope)src"]'),
            "rails: inputs pathspec ':(nope)src' uses unknown magic nope",
            id="unknown pathspec magic",
        ),
        pytest.param(
            _with('inputs = [":(glob)**"]', 'inputs = [":(glob**"]'),
            "rails: inputs pathspec ':(glob**' opens its magic with :( and never closes it",
            id="pathspec magic never closed",
        ),
        pytest.param(
            _with('toolchain = ["uv-sync"]', 'toolchain = ["make"]'),
            "rails: toolchain names 'make', which is not one of",
            id="an unknown toolchain",
        ),
        pytest.param(
            _with('cache = ["uv"]', 'cache = ["pip"]'),
            "rails: cache names 'pip', which is not one of",
            id="an unknown cache",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 0"),
            "rails: timeout_min is a positive whole number",
            id="a cap of 0",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = -5"),
            "rails: timeout_min is a positive whole number",
            id="a negative cap",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = true"),
            "rails: timeout_min is a positive whole number",
            id="a cap that is a boolean",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = "10"'),
            "rails: timeout_min is a positive whole number",
            id="a cap that is a string",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\npython = "3"'),
            "rails: python is a version such as 3.12.15, or image for the runner's own",
            id="a python that is no version",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\npython = "latest"'),
            "rails: python is a version such as 3.12.15, or image for the runner's own",
            id="a python that is a word",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\nenv = { X = 1 }"),
            "rails: env is a table of string values",
            id="a job env value that is not a string",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\nenv = "X=1"'),
            "rails: env is a table of string values",
            id="a job env that is not a table",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\nworkers = 0"),
            "rails: workers is 0, and no step passes pytest -n nor does workers_env name",
            id="no workers, and no step passes -n0",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\nworkers = -1"),
            "rails: workers is a positive whole number",
            id="a negative worker count",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\nworkers = false"),
            "rails: workers is a positive whole number",
            id="workers that is a boolean",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\nworkers = "many"'),
            "rails: workers is a positive whole number",
            id="workers that is a word",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\nmem_gb = 0"),
            "rails: mem_gb is a positive number",
            id="no memory",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\nmem_gb = "8"'),
            "rails: mem_gb is a positive number",
            id="memory that is a string",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\nmem_gb = true"),
            "rails: mem_gb is a positive number",
            id="memory that is a boolean",
        ),
        pytest.param(
            _with("timeout_min = 10", "timeout_min = 10\nshards = 0"),
            "rails: shards is a positive whole number",
            id="no shards",
        ),
        pytest.param(
            _with("timeout_min = 10", 'timeout_min = 10\nplatform = ""'),
            "rails: platform is a non-empty string",
            id="an empty platform",
        ),
    ],
)
def test_each_rule_refuses_its_violation_by_name(text: str, problem: str) -> None:
    problems = _refused(text)
    assert any(p.startswith(problem) for p in problems), problems


# ── decision 8 cannot be skirted by a malformed posture ──────────────────────────────────────


@pytest.mark.parametrize("value", ['"Security"', '"SECURITY"', "true", "1", '""'])
def test_a_security_value_outside_the_two_postures_is_refused(value: str) -> None:
    """rails is reused by tree, so a posture the reader let through as not ``security`` would
    let a security job's evidence be reused."""
    problems = _refused(_with('security = "ordinary"', f"security = {value}"))
    assert "rails: security is one of ordinary, security" in problems


@pytest.mark.parametrize("value", ["1", '"true"', '"yes"', "0"])
def test_a_live_value_that_is_not_a_boolean_is_refused(value: str) -> None:
    """rails is reused by tree: a ``live`` the reader read as false would let it be reused."""
    problems = _refused(_with('reuse = "tree"', f'reuse = "tree"\nlive = {value}'))
    assert "rails: live is true or false" in problems


@pytest.mark.parametrize("flag", ["needs_node", "needs_browser", "full_history"])
def test_each_flag_that_is_not_a_boolean_is_refused(flag: str) -> None:
    problems = _refused(_with("timeout_min = 10", f'timeout_min = 10\n{flag} = "true"'))
    assert f"rails: {flag} is true or false" in problems


def test_a_job_that_reads_the_advisory_feed_is_live() -> None:
    """What the feed says is what it said at the time of the run, so the job is live and its
    evidence is never reused; the posture is the reader's to hold, not only the data's."""
    text = _with("external_inputs = []", 'external_inputs = ["advisory-feed"]')
    problems = _refused(text)
    assert any(p.startswith("rails: reads advisory-feed") for p in problems), problems
    live = _with(
        'external_inputs = []\ntoolchain = ["uv-sync"]',
        'external_inputs = ["advisory-feed"]\ntoolchain = ["uv-sync"]\nlive = true',
    )
    assert "rails: reuse is tree, but a live job's evidence is never reused" in _refused(live)
    kept = live.replace('reuse = "tree"\n', "", 1)
    assert catalogue.load(kept).job("rails").live


@pytest.mark.parametrize(
    "job_id, platform, family",
    [("python-suite", "macos-26", "ubuntu-"), ("python-suite-macos", "ubuntu-24.04", "macos-")],
)
def test_each_suite_runs_on_its_own_platform(job_id: str, platform: str, family: str) -> None:
    """The merge tier's security evidence is the suite's run on each platform, so a suite moved
    to the other platform leaves one platform's run missing."""
    if job_id == "python-suite":
        text = _in_job(job_id, "timeout_min = 55", f'timeout_min = 55\nplatform = "{platform}"')
    else:
        text = _in_job(job_id, 'platform = "macos-26"', f'platform = "{platform}"')
    problems = _refused(text)
    assert any(
        p.startswith(f"{job_id}: platform is {platform}, but") and f"a {family}* runner" in p
        for p in problems
    ), problems


@pytest.mark.parametrize(
    "toolchain, flags, problem",
    [
        (["uv-sync", "node"], "", "rails: needs_node is false, and its toolchain installs node"),
        (["uv-sync"], "needs_node = true", "rails: needs_node is true, and its toolchain"),
        (
            ["uv-sync", "node", "chromium"],
            "needs_node = true",
            "rails: needs_browser is false, and its toolchain installs chromium",
        ),
        (["uv-sync"], "needs_browser = true", "rails: needs_browser is true, and its toolchain"),
    ],
)
def test_the_node_and_browser_flags_are_the_toolchains(
    toolchain: list[str], flags: str, problem: str
) -> None:
    listed = ", ".join(f'"{t}"' for t in toolchain)
    text = _with('toolchain = ["uv-sync"]', f"toolchain = [{listed}]\n{flags}")
    problems = _refused(text)
    assert any(p.startswith(problem) for p in problems), problems


# ── pathspecs, in every form git reads ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec", [":!../x", ":^../x", ":/../x", ":!src/../../x", ":!:../x", ":(exclude)../x"]
)
def test_a_pathspec_that_leaves_the_repository_in_any_magic_is_refused(spec: str) -> None:
    problems = _refused(_with('inputs = ["src/", "tests/"]', f'inputs = ["src/", "{spec}"]'))
    assert f"python-suite: inputs pathspec '{spec}' leaves the repository" in problems


@pytest.mark.parametrize(
    "spec", [":!docs/", ":^docs/", ":(exclude)docs/", ":/src", ":/", ":!:docs"]
)
def test_a_pathspec_with_magic_inside_the_repository_loads(spec: str) -> None:
    text = _with('inputs = ["src/", "tests/"]', f'inputs = ["src/", "{spec}"]')
    assert catalogue.load(text).job("python-suite").inputs == ("src/", spec)


# ── a step's env and workdir read nothing around the catalogue ───────────────────────────────


@pytest.mark.parametrize(
    "extra, problem",
    [
        ('env = { X = "$HOME" }', "rails: command step 1 env X is '$HOME', which reads the"),
        ('env = { X = "${{ github.sha }}" }', "rails: command step 1 env X is '${{ github.sha }}'"),
        ('env = { X = "{home}/x" }', "rails: command step 1 env X uses unknown placeholder {home}"),
        ('env = { "A=B" = "1" }', "rails: command step 1 env name 'A=B' is not a variable name"),
        ('env = { "" = "1" }', "rails: command step 1 env name '' is not a variable name"),
        ('env = { lower = "1" }', "rails: command step 1 env name 'lower' is not a variable name"),
        ('workdir = "$HOME"', "rails: command step 1 workdir is a path inside the repository"),
        ('workdir = "{runner_temp}"', "rails: command step 1 workdir is a path inside the"),
    ],
)
def test_a_step_env_or_workdir_that_reads_the_environment_is_refused(
    extra: str, problem: str
) -> None:
    problems = _refused(_step(extra))
    assert any(p.startswith(problem) for p in problems), problems


@pytest.mark.parametrize(
    "env, problem",
    [
        ('{ X = "$HOME" }', "rails: env X is '$HOME', which reads the environment"),
        ('{ X = "{home}" }', "rails: env X uses unknown placeholder {home}"),
        ('{ "A=B" = "1" }', "rails: env name 'A=B' is not a variable name"),
    ],
)
def test_a_job_env_that_reads_the_environment_is_refused(env: str, problem: str) -> None:
    problems = _refused(_with("timeout_min = 10", f"timeout_min = 10\nenv = {env}"))
    assert any(p.startswith(problem) for p in problems), problems


def test_a_placeholder_in_a_step_env_value_is_the_steps() -> None:
    text = _step('env = { OUT = "{runner_temp}/out.json" }')
    step = catalogue.load(text).job("rails").command[0]
    assert step.placeholders() == {"runner_temp"}


def test_a_shard_named_only_in_a_step_env_still_counts() -> None:
    text = _with('"--shard", "{shard}/4"] }', '"--shard", "4"], env = { SHARD = "{shard}" } }')
    assert catalogue.load(text).job("python-suite").shards == 4


# ── a loaded catalogue is a value ────────────────────────────────────────────────────────────


def test_a_loaded_catalogue_cannot_be_changed_in_place_and_hashes() -> None:
    text = _step('env = { PERSONALCLAW_REQUIRE_NPM_TREE = "1" }').replace(
        "timeout_min = 10", 'timeout_min = 10\nenv = { PERSONALCLAW_SKIP_APP_BACKENDS = "1" }', 1
    )
    cat = catalogue.load(text)
    rails = cat.job("rails")
    with pytest.raises(TypeError):
        rails.env["INJECTED"] = "1"  # type: ignore[index]
    with pytest.raises(TypeError):
        rails.command[0].env["PERSONALCLAW_REQUIRE_NPM_TREE"] = "0"  # type: ignore[index]
    assert cat.job("rails").env == {"PERSONALCLAW_SKIP_APP_BACKENDS": "1"}
    assert rails.command[0].env == {"PERSONALCLAW_REQUIRE_NPM_TREE": "1"}
    assert hash(rails) == hash(catalogue.load(text).job("rails"))
    assert len({*cat.jobs}) == 3


def test_an_unknown_job_id_is_named() -> None:
    with pytest.raises(KeyError, match="'nope' is no job of the catalogue"):
        catalogue.load(_MINIMAL).job("nope")


# ── the file, read ───────────────────────────────────────────────────────────────────────────


def test_a_catalogue_that_is_not_utf8_is_refused_by_name(tmp_path: Path) -> None:
    (tmp_path / "tools" / "ci").mkdir(parents=True)
    (tmp_path / catalogue.CATALOGUE).write_bytes(b"\xff\xfe[[job]]\n")
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.read(tmp_path)
    assert caught.value.problems == ["tools/ci/catalogue.toml is not UTF-8"]


def test_list_refuses_a_catalogue_that_is_not_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "latin.toml"
    path.write_bytes(b"\xff\xfe[[job]]\n")
    assert main(["list", "--catalogue", str(path)]) == 1
    assert capsys.readouterr().err.strip() == f"{path} is not UTF-8"


def test_explain_refuses_a_bad_catalogue_naming_the_fault(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _catalogue_file(tmp_path, _with("timeout_min = 10", "timout_min = 10"))
    assert main(["explain", "merge", "--catalogue", bad]) == 1
    captured = capsys.readouterr()
    assert "rails: unknown key 'timout_min'" in captured.err
    assert captured.out == ""


# ── a pytest that only collects runs no test ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "pytest --collect-only tests",
        "pytest --co tests",
        "uv run pytest --collect-only -qq -n0 tests",
        "uv run --frozen pytest --co tests",
        "uv run python -m pytest --co tests",
        "python3.13 -m pytest -k smoke --co tests",
        "PYTHONPATH=tests uv run pytest --co tests",
    ],
)
def test_pytests_own_collect_only_spellings_only_collect(command: str) -> None:
    assert catalogue.collect_only(command.split())


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("pytest tests", id="no flag"),
        pytest.param("pytest --collect tests", id="a flag pytest does not spell so"),
        pytest.param("pytest --collect-only=yes tests", id="a flag with a value"),
        pytest.param("pytest -k --co tests", id="the value of -k"),
        pytest.param("pytest -m --collect-only tests", id="the value of -m"),
        pytest.param("echo pytest --co", id="another program's argument"),
        pytest.param("sh -c 'pytest --co tests'", id="inside a quoted string"),
        pytest.param("uv run ruff --co", id="another tool's argument"),
    ],
)
def test_anything_else_is_not_collect_only(command: str) -> None:
    assert not catalogue.collect_only(shlex.split(command))


def test_no_workers_loads_when_its_step_passes_pytest_n0() -> None:
    """``workers = 0`` is pytest's ``-n0``: one process, as a whole-suite collection asks."""
    text = _with('"tests/rails"]', '"-n0", "tests/rails"]').replace(
        "timeout_min = 10", "timeout_min = 10\nworkers = 0", 1
    )
    assert catalogue.load(text).job("rails").workers == 0


def test_a_security_job_whose_every_pytest_step_only_collects_is_refused() -> None:
    """A collect-only leg runs no test, so it is never any job's security evidence."""
    text = _in_job("python-suite-macos", '"pytest", "tests"]', '"pytest", "--co", "tests"]')
    problems = _refused(text)
    assert any(
        p.startswith("python-suite-macos: security posture, and every pytest step only collects")
        for p in problems
    ), problems
    hidden = _in_job("python-suite-macos", '"pytest", "tests"]', '"pytest", "-k", "--co", "tests"]')
    assert catalogue.load(hidden).job("python-suite-macos").security == "security"
