"""AG-5 — ``SafetyProfile.tool_grants`` is enforced at a LIVE tool-dispatch seam (§3).

Why this file exists at all. ``tool_grants`` shipped with the profile family and was read by
nothing in ``src/``: its one enforcement point (``sandbox_providers/tool_gateway.py``) had zero
importers outside ``tests/``, so the field was a read-only promise that denied nothing — and it
survived a ``confirmed`` audit verdict anyway. The lesson is the design rule for this file: an
availability check on a SYMBOL can never be false, so nothing here asserts that a function
exists, that a field is importable, or that a profile carries a tier. Every test DRIVES a
dispatch path and asserts the call was refused there.

The seam driven below is ``mcp_shared.call_tool_with_logging`` — "EVERY in-process MCP tool call
funnels through this function", which is why the capability posture is enforced there. The tool's
own function is passed as a spy, so a "refusal" that still ran the tool cannot pass.

1. **A tier the call is not on refuses it**, and the tool never runs.
2. **The operator ceiling reaches the seam.** A ceiling ``tools`` allowlist composes into
   ``tool_grants="custom"`` + ``tool_allowlist`` (``ceiling._overrides_gate``) and now refuses a
   call it previously changed nothing about. This test cannot pass unless the live path reads
   those two fields: no other value in the tree carries that allowlist, and the tool it refuses
   is a READ tool that no write classifier would ever deny.
3. **Fail-CLOSED edges deny** — an empty/blank allowlist, an unrecognised tier, and a ceiling
   that will not resolve.

Every deny carries a vacuity floor: the identical call under a granting tier is SERVED. A gate
that refused everything would pass the refusals alone.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import mcp_shared
from personalclaw.guardrails import ceiling as C
from personalclaw.guardrails.policy import SafetyProfile, tool_grant_denial
from personalclaw.workflows.engine import WF_DEPTH_KEY


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home for the SEL rows the seam writes, and for the ceiling it reads."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv(C.CEILING_PATH_ENV, raising=False)
    C.reset_ceiling()
    yield tmp_path
    C.reset_ceiling()


def _write_ceiling(home_dir, scopes: dict) -> None:
    path = home_dir / "governance" / "ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "scopes": scopes}), encoding="utf-8")
    C.reset_ceiling()


class _Spy:
    """The tool's own function. ``calls`` is the only proof that a refusal actually refused."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, name: str, args: dict) -> str:
        self.calls.append(name)
        return f"ran {name}"


def _dispatch(name: str) -> tuple[str, _Spy]:
    """One in-process MCP tool call through the real handler seam."""
    spy = _Spy()
    result = mcp_shared.call_tool_with_logging(
        name,
        {},
        lambda _n, a: a,
        spy,
        "test-session",
        "test-service",
    )
    return result, spy


def _leaf(monkeypatch, *, read_only: bool, depth: int = 1) -> None:
    """Put the process in the posture a compiled workflow leaf runs with."""
    monkeypatch.setenv(WF_DEPTH_KEY, str(depth))
    monkeypatch.setenv(mcp_shared.LEAF_READ_ONLY_KEY, "1" if read_only else "0")


# ── 1. the tier refuses the call at the seam ──────────────────────────────────


def test_a_read_tier_refuses_a_write_call_at_the_handler_and_the_tool_never_runs(home, monkeypatch):
    """LOAD-BEARING. The refusal has to happen at the seam that ANSWERS the call: a filtered tool
    list computed anywhere else is a control that looks like enforcement while the handler still
    runs the tool. So the assertion is on the spy, not only on the returned string."""
    _leaf(monkeypatch, read_only=True)
    result, spy = _dispatch("memory_remember")
    assert result.startswith("Error:"), result
    assert "write-class" in result, result
    assert "read" in result, result
    assert spy.calls == [], "the tool ran despite being refused"


def test_a_read_write_tier_serves_the_same_call(home, monkeypatch):
    """The vacuity floor for the test above: identical call, one tier wider, actually runs."""
    _leaf(monkeypatch, read_only=False)
    result, spy = _dispatch("memory_remember")
    assert result == "ran memory_remember", result
    assert spy.calls == ["memory_remember"]


def test_a_read_tier_still_serves_a_read_call(home, monkeypatch):
    """Read-only is scoped, not deny-all — a research leaf that cannot read cannot research."""
    _leaf(monkeypatch, read_only=True)
    result, spy = _dispatch("memory_recall")
    assert result == "ran memory_recall", result
    assert spy.calls == ["memory_recall"]


