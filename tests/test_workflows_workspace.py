"""Tests for the workspace block, folder contracts and env grants (WORK-CONTAINERS §4, S49).

Three properties, each failing in a chosen direction.

**An ungranted secret is ABSENT, not empty.** An empty string reads to a child as "this
credential is
configured and blank", which produces an authentication failure instead of a missing-configuration
error — and the first is far harder to diagnose.

**A reserved env var is REJECTED, not overridden.** Letting a run set `HOME` or `PATH` lets it
relocate every config file, credential store and binary the rest of the system resolves
through them,
including the machinery enforcing every other rule here.

**A folder contract problem is a WARNING, never fatal.** This reads a format that will grow, and a
strict reader over an evolving format discards the data it was meant to protect — the
23-of-25-dropped-memories bug class.
"""

import pytest

from personalclaw.workflows.workspace import (
    RESERVED_ENV_PREFIXES,
    RESERVED_ENV_VARS,
    SETUP_MARKER_DIR,
    TTL_STAGING_DAYS,
    FolderContract,
    Lifecycle,
    Mode,
    WorkspaceSpec,
    is_reserved_env,
    looks_secret,
    may_write,
    parse_env,
    parse_folder_contract,
    parse_workspace,
    plan_provisioning,
    presence_flags,
    setup_marker,
    spawn_env,
    validate_frontmatter,
)


def codes(issues) -> set[str]:
    return {i.code for i in issues}


# ── the mode is a declaration, never a guess ──


def test_the_default_mode_is_SCRATCH():
    """Being wrong about isolation should cost a copy, not the original. `in_place` is the mode in
    which a destructive step runs against real state, so it is never a default."""
    spec, issues = parse_workspace({})
    assert spec.mode is Mode.SCRATCH
    assert issues == []


def test_an_unknown_mode_is_FATAL_not_defaulted():
    """Defaulting would silently run in a mode nobody chose, and the modes differ in exactly the way
    that matters."""
    spec, issues = parse_workspace({"mode": "yolo"})
    assert "unknown_mode" in codes(issues)
    assert any(i.fatal for i in issues)


def test_in_place_is_not_isolated():
    """The board's suspend/resume decision (S46) keys off this: an isolated substrate can survive a
    restart, an in-place one cannot."""
    assert parse_workspace({"mode": "in_place"})[0].isolated is False


@pytest.mark.parametrize("mode", ["scratch", "worktree", "container"])
def test_the_isolating_modes_report_isolated(mode):
    assert parse_workspace({"mode": mode})[0].isolated is True


def test_teardown_on_an_in_place_workspace_is_WARNED():
    """A cleanup command there deletes real work, not scratch state. A warning rather than
    a refusal:
    the author may genuinely want to stop a service they started in place."""
    _spec, issues = parse_workspace({"mode": "in_place", "teardown": "rm -rf build"})
    assert "in_place_teardown" in codes(issues)
    assert not any(i.fatal for i in issues)


def test_a_non_object_workspace_block_is_refused():
    _spec, issues = parse_workspace("worktree")
    assert any(i.fatal for i in issues)


def test_no_workspace_block_yields_the_default_spec():
    spec, issues = parse_workspace(None)
    assert spec.mode is Mode.SCRATCH
    assert issues == []


# ── preserve patterns ──


def test_preserve_patterns_survive_parsing():
    """The adoption-critical detail: a worktree with no `.env` is a worktree where every
    build fails,
    and a user whose first isolated run cannot install dependencies concludes isolation is broken.
    """
    spec, _ = parse_workspace({"preserve_patterns": [".env", "*.local.json"]})
    assert spec.preserve_patterns == [".env", "*.local.json"]


@pytest.mark.parametrize("pattern", ["**", "**/*", "*", ".", "/"])
def test_a_greedy_preserve_pattern_is_REFUSED(pattern):
    """The point of isolation is that the run works on a copy of what it NEEDS. A pattern
    that copies
    the whole tree defeats the isolation it is being copied into."""
    _spec, issues = parse_workspace({"preserve_patterns": [pattern]})
    assert "greedy_preserve_pattern" in codes(issues)
    assert any(i.fatal for i in issues)


