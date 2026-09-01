"""The rail for the decoy-``command``-argument class (#443).

**The class.** ``command`` is an ordinary argument name. ``extract_bash_command`` reads a
``command`` key out of *any* tool's arguments, and every decision that wanted "the shell
command this call will run" called it directly — so a destructive tool that happened to
carry a ``command`` key was judged by a string that was never going to be executed.

Two independent gates consumed it, and only the first was reported:

* :func:`resolve_effective_risk` → ``"safe"`` → **auto-approved with no prompt** under
  ``trust_reads``, which is a shipped approval mode and what ``--test-mode`` selects.
* :func:`task_mode_denies` → ``""`` → the tool was **allowed to run** in ``ask``,
  ``plan`` and ``build`` mode, whose entire contract is that mutations do not.

Both reduce to one question — *is this call a shell invocation?* — which nothing owned.
:func:`is_shell_invocation` owns it now, and this suite is the guard on the property that
matters: **no argument a caller can add may lower a tool's risk or unlock it.**

**Why the tests are written as an attack over every gate, not per-function.** The first
fix for this bug moved the extraction from :func:`resolve_effective_risk` into
:func:`classify_invocation` and left it unscoped, so the bypass survived a rewrite that
looked like a fix. A test naming one function would have gone green. So the core test
sweeps the decoy across *every* consumer at once.
"""

from __future__ import annotations

import pytest

from personalclaw.task_modes import (
    MUTATING,
    READ_ONLY,
    SHELL_TITLE_PREFIXES,
    SHELL_TOOL_NAMES,
    UNCLASSIFIED,
    classify_invocation,
    extract_bash_command,
    infer_risk_from_name,
    is_shell_invocation,
    resolve_effective_risk,
    shell_command,
    task_mode_denies,
)

#: Tools whose declared risk a decoy must never lower. Real tools with real declared
#: risks, named in #443's measurement.
DESTRUCTIVE_TOOLS = [
    ("workflow_delete_def", {"name": "x"}),
    ("memory_forget", {"rule": "x"}),
    ("artifact_delete", {"slug": "x"}),
    ("automation_delete_all", {}),
    ("task_delete", {"task_id": "t-1"}),
]

#: Every spelling of the decoy. `is_read_only_bash` says yes to all of them, which is the
#: whole point — the string is plausible, it is simply not this call's command.
DECOYS = [
    {"command": "ls"},
    {"command": "cat /etc/hostname"},
    {"command": "pwd"},
    {"command": "git status"},
]


def _with(args: dict, decoy: dict) -> dict:
    return {**args, **decoy}


class TestDecoyCannotLowerRisk:
    """The reported half: `trust_reads` auto-approves anything resolving to `safe`."""

    @pytest.mark.parametrize("tool,args", DESTRUCTIVE_TOOLS, ids=[t for t, _ in DESTRUCTIVE_TOOLS])
    @pytest.mark.parametrize("decoy", DECOYS, ids=[d["command"].split()[0] for d in DECOYS])
    def test_declared_destructive_survives_the_decoy(self, tool, args, decoy):
        baseline = resolve_effective_risk("destructive", tool, "", args)
        withdecoy = resolve_effective_risk("destructive", tool, "", _with(args, decoy))
        assert baseline == "destructive"
        assert withdecoy == "destructive", (
            f"a `command` argument downgraded {tool} from {baseline} to {withdecoy} — "
            "trust_reads auto-approves `safe` with no prompt"
        )

    @pytest.mark.parametrize("tool,args", DESTRUCTIVE_TOOLS, ids=[t for t, _ in DESTRUCTIVE_TOOLS])
    def test_declared_caution_survives_the_decoy(self, tool, args):
        assert resolve_effective_risk("caution", tool, "", _with(args, {"command": "ls"})) != "safe"

    def test_an_undeclared_external_tool_is_not_made_safe_by_a_decoy(self):
        """An MCP tool that declares no risk is the case with the least to fall back on —
        so it is the one where a decoy would buy the most."""
        assert resolve_effective_risk("", "vendor_wipe_database", "", {"command": "ls"}) != "safe"

    def test_the_decoy_cannot_produce_a_read_only_verdict(self):
        """A tool that legitimately answers READ_ONLY must still be forced MUTATING by a
        decoy `command`, or the bypass reopens through the kind/name fallthrough.

        This used to be demonstrated with `memory_forget`, which reached READ_ONLY because
        `forget` was missing from `_MUTATING_NAME_HINTS` — that was #2118, and it is fixed,
        so `memory_forget` now answers MUTATING on its name alone and can no longer show
        this property. `memory_recall` is a genuine read, which is the honest fixture for it:
        the point is that a non-shell tool carrying a shell string is not a call we
        understand, whatever its name says.
        """
        assert classify_invocation("memory_recall", "", {"rule": "x"}) == READ_ONLY
        assert classify_invocation("memory_recall", "", {"rule": "x", "command": "ls"}) == MUTATING


