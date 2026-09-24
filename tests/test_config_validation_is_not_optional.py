"""Config validation runs on a NORMAL install, and the dependency that powers it is declared.

`jsonschema` was not in `[project] dependencies`. It arrived only transitively through the
OPTIONAL `[mcp]` extra, and `_validate_config_data` opened with `if not _HAS_JSONSCHEMA: return
data`. So `pip install personalclaw` got:

* no enum validation — measured: ``agent.approval_mode = "NOT_A_MODE"`` entered the running config
* no type validation
* no unknown-top-level-key warning
* no pruning of retired fields, so a pre-removal `config.json` never self-healed

and CI could not see any of it, because `[dev]` pulls `[mcp]` and the validation tests carried a
`skipif` — a skip that reads as a pass in every summary.

These tests hold the FIX in place from both ends: the declaration (so a future dependency bump
cannot quietly make it transitive again) and the behaviour (so the pass cannot become a no-op
again by some other route).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_jsonschema_is_a_declared_core_dependency():
    """The declaration itself, not just importability in this venv.

    Importability proves nothing here: the dev venv has `jsonschema` either way, which is exactly
    why the defect survived. What matters is that a bare `pip install personalclaw` gets it.
    """
    text = _PYPROJECT.read_text(encoding="utf-8")
    core = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.M)
    assert core, "pyproject has no [project] dependencies array"
    assert "jsonschema" in core.group(1), (
        "jsonschema is not a hard dependency — config validation silently no-ops on a normal "
        "install, and only the optional [mcp] extra would drag it in"
    )


def test_the_validator_imports_jsonschema_unconditionally():
    """No `try/except ImportError` guard, because a guard is what made the pass skippable.

    Reads ``config/validation.py``, which is where ``_validate_config_data`` and its
    ``import jsonschema`` live since PHF-14 split the validation machinery out of
    ``config/loader.py``. The rail has to follow the code it guards: pointed at ``loader.py``
    it would now red on the move itself, and "fix" it by re-adding an import ``loader`` does
    not use — which is the shim this codebase forbids.
    """
    src = (
        Path(__file__).resolve().parent.parent / "src/personalclaw/config/validation.py"
    ).read_text(encoding="utf-8")
    # CODE only. The name legitimately survives in the comment that explains what was removed,
    # and a rail that counts comment text fails on its own documentation — which it did on the
    # first run of this test.
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert "_HAS_JSONSCHEMA" not in code, "the optional-dependency flag is back in code"
    assert re.search(
        r"^import jsonschema$", code, re.M
    ), "the config validator no longer imports jsonschema"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config home. Never the real one."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _load(home: Path, data: dict):
    (home / "config.json").write_text(json.dumps(data), encoding="utf-8")
    from personalclaw.config.loader import AppConfig

    return AppConfig.load()


def test_an_invalid_enum_does_not_reach_the_running_config(home):
    """The measured symptom, as a test: the bad value is dropped and the default takes over."""
    cfg = _load(home, {"agent": {"approval_mode": "NOT_A_MODE"}})
    assert (
        cfg.agent.approval_mode != "NOT_A_MODE"
    ), "an invalid enum entered the running config — validation is a no-op again"


def test_a_retired_field_is_pruned_rather_than_carried(home):
    """The pass the early return also skipped: retired fields self-heal on load.

    `agent.streaming` was removed from `AgentConfig` with zero consumers; a pre-removal config
    should load cleanly and be rewritten without it, not warn on every load forever.
    """
    from personalclaw.config.validation import _validate_config_data

    data = {"agent": {"streaming": True, "model": "gpt-9"}, "default_memory_store": "x"}
    _validate_config_data(data)
    assert "streaming" not in data["agent"], "retired agent.streaming survived validation"
    assert "model" not in data["agent"], "retired agent.model survived validation"
    assert "default_memory_store" not in data, "retired default_memory_store survived validation"


def test_an_unrecognized_top_level_key_is_reported(home, caplog):
    """The third skipped pass. Silence here is how a typo'd section looks like it applied."""
    import logging

    from personalclaw.config.validation import _validate_config_data

    with caplog.at_level(logging.WARNING, logger="personalclaw.config.loader"):
        _validate_config_data({"definitely_not_a_section": {"x": 1}})
    assert any(
        "unrecognized top-level keys" in r.message for r in caplog.records
    ), f"no warning for an unknown section: {[r.message for r in caplog.records]}"


def test_the_validation_pass_can_still_fail(home):
    """The vacuity assertion: prove the guard has teeth by giving it something it must reject.

    Without this, every test above could pass against a validator that accepts everything — which
    is precisely the state this change repairs.
    """
    from personalclaw.config.validation import _validate_config_data

    data = {"agent": {"max_subagents": "not-a-number"}}
    _validate_config_data(data)
    assert (
        data.get("agent", {}).get("max_subagents") != "not-a-number"
    ), "a string sailed through an integer field — the schema is not being applied"