# ── reserved env vars ──


@pytest.mark.parametrize("name", sorted(RESERVED_ENV_VARS))
def test_a_reserved_var_cannot_be_overridden(name):
    """Redirecting `HOME` or `PATH` relocates every config file, credential store and binary the
    system resolves through them — including what enforces the other rules here."""
    env, issues = parse_env({name: "/tmp/evil"})
    assert env == {}
    assert "reserved_env" in codes(issues)
    assert any(i.fatal for i in issues)


@pytest.mark.parametrize("prefix", RESERVED_ENV_PREFIXES)
def test_a_reserved_PREFIX_is_rejected(prefix):
    """`XDG_CONFIG_HOME` and every sibling relocate config resolution; a `PERSONALCLAW_` override
    would let a run repoint its own home."""
    assert is_reserved_env(f"{prefix}ANYTHING") is True


def test_reserved_matching_is_case_insensitive():
    assert is_reserved_env("home") is True
    assert is_reserved_env("Path") is True


def test_an_ordinary_var_is_allowed():
    env, issues = parse_env({"NODE_ENV": "test"})
    assert env == {"NODE_ENV": "test"}
    assert issues == []


def test_an_invalid_env_NAME_is_rejected_without_being_fatal():
    """A malformed name cannot be exported at all, so it is worth saying — but it is a typo, not a
    security problem, and refusing the whole run over one would be disproportionate."""
    env, issues = parse_env({"not-a-valid-name": "x", "GOOD": "y"})
    assert env == {"GOOD": "y"}
    assert "invalid_env_name" in codes(issues)
    assert not any(i.fatal for i in issues)


def test_a_non_object_env_section_is_refused():
    _env, issues = parse_env(["A=1"])
    assert any(i.fatal for i in issues)


# ── the secret filter ──


def test_an_ungranted_secret_is_ABSENT_not_empty():
    """An empty string reads to a child as "configured and blank", producing an authentication error
    instead of a missing-configuration one — much harder to diagnose."""
    spec, _ = parse_workspace({"env": {"OPENAI_API_KEY": "{{secret:OPENAI_API_KEY}}"}})
    env, withheld = spawn_env(spec, granted={})
    assert "OPENAI_API_KEY" not in env
    assert withheld == ["OPENAI_API_KEY"]


def test_a_granted_secret_resolves():
    spec, _ = parse_workspace({"env": {"OPENAI_API_KEY": "{{secret:OPENAI_API_KEY}}"}})
    env, withheld = spawn_env(spec, granted={"OPENAI_API_KEY": "sk-real"})
    assert env["OPENAI_API_KEY"] == "sk-real"
    assert withheld == []


def test_withheld_keys_are_RETURNED_so_the_cockpit_can_show_them():
    """A child failing for reasons nobody can see is the alternative. "2 declared secrets were not
    granted" is diagnosable; a mysterious auth error is not."""
    spec, _ = parse_workspace({"env": {"A": "{{secret:A}}", "B": "{{secret:B}}", "C": "literal"}})
    _env, withheld = spawn_env(spec, granted={})
    assert set(withheld) == {"A", "B"}


def test_a_literal_value_passes_through():
    spec, _ = parse_workspace({"env": {"NODE_ENV": "test"}})
    assert spawn_env(spec)[0] == {"NODE_ENV": "test"}


def test_inherit_from_host_works_for_a_NON_secret_var():
    """`{"AWS_PROFILE": null}` says "pass mine through" — a value in its own right, different from
    omitting the key (absent) and from `""` (set and empty)."""
    spec, _ = parse_workspace({"env": {"AWS_PROFILE": None}})
    env, withheld = spawn_env(spec, host_env={"AWS_PROFILE": "dev"})
    assert env == {"AWS_PROFILE": "dev"}
    assert withheld == []