#: The subset of :data:`DESTRUCTIVE_TOOLS` that ``task_mode_denies`` actually denies, so a
#: "the decoy no longer unlocks it" assertion has something to measure.
#:
#: ``memory_forget`` was deliberately ABSENT here while #2118 was open: it graded
#: ``destructive`` by name inference but classified READ_ONLY, and ``task_mode_denies``
#: consults only the classifier, so it ran in ask/plan with no decoy needed. #2118 is closed
#: — ``_MUTATING_NAME_HINTS`` is now a union over ``_DESTRUCTIVE_NAME_HINTS`` — so it belongs
#: in this list, and its presence is what keeps the decoy legs below honest about it.
TASK_MODE_DENIED_TOOLS = [
    ("workflow_delete_def", {"name": "x"}),
    ("artifact_delete", {"slug": "x"}),
    ("automation_delete_all", {}),
    ("task_delete", {"task_id": "t-1"}),
    ("session_delete", {"id": "s-1"}),
    ("memory_forget", {"rule": "x"}),
]


class TestDecoyCannotUnlockTaskModes:
    """The unreported half. ask/plan/build decide WHICH tools may run at all."""

    @pytest.mark.parametrize("mode", ["ask", "plan", "build"])
    @pytest.mark.parametrize(
        "tool,args", TASK_MODE_DENIED_TOOLS, ids=[t for t, _ in TASK_MODE_DENIED_TOOLS]
    )
    def test_decoy_does_not_unlock_a_denied_tool(self, mode, tool, args):
        assert task_mode_denies(mode, tool, "", args), f"{tool} should be denied in {mode}"
        assert task_mode_denies(mode, tool, "", _with(args, {"command": "ls"})), (
            f"a `command` argument let {tool} RUN in {mode} mode, whose contract is that "
            "mutations do not"
        )

    def test_agent_mode_still_allows_everything(self):
        """Vacuity floor: a fix that denied everything would pass every test above."""
        assert task_mode_denies("agent", "workflow_delete_def", "", {"command": "ls"}) == ""

    def test_a_destructive_verb_is_denied_in_ask_and_plan(self):
        """#2118 CLOSED. This is the inverse of the test that used to pin the gap here.

        The two name heuristics disagreed: ``destroy``, ``drop_``, ``purge`` and ``forget``
        were destructive-but-not-mutating, so ``memory_forget`` graded ``destructive`` for
        the approval card while classifying READ_ONLY for the task-mode gate — and ran in
        ask AND plan mode with nothing to deny it, no decoy argument required. The gate
        asked only the classifier, and the classifier had never heard of the verb.

        Both heuristics must now agree for every destructive verb, in every mode whose
        contract is that mutations do not run.
        """
        for tool in ("memory_forget", "knowledge_forget", "cache_purge", "session_destroy"):
            assert infer_risk_from_name(tool) == "destructive", tool
            assert classify_invocation(tool, "", {}) == MUTATING, (
                f"{tool} classifies read-only, so the task-mode gate will let it run — "
                "the #2118 shape"
            )
            for mode in ("ask", "plan"):
                assert task_mode_denies(mode, tool, "", {}), f"{tool} should be denied in {mode}"

    def test_the_destructive_set_is_contained_in_the_mutating_set(self):
        """The structural half — what stops #2118 recurring rather than being fixed once.

        A destructive verb that is not also mutating is a contradiction, and two independent
        literals had silently drifted into exactly that. `_MUTATING_NAME_HINTS` is now built
        as a union over `_DESTRUCTIVE_NAME_HINTS`, so adding a verb to one widens the
        task-mode gate in the same edit. This asserts the containment rather than the
        current membership, so it keeps holding as either set grows — the leg above would
        pass while a NEWLY added destructive verb leaked, because it names four tools.
        """
        from personalclaw.task_modes import _DESTRUCTIVE_NAME_HINTS, _MUTATING_NAME_HINTS

        assert _DESTRUCTIVE_NAME_HINTS, "the destructive set is empty — nothing is asserted"
        missing = [h for h in _DESTRUCTIVE_NAME_HINTS if h not in _MUTATING_NAME_HINTS]
        assert not missing, (
            f"destructive verbs absent from the mutating set: {missing}. Each one is a tool "
            "the approval card grades 'destructive' while the task-mode gate reads it as a "
            "read and lets it run in ask/plan (#2118)."
        )
        assert len(_MUTATING_NAME_HINTS) == len(set(_MUTATING_NAME_HINTS)), (
            "the union duplicated a fragment; harmless for matching but it means the two "
            "sets are being maintained by hand again"
        )


