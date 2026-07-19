"""Action provider schema ↔ executor parity (the #13/#21/#37 drift class).

Every native action provider's bundled manifest declares a ``settingsSchema``
whose ``properties`` drive the Triggers UI (ActionConfig) form. If an executor
reads an ``action_config`` key the schema doesn't declare, that key is
UNCONFIGURABLE from the UI — the exact failure behind:
  * #13  webhook empty settingsSchema
  * #21  create-task missing assignee/due/labels
  * #37  bash-action empty settingsSchema (command unconfigurable → every bash
         hook failed "missing 'command' field"); run-prompt/run-workflow missing
         ``dry_run``.

This test statically extracts each executor's ``action_config.get("<key>")`` /
``action_config["<key>"]`` reads and asserts every such key is declared in the
matching manifest's schema. It's intentionally conservative: schema MAY be a
superset (optional fields the executor doesn't always read), but every key the
executor READS must be declared. Keys threaded outside action_config (e.g. bash's
``timeout`` comes from the hook-level field, not action_config) are not read from
action_config and so aren't flagged.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
# apps/native/<provider>-action/app.json  ↔  action_providers/<module>.py
_NATIVE_APPS = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "apps" / "native"
_ACTION_PKG = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "action_providers"
# The webhook action moved to a standalone workspace app (apps/webhook-action) —
# NOT native. Kept in the parity sweep because it was the original drift bug (#13).
_WORKSPACE_APPS = _REPO / "apps"

# provider key → (manifest app.json path, executor .py path). Native providers live
# under apps/native/<key>-action; webhook is a workspace app.
_NATIVE_KEYS = {
    "bash": "bash_provider.py",
    "create-task": "create_task_provider.py",
    "invoke-agent": "invoke_agent_provider.py",
    "notify": "notify_provider.py",
    "run-prompt": "run_prompt_provider.py",
    "run-script": "run_script_provider.py",
    "run-workflow": "run_workflow_provider.py",
    "send-message": "send_message_provider.py",
}


def _provider_paths(key: str) -> tuple[Path, Path]:
    """Return (manifest, executor) paths for a provider key."""
    if key == "webhook":
        if not _WORKSPACE_APPS.is_dir():  # standalone core clone — workspace app absent
            pytest.skip("webhook-action app dir not present (standalone clone)")
        return (_WORKSPACE_APPS / "webhook-action" / "app.json",
                _WORKSPACE_APPS / "webhook-action" / "provider.py")
    return (_NATIVE_APPS / f"{key}-action" / "app.json",
            _ACTION_PKG / _NATIVE_KEYS[key])


_PROVIDERS = {**{k: None for k in _NATIVE_KEYS}, "webhook": None}


def _manifest_props(key: str) -> set[str]:
    path, _ = _provider_paths(key)
    data = json.loads(path.read_text())
    schema = (data.get("provider", data) or {}).get("settingsSchema", {}) or {}
    return set((schema.get("properties") or {}).keys())


def _executor_config_reads(path: Path) -> set[str]:
    """Static-parse the executor for ``action_config.get('k')`` / ``['k']`` /
    ``config.get('k')`` string-literal key reads."""
    tree = ast.parse(path.read_text())
    keys: set[str] = set()

    for node in ast.walk(tree):
        # action_config.get("key") / config.get("key")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("action_config", "config")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        # action_config["key"] subscript
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("action_config", "config")
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


# Keys an executor reads from action_config that are intentionally NOT
# user-configurable form fields (internal/threaded elsewhere). Empty for now —
# add here with a justification if a real exception arises.
_NON_SCHEMA_KEYS: dict[str, set[str]] = {}


@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_executor_reads_are_declared_in_schema(provider: str):
    manifest, executor = _provider_paths(provider)
    if not manifest.exists() or not executor.exists():
        pytest.skip(f"{provider}: manifest or executor missing in this layout")

    declared = _manifest_props(provider)
    reads = _executor_config_reads(executor) - _NON_SCHEMA_KEYS.get(provider, set())

    missing = reads - declared
    assert not missing, (
        f"{provider}-action manifest schema is MISSING keys its executor reads "
        f"from action_config: {sorted(missing)}. Declared: {sorted(declared)}. "
        f"Add them to settingsSchema.properties or the UI can't configure them "
        f"(the #13/#21/#37 schema↔executor drift class)."
    )


def test_bash_action_exposes_command():
    """Direct regression for #37: bash-action MUST declare 'command'."""
    assert "command" in _manifest_props("bash")


def test_run_prompt_and_workflow_expose_dry_run():
    """#37: dry-run replay must be configurable from the Triggers UI."""
    assert "dry_run" in _manifest_props("run-prompt")
    assert "dry_run" in _manifest_props("run-workflow")