def test_inherit_does_NOT_pass_a_host_SECRET_without_a_grant():
    """Otherwise "inherit my environment" becomes a blanket credential grant, which is exactly the
    leak the secret filter exists to prevent."""
    spec, _ = parse_workspace({"env": {"GITHUB_TOKEN": None}})
    env, withheld = spawn_env(spec, host_env={"GITHUB_TOKEN": "ghp_real"})
    assert env == {}
    assert withheld == ["GITHUB_TOKEN"]


def test_an_inherited_secret_CAN_be_explicitly_granted():
    spec, _ = parse_workspace({"env": {"GITHUB_TOKEN": None}})
    env, _withheld = spawn_env(
        spec, granted={"GITHUB_TOKEN": "ghp_real"}, host_env={"GITHUB_TOKEN": "ghp_real"}
    )
    assert env == {"GITHUB_TOKEN": "ghp_real"}


def test_an_absent_host_var_is_withheld_rather_than_set_empty():
    spec, _ = parse_workspace({"env": {"MISSING": None}})
    env, withheld = spawn_env(spec, host_env={})
    assert env == {}
    assert withheld == ["MISSING"]


def test_the_secret_hint_list_covers_a_provider_specific_credential_shape():
    """Measured: `GITHUB_PAT` read as NON-secret, so a run declaring inherit-from-host would have
    passed a GitHub personal access token straight into a leaf's environment. A hint list is only as
    good as its worst-covered credential, and bespoke names are exactly what a generic list misses.
    """
    assert looks_secret("GITHUB_PAT") is True
    assert looks_secret("AWS_ACCESS_KEY_ID") is True
    assert looks_secret("SLACK_SIGNING_SECRET") is True
    assert looks_secret("REFRESH_TOKEN") is True


@pytest.mark.parametrize(
    "name",
    [
        # a bare `PAT`, and the same token under every naming convention a second account gets
        "PAT",
        "GH_PAT",
        "PAT_GITHUB",
        "GITHUB_PAT_2",
        "GITHUB_PAT2",
    ],
)
def test_a_PAT_is_credential_bearing_however_it_is_spelled(name):
    """`GITHUB_PAT` is the MEASURED credential this hint exists for, so the narrowing that fixed
    the `..._PATH` collision has to leave every spelling of a personal access token matching. A
    trailing index counts as part of the word: `GITHUB_PAT2` is a second account's token, not a
    different kind of name."""
    assert looks_secret(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "NODE_ENV",
        "PORT",
        "PATTERN_FILE",
        "COMPATIBILITY",
        "LANG",
        # The native-library search paths. `pat` used to be spelled as the substring `_pat`, so
        # every one of these read as a credential and was stripped from a batch leaf's env — a
        # native extension then failed to load and presented as a broken leaf. Bare `PATH` is not
        # in this list on purpose: it has no `_pat` in it, so it never broke, and probing it is
        # what hid the defect.
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "PKG_CONFIG_PATH",
        "C_INCLUDE_PATH",
        "NODE_PATH",
        "GEM_PATH",
        # the other two shapes `pat` as a character run swept up
        "RE_PATTERN",
        "COMPAT_MODE",
    ],
)
def test_the_widened_hint_list_has_no_false_POSITIVES(name):
    """A false positive withholds a harmless var and makes the child fail for a reason the user
    cannot see — the widening had to be checked in both directions."""
    assert looks_secret(name) is False


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GITHUB_PAT", True),
        ("DYLD_LIBRARY_PATH", False),
        ("apiKey", True),
        ("OPENAI_API_KEY", True),
        ("ACCESSTOKEN", True),
        ("RE_PATTERN", False),
    ],
)
def test_the_env_filter_and_the_spec_scanner_AGREE(name, expected):
    """One hint list, one matcher. Env var names and spec config keys are classified by the same
    predicate, so a fix to either reaches both — two matchers over one list is the same hazard as
    two lists one indirection later, and is how `pat` came to be spelled `_pat` in one place only.

    `apiKey` and `ACCESSTOKEN` are here as the guard on the OTHER risk: most hints must keep
    matching as SUBSTRINGS. A token-split rule applied to the whole list would stop matching both
    — silently narrowing credential protection while fixing a false positive.
    """
    from personalclaw.workflows import secrets

    assert looks_secret(name) is expected
    assert secrets.is_secret_key(name) is expected