class TestRealShellCallsAreUnchanged:
    """The regression surface. Over-tightening here would prompt on every read-only
    `bash`, which is the behaviour `trust_reads` exists to avoid."""

    @pytest.mark.parametrize(
        "title,kind",
        [
            ("bash", ""),  # native loop: its own tool, and it declares NO kind
            ("Bash", ""),  # title casing is the model's, not ours
            ("execute_bash", ""),
            ("terminal", ""),
            ("bash", "execute"),  # ACP: kind declared
            ("anything", "command"),  # ACP: kind alone is enough
            ("anything", "execute"),
        ],
    )
    def test_read_only_shell_is_still_downgraded_to_safe(self, title, kind):
        assert is_shell_invocation(title, kind) is True
        assert resolve_effective_risk("destructive", title, kind, {"command": "ls -la"}) == "safe"

    @pytest.mark.parametrize("title,kind", [("bash", ""), ("bash", "execute")])
    def test_mutating_shell_is_still_destructive(self, title, kind):
        assert resolve_effective_risk("destructive", title, kind, {"command": "rm -rf /"}) == (
            "destructive"
        )

    def test_acp_json_string_input_still_parses(self):
        """ACP agents pass the arguments as a raw JSON string, not a dict."""
        assert resolve_effective_risk("destructive", "bash", "", '{"command": "ls"}') == "safe"
        assert shell_command("bash", "", '{"command": "ls"}') == "ls"

    def test_read_only_shell_still_runs_in_ask_mode(self):
        assert task_mode_denies("ask", "bash", "", {"command": "ls"}) == ""
        assert task_mode_denies("ask", "bash", "", {"command": "rm -rf /"}) != ""


class TestAcpInlineCommandTitle:
    """An ACP permission frame can carry the command with no kind and no tool name.

    Found by the existing suite, not by these tests: an exact-name-plus-kind check reds
    ``test_acp_effective_risk_correlation`` with "a read-only ls RUNS in ask mode",
    because a permission frame with no preceding ``tool_call`` frame has only its title.
    """

    def test_running_prefix_is_recognised_as_a_shell_call(self):
        assert is_shell_invocation("Running: ls -la", "") is True
        title, args = "Running: ls -la", {"command": "ls -la"}
        assert classify_invocation(title, "", args) == READ_ONLY
        assert resolve_effective_risk("destructive", title, "", args) == "safe"
        assert task_mode_denies("ask", title, "", args) == ""

    def test_a_mutating_inline_command_is_still_denied(self):
        title, args = "Running: rm -rf /", {"command": "rm -rf /"}
        assert classify_invocation(title, "", args) == MUTATING
        assert resolve_effective_risk("destructive", title, "", args) == "destructive"
        assert task_mode_denies("ask", title, "", args) != ""

    def test_the_prefix_matches_the_one_hooks_normalizes(self):
        """The prefix is not a convention this module invented, and it must not become a
        second copy of one. ``hooks`` strips the same string and treats it as
        ``execute_bash``; if that is ever renamed, this gate has to notice rather than
        silently stop recognising ACP shell calls.
        """
        from personalclaw.hooks import _TOOL_TITLE_PREFIXES

        lowered = {p.lower() for p in _TOOL_TITLE_PREFIXES}
        for prefix in SHELL_TITLE_PREFIXES:
            assert prefix in lowered, f"{prefix!r} is no longer a title prefix hooks strips"

    def test_reading_prefix_is_not_treated_as_a_shell_call(self):
        """``Reading `` names a FILE. Treating it as a command would hand
        ``is_read_only_bash`` a path to parse, and would let a file argument speak for a
        call the same way a ``command`` argument used to."""
        assert is_shell_invocation("Reading /etc/passwd", "") is False
        assert shell_command("Reading /etc/passwd", "", {"command": "rm -rf /"}) == ""


