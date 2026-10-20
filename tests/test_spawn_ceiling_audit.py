"""Spawn-ceiling audit tripwire.

Every process-spawning call site in ``src/personalclaw`` must be *accounted for*: either
it is **ceiling-wrapped** (routed through the post-exec shim via ``create_subprocess_limited``
/ ``spawn_shim_argv``, so an agent-influenced child carries a resource ceiling) or it is
**operator-exempt** (an operator-initiated spawn — the frontend build, the service/update
machinery, the interactive terminal, host-fact probes — which must NOT be constrained).

The census is an AST walk (no import side effects) over every ``subprocess.Popen/run/call/
check_output/check_call``, ``asyncio.create_subprocess_exec/shell``, ``os.execv*``, and
``StdioServerParameters`` site. Each is keyed by ``file::qualname::callee``. The keys are
partitioned across two hardcoded allowlists below. **A new, unmapped spawn site reds this
test naming its file:line** — the author must consciously classify it. That conscious step
is the control: an agent-influenced spawn that forgets the ceiling cannot slip in silently.

Adding a site → add its key to exactly one allowlist with a one-line reason. Removing a
site → drop its key. The test fails if the census and the union of the allowlists disagree
in either direction, so a stale allowlist entry is caught too.
"""

from __future__ import annotations

from spawn_ceiling_audit_shared import _CEILING_WRAPPED, _OPERATOR_EXEMPT

# ── CEILING-WRAPPED: agent-influenced spawns routed through the post-exec shim ──

# ── OPERATOR-EXEMPT: operator-initiated spawns that must NOT carry an agent ceiling ──


def test_ceiling_wrapped_and_operator_exempt_are_disjoint():
    """A site cannot be both wrapped and exempt."""
    both = set(_CEILING_WRAPPED) & set(_OPERATOR_EXEMPT)
    assert not both, f"sites in both allowlists: {sorted(both)}"


def test_agent_influenced_seams_are_all_ceiling_wrapped():
    """The named agent-influenced seams are each present
    in the ceiling-wrapped set (a regression guard so one cannot be quietly re-exempted)."""
    required = {
        "agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::"
        "create_subprocess_limited",
        "action_providers/bash_provider.py::BashActionProvider.execute::"
        "create_subprocess_limited",
        "apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen",
        "mcp_stdio.py::stdio_streams::create_subprocess_limited",
        # The ACP session_host spawn is routed through the sandbox provider handle — the
        # single seam every routed spawn now funnels through — so the ACP ceiling is asserted
        # here rather than at the former AcpProcess.spawn site.
        "sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited",
        "loop/gates.py::run_verify_command::create_subprocess_limited",
        "loop/worktree.py::_git::subprocess.run",
        # The last of the seven named seams to close: the cron/scheduled-script runner had
        # the OS sandbox and the clean env but no ceiling. Ratcheted in here so the
        # exemption cannot come back.
        "schedule_script.py::run_script_sandboxed::subprocess.run",
        # Third-party parser code from a Store app. Ratcheted in for the same reason —
        # this is the site where an exemption would be least defensible.
        "knowledge_providers/pack_parse.py::run_parse_script::subprocess.run",
        # Bundling a react artifact runs a bundler over agent-authored source.
        # Ratcheted in so the artifact deploy path can never grow a second, unwrapped build.
        "artifacts/build.py::_run_esbuild::create_subprocess_limited",
    }
    missing = sorted(required - set(_CEILING_WRAPPED))
    assert not missing, f"agent seams not ceiling-wrapped: {missing}"