# ── presence flags, never values ──


def test_the_serialized_spec_carries_presence_NOT_values():
    """A workspace block echoed into a run record, a journal or a UI must not be what
    leaks a token."""
    spec, _ = parse_workspace({"env": {"KEY": "literal-value", "OTHER": None}})
    payload = spec.to_dict()
    assert payload["env"] == {"KEY": True, "OTHER": False}
    assert "literal-value" not in str(payload)


def test_presence_flags_distinguish_THREE_states():
    """Granted works, declared-but-not-granted needs a grant, inherited needs nothing. Collapsing
    them into configured/not-configured would make the second look like the third."""
    spec, _ = parse_workspace(
        {"env": {"A": "{{secret:A}}", "B": "{{secret:B}}", "C": None, "D": "x"}}
    )
    flags = presence_flags(spec, granted={"A": "v"})["env"]
    assert flags == {"A": "granted", "B": "declared_not_granted", "C": "inherited", "D": "literal"}


def test_presence_flags_never_include_a_value():
    spec, _ = parse_workspace({"env": {"KEY": "{{secret:KEY}}"}})
    assert "supersecret" not in str(presence_flags(spec, granted={"KEY": "supersecret"}))


# ── provisioning order ──


def test_preserve_runs_BEFORE_setup():
    """An `npm install` that runs before `.npmrc` is copied in reaches for the wrong registry."""
    spec, _ = parse_workspace(
        {"mode": "worktree", "preserve_patterns": [".npmrc"], "setup": "npm ci"}
    )
    steps = plan_provisioning(spec).steps
    assert [i for i, s in enumerate(steps) if ".npmrc" in s][0] < [
        i for i, s in enumerate(steps) if "npm ci" in s
    ][0]


def test_teardown_runs_BEFORE_deletion():
    """Its whole job is to stop services and sync artifacts out while the workspace still exists.
    Running it after would run it against a directory that no longer holds either."""
    spec, _ = parse_workspace({"mode": "worktree", "teardown": "docker compose down"})
    steps = plan_provisioning(spec).teardown_steps
    assert [i for i, s in enumerate(steps) if "docker compose down" in s][0] < [
        i for i, s in enumerate(steps) if "delete" in s
    ][0]


def test_worktree_mode_reuses_the_PROVEN_machinery():
    """A second git implementation would drift from `loop/worktree.py`, and the drift
    would show up as
    a branch naming difference nobody notices until a merge."""
    spec, _ = parse_workspace({"mode": "worktree"})
    assert any("loop.worktree" in s for s in plan_provisioning(spec).steps)


def test_an_isolated_run_commits_before_deleting():
    """Durable-branch persistence: when the workspace is ephemeral, the run record has to reference
    git rather than a directory that is about to stop existing."""
    spec, _ = parse_workspace({"mode": "worktree"})
    steps = plan_provisioning(spec).teardown_steps
    assert any("per-run branch" in s for s in steps)


def test_an_in_place_run_does_not_schedule_deletion():
    """Deleting an in-place workspace is deleting the user's project."""
    spec, _ = parse_workspace({"mode": "in_place"})
    assert not any("delete" in s for s in plan_provisioning(spec).teardown_steps)


def test_a_fatal_issue_makes_the_plan_NOT_ok():
    _spec, issues = parse_workspace({"mode": "nonsense"})
    assert plan_provisioning(WorkspaceSpec(), issues=issues).ok is False


# ── setup idempotency ──


def test_setup_is_marker_guarded_because_it_runs_on_EVERY_resume():
    """A `npm install` that re-runs is slow; a `git clone` that re-runs fails. A setup block that
    fails on resume makes resume unusable."""
    spec, _ = parse_workspace({"setup": "npm ci"})
    assert any(SETUP_MARKER_DIR in s for s in plan_provisioning(spec).steps)