class TestUnreadableShellCall:
    def test_shell_with_no_readable_command_is_unclassified_not_a_read(self):
        """`bash` carries no mutating name hint, so before the fix an unreadable input
        fell all the way through to name inference and came back READ_ONLY — the
        product's own shell tool, classified as a read. It is now UNCLASSIFIED, and
        `resolve_effective_risk` honours the declared risk for it."""
        for bad in (None, {}, {"other": "x"}):
            assert classify_invocation("bash", "", bad) == UNCLASSIFIED
            assert resolve_effective_risk("destructive", "bash", "", bad) == "destructive"

    def test_unclassified_shell_never_resolves_safe(self):
        """Not even with nothing declared: trust_reads must not auto-approve a command
        nobody read."""
        assert resolve_effective_risk("", "bash", "", {}) != "safe"


class TestScopedVsRawExtractor:
    """The two functions answer different questions, and the difference IS the fix."""

    def test_raw_extractor_still_reads_any_command_key(self):
        """`extract_bash_command` is a parser and stays one — narrowing it would break
        the ACP callers that hand it a bare command string. The scoping is a separate
        function precisely so the parser can stay total."""
        assert extract_bash_command({"command": "ls"}) == "ls"
        assert extract_bash_command("ls -la") == "ls -la"

    def test_scoped_extractor_refuses_a_non_shell_tool(self):
        assert shell_command("memory_forget", "", {"command": "ls"}) == ""
        assert shell_command("workflow_delete_def", "", {"command": "ls"}) == ""

    def test_scoped_extractor_agrees_with_the_raw_one_for_shell_calls(self):
        for title, kind in (("bash", ""), ("x", "execute")):
            assert shell_command(title, kind, {"command": "ls"}) == extract_bash_command(
                {"command": "ls"}
            )

    def test_no_gate_reaches_the_raw_extractor(self):
        """The census that keeps this closed. `task_modes` is where every gate lives, so
        the only permitted uses of the raw parser there are inside `shell_command` and
        its own definition — a gate calling it directly is the bug, restored.
        """
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src"
            / "personalclaw"
            / "task_modes.py"
        ).read_text(encoding="utf-8")
        # Call sites, not the def and not prose about it.
        calls = [
            n
            for n, line in enumerate(src.splitlines(), 1)
            if "extract_bash_command(" in line
            and "def extract_bash_command" not in line
            and not line.lstrip().startswith("#")
            and ":func:" not in line
        ]
        assert len(calls) == 2, (
            f"expected exactly 2 call sites of the RAW extractor in task_modes.py "
            f"(`shell_command`, and `classify_invocation`'s final decoy check), found "
            f"{len(calls)} at lines {calls} — a gate reading a `command` key off any "
            "tool is #443"
        )


class TestOneShellVocabulary:
    def test_loop_breaker_shares_the_set_rather_than_copying_it(self):
        """It held a second copy that had already drifted (no `run_script`). Two copies
        of a security-relevant set is one that gets tightened in half the places."""
        from personalclaw.guardrails import loop_breaker

        assert loop_breaker.SHELL_TOOLS is SHELL_TOOL_NAMES

    def test_the_set_covers_the_product_s_own_shell_tool(self):
        """Vacuity floor: an empty or typo'd set would make `is_shell_invocation` false
        for everything, which passes every bypass test above while breaking `bash`."""
        assert "bash" in SHELL_TOOL_NAMES
        assert len(SHELL_TOOL_NAMES) >= 5

    def test_approval_brief_hints_stay_separate(self):
        """`SHELL_HINTS` is substring matching for a human-facing brief, where
        over-matching is harmless. This set decides whether a `command` string is
        authoritative, where over-matching is the bug. Merging them would widen the
        gate — assert they are not the same object so a future tidy-up has to read this.
        """
        from personalclaw.approval_brief import SHELL_HINTS

        assert set(SHELL_HINTS) != set(SHELL_TOOL_NAMES)
        # And the reason: the hints include fragments that are not shell TOOLS.
        assert {"exec", "spawn", "command"} & set(SHELL_HINTS)
        assert not {"exec", "spawn", "command"} & SHELL_TOOL_NAMES
