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
        """`memory_forget` carries no mutating NAME hint, so the kind/name fallthrough
        answers READ_ONLY on its own — which would hand back exactly what the removed
        step 1 used to give. The bypass has two doors; this is the second."""
        assert classify_invocation("memory_forget", "", {"rule": "x"}) == READ_ONLY
        assert classify_invocation("memory_forget", "", {"rule": "x", "command": "ls"}) == MUTATING


#: The subset of :data:`DESTRUCTIVE_TOOLS` that ``task_mode_denies`` actually denies, so a
#: "the decoy no longer unlocks it" assertion has something to measure.
#:
#: ``memory_forget`` is deliberately absent, and its absence is a SEPARATE finding, not a
#: gap in this fix: ``infer_risk_from_name("memory_forget")`` answers ``destructive``
#: while ``classify_invocation`` answers ``READ_ONLY`` (``forget`` is not in
#: ``_MUTATING_NAME_HINTS``), and ``task_mode_denies`` consults only the second — so it
#: runs in ask/plan mode with no decoy needed. Same for ``knowledge_forget``. That is two
#: name heuristics in one module disagreeing, a different root cause from this one, and
#: pulling it in here would mean one branch changing the risk class of every tool whose
#: name inference and classification differ. Filed as #2118.
TASK_MODE_DENIED_TOOLS = [
    ("workflow_delete_def", {"name": "x"}),
    ("artifact_delete", {"slug": "x"}),
    ("automation_delete_all", {}),
    ("task_delete", {"task_id": "t-1"}),
    ("session_delete", {"id": "s-1"}),
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

    def test_the_separate_forget_finding_is_recorded_not_silently_fixed(self):
        """Pins #2118, the finding this fix deliberately does NOT close, so it stays visible.

        ``memory_forget``/``knowledge_forget`` run in ask/plan mode with no decoy at all,
        because the module's two name heuristics disagree about them. If a later change
        closes that, this test reds and the note above gets deleted with it — which is the
        point. A known gap that nothing asserts is a gap that gets forgotten.
        """
        for tool in ("memory_forget", "knowledge_forget"):
            assert infer_risk_from_name(tool) == "destructive"
            assert classify_invocation(tool, "", {}) == READ_ONLY
            assert task_mode_denies("plan", tool, "", {}) == ""


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
