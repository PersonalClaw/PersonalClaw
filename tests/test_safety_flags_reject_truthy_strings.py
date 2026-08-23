"""A safety flag must not be enabled by a string whose author meant to disable it.

``bool("false")`` is ``True`` and ``bool("0")`` is ``True``, so five flags that decide whether a
safety control applies were **enabled** by a value plainly written to disable them — and the failure
landed in the unsafe direction with nothing logged.

The five, and where the value comes from:

* ``hooks.auto_approve_subagent_spawn`` / ``auto_approve_subagent_tools`` — the hooks JSON file the
  user hand-edits. JSON takes both ``false`` and ``"false"``, and the quoted form is the habit
  anyone arriving from YAML or a shell env brings.
* ``skip_preflight`` / ``always_allow`` — an **HTTP request body**: whatever the client sent.
* ``unattended_suppress`` — config.

These tests assert the **CALL SITES**, not just the helper: a correct `strict_bool` is worthless
if a gate still calls bare `bool()`, which is exactly the shape of the original defect.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from personalclaw import hooks
from personalclaw.safety_flags import strict_bool

SRC = pathlib.Path(hooks.__file__).resolve().parent

#: ``(module, flag)`` for every safety flag that reads user-controlled data.
GUARDED_FLAGS = [
    ("hooks.py", "auto_approve_subagent_spawn"),
    ("hooks.py", "auto_approve_subagent_tools"),
    ("workflows/handlers.py", "skip_preflight"),
    ("workflows/handlers.py", "always_allow"),
    ("workflows/engine.py", "unattended_suppress"),
]


class TestTheHelper:
    @pytest.mark.parametrize("value", ["false", "False", " FALSE ", "no", "off", "0", "", "n"])
    def test_a_falsey_spelling_never_enables_the_control(self, value):
        assert strict_bool(value, field="t") is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on", "1", "y"])
    def test_a_truthy_spelling_still_works(self, value):
        """The fix must not break the operator who wrote a quoted `"true"` on purpose."""
        assert strict_bool(value, field="t") is True

    def test_a_real_bool_passes_through(self):
        assert strict_bool(True, field="t") is True
        assert strict_bool(False, field="t") is False

    @pytest.mark.parametrize("value", ["maybe", "yep", [], {"a": 1}, object()])
    def test_an_unrecognised_value_takes_the_DEFAULT_and_warns(self, value, caplog):
        """Unreadable must mean "safe", and must not be silent — a control that stopped applying
        for a reason nobody can see is the defect this whole batch is made of."""
        with caplog.at_level("WARNING"):
            assert strict_bool(value, field="probe.flag", default=False) is False
        named = [r for r in caplog.records if "probe.flag" in r.getMessage()]
        assert named, f"no WARNING naming the field: {[r.getMessage() for r in caplog.records]}"

    def test_the_default_is_honoured_for_None(self):
        assert strict_bool(None, field="t", default=False) is False
        assert strict_bool(None, field="t", default=True) is True

    def test_numbers_coerce_normally(self):
        """`0`/`1` are unambiguous, so they are not worth refusing."""
        assert strict_bool(0, field="t") is False
        assert strict_bool(1, field="t") is True

    def test_the_old_behaviour_really_was_wrong(self):
        """The vacuity floor: if `bool("false")` were already False, none of this would matter and
        this whole file would be asserting nothing."""
        assert bool("false") is True
        assert bool("0") is True


class TestTheCallSites:
    """The half that catches a regression: the helper being right does not make the gates right."""

    @pytest.mark.parametrize("module_rel,flag", GUARDED_FLAGS)
    def test_the_flag_is_read_through_strict_bool_not_bare_bool(self, module_rel, flag):
        path = SRC / module_rel
        assert path.exists(), f"{module_rel} moved — re-point this rail"
        tree = ast.parse(path.read_text(), filename=str(path))
        bare: list[int] = []
        strict: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name not in ("bool", "strict_bool"):
                continue
            # does this call mention the flag anywhere inside it?
            if flag not in ast.dump(node):
                continue
            (strict if name == "strict_bool" else bare).append(node.lineno)
        assert not bare, (
            f"{module_rel}:{bare} reads the safety flag {flag!r} with bare bool(). "
            f'bool("false") is True, so a value written to DISABLE the control enables it. '
            f"Use strict_bool(..., field=...)."
        )
        assert strict, (
            f"{module_rel} no longer reads {flag!r} through strict_bool — either the flag was "
            f"renamed or the guard was dropped. A rail that matches nothing is not a rail."
        )

    def test_every_guarded_module_imports_the_helper(self):
        for module_rel, _ in GUARDED_FLAGS:
            text = (SRC / module_rel).read_text()
            assert "strict_bool" in text, f"{module_rel} does not reference strict_bool"

    def test_the_hooks_config_actually_refuses_a_quoted_false(self):
        """End to end through the real loader, not the helper: the defect was that a config file
        saying `"false"` turned auto-approval ON."""
        cfg = hooks.HooksConfig.from_dict(
            {
                "auto_approve_subagent_spawn": "false",
                "auto_approve_subagent_tools": "0",
            }
        )
        assert cfg.auto_approve_subagent_spawn is False
        assert cfg.auto_approve_subagent_tools is False

    def test_the_hooks_config_still_honours_a_real_true(self):
        cfg = hooks.HooksConfig.from_dict({"auto_approve_subagent_spawn": True})
        assert cfg.auto_approve_subagent_spawn is True