# ── 2. the operator ceiling reaches the seam ──────────────────────────────────


def test_the_ceiling_tools_allowlist_refuses_a_call_the_write_gate_would_have_served(
    home, monkeypatch
):
    """LOAD-BEARING, and the test that cannot be faked.

    ``memory_recall`` is a READ tool and the leaf is MUTATING, so every other gate in this path
    admits it: the write classifier says "not a write", the tier says ``read_write``. The only
    thing that can refuse it is the ``tool_allowlist`` the ceiling composed — a value that, before
    this atom, was parsed, validated, composed and then read by nobody.
    """
    _write_ceiling(home, {"tools": {"allow": ["artifact_*"]}})
    _leaf(monkeypatch, read_only=False)
    result, spy = _dispatch("memory_recall")
    assert result.startswith("Error:"), result
    assert "allowlist" in result, result
    assert "artifact_*" in result, result
    assert spy.calls == [], "the tool ran despite being outside the operator's allowlist"


def test_an_allowlisted_call_is_served_under_the_same_ceiling(home, monkeypatch):
    """The vacuity floor for the ceiling: the allowlist ADMITS what it names, by the same
    ``name_glob`` matcher the ceiling composed it with (so a pattern is a pattern at both ends)."""
    _write_ceiling(home, {"tools": {"allow": ["artifact_*"]}})
    _leaf(monkeypatch, read_only=False)
    result, spy = _dispatch("artifact_get")
    assert result == "ran artifact_get", result
    assert spy.calls == ["artifact_get"]


def test_an_allowlisted_write_call_is_served_because_the_operator_named_it(home, monkeypatch):
    """An explicit allowlist entry IS the write grant. ``custom`` must not second-guess the
    operator who wrote it, or the scope could never grant anything a classifier calls a write."""
    _write_ceiling(home, {"tools": {"allow": ["artifact_save"]}})
    _leaf(monkeypatch, read_only=False)
    result, spy = _dispatch("artifact_save")
    assert result == "ran artifact_save", result
    assert spy.calls == ["artifact_save"]


def test_the_parent_is_not_a_leaf_so_depth_zero_is_untouched(home, monkeypatch):
    """Depth 0 is the parent turn, not a leaf. The ceiling's tools scope is a LEAF bound here; a
    control that silently narrowed every parent tool call would be an outage with no way out."""
    _write_ceiling(home, {"tools": {"allow": ["artifact_*"]}})
    _leaf(monkeypatch, read_only=True, depth=0)
    result, spy = _dispatch("memory_remember")
    assert result == "ran memory_remember", result
    assert spy.calls == ["memory_remember"]


# ── 3. fail-CLOSED edges ──────────────────────────────────────────────────────


def test_a_ceiling_that_will_not_resolve_denies_at_the_handler(home, monkeypatch):
    """A corrupt ceiling means the leaf's grants are UNKNOWN. Unknown must deny: falling through
    to "no bound" would turn a permissions problem into a silent privilege escalation, which is
    the same reasoning ``load_ceiling`` gives for raising."""
    path = home / "governance" / "ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    C.reset_ceiling()
    _leaf(monkeypatch, read_only=False)
    result, spy = _dispatch("memory_recall")
    assert result.startswith("Error:"), result
    assert "ceiling" in result, result
    assert spy.calls == [], "an unresolvable ceiling served the call anyway"


@pytest.mark.parametrize("allowlist", [(), ("",), ("   ",)])
def test_a_custom_tier_with_no_usable_allowlist_denies_everything(allowlist):
    """``custom`` + nothing parseable is an unreadable grant set. It denies EVERY tool, reads
    included — the narrowest posture, not the widest. Unreachable through the ceiling (which only
    emits ``custom`` alongside entries), so it is asserted on the algebra directly."""
    profile = SafetyProfile(name="broken", tool_grants="custom", tool_allowlist=allowlist)
    assert tool_grant_denial(profile, "memory_recall", write_class=False)
    assert tool_grant_denial(profile, "memory_remember", write_class=True)


@pytest.mark.parametrize("tier", ["", "READ_WRITE", "readwrite", "full", "none"])
def test_an_unrecognised_tier_is_the_narrowest_one(tier):
    """A typo in a tier must not read as "full grant". Every unrecognised value resolves to
    ``read``: writes denied, reads served."""
    profile = SafetyProfile(name="typo", tool_grants=tier)
    assert tool_grant_denial(profile, "memory_remember", write_class=True)
    assert tool_grant_denial(profile, "memory_recall", write_class=False) == ""