def test_the_marker_is_CONTENT_addressed_so_an_edited_step_re_runs():
    """A marker keyed by index would skip an edited step as though it had already run."""
    assert setup_marker("npm ci") == setup_marker("npm ci")
    assert setup_marker("npm ci") != setup_marker("npm ci --force")


# ── folder contracts: warnings, never fatal ──


def test_the_default_lifecycle_is_TRANSIENT():
    """Agent-originated writes default to a staging zone that cannot be promoted without explicit
    action — propose-don't-write enforced by the filesystem rather than by a prompt."""
    assert parse_folder_contract({})[0].lifecycle is Lifecycle.TRANSIENT


def test_an_unknown_lifecycle_WARNS_and_falls_back_to_transient():
    """The recoverable direction: content that should have been permanent and got cleaned is
    recoverable from the run that made it, while content that should have been transient and
    persisted is a leak nobody notices."""
    contract, issues = parse_folder_contract({"lifecycle": "forever"})
    assert contract.lifecycle is Lifecycle.TRANSIENT
    assert "unknown_lifecycle" in codes(issues)
    assert not any(i.fatal for i in issues)


def test_an_unparseable_contract_yields_DEFAULTS_plus_a_warning():
    """The alternative is a directory that becomes unusable over a typo in a metadata file."""
    contract, issues = parse_folder_contract("nonsense")
    assert contract.lifecycle is Lifecycle.TRANSIENT
    assert issues and not any(i.fatal for i in issues)


def test_UNKNOWN_fields_are_kept_not_dropped():
    """A round-trip that silently lost them would corrupt a newer app's contract when an older core
    rewrote the file — the 23-of-25-dropped-memories bug class."""
    contract, issues = parse_folder_contract({"role": "notes", "future_field": {"nested": 1}})
    assert contract.unknown == {"future_field": {"nested": 1}}
    assert contract.to_dict()["future_field"] == {"nested": 1}
    assert issues == []


def test_ttl_staging_carries_a_day_scale_TTL():
    contract, _ = parse_folder_contract({"lifecycle": "ttl_staging"})
    assert contract.ttl_days == TTL_STAGING_DAYS


def test_a_non_staging_lifecycle_has_no_ttl():
    assert parse_folder_contract({"lifecycle": "permanent"})[0].ttl_days == 0


def test_agent_writable_DEFAULTS_to_false():
    """Defaulting to writable would make every folder that forgot to declare a permission an open
    one, and forgetting is the common case."""
    allowed, why = may_write(parse_folder_contract({})[0])
    assert allowed is False
    assert "does not declare" in why


def test_an_immutable_folder_refuses_writes():
    contract, _ = parse_folder_contract({"lifecycle": "immutable", "agent_writable": True})
    allowed, why = may_write(contract)
    assert allowed is False
    assert "immutable" in why


def test_immutable_plus_writable_resolves_toward_the_SAFETY_declaration():
    """The contradiction is the author's, and the immutable declaration is the one with a safety
    purpose — so it wins, and the conflict is reported."""
    contract, issues = parse_folder_contract({"lifecycle": "immutable", "agent_writable": True})
    assert contract.agent_writable is False
    assert "immutable_but_writable" in codes(issues)


def test_a_declared_writable_folder_allows_writes():
    contract, _ = parse_folder_contract({"agent_writable": True, "lifecycle": "permanent"})
    assert may_write(contract) == (True, "")


def test_missing_frontmatter_is_RECORDED_not_refused():
    """Rejecting the write would lose the content to protect a metadata convention, which inverts
    what the convention is for."""
    contract = FolderContract(required_frontmatter=["title", "date"])
    issues = validate_frontmatter(contract, {"title": "x"})
    assert "missing_frontmatter" in codes(issues)
    assert not any(i.fatal for i in issues)
    assert "date" in issues[0].message


def test_complete_frontmatter_yields_no_issues():
    contract = FolderContract(required_frontmatter=["title"])
    assert validate_frontmatter(contract, {"title": "x"}) == []
