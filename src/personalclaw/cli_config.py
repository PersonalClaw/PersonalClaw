"""CLI config subcommand — get, set, edit configuration values."""

import argparse
import json
import os
import sys
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config import AppConfig
from personalclaw.config.loader import config_path
from personalclaw.hooks import safe_read_file
from personalclaw.sel import sel

_MISSING = object()


def _config_cmd(args: argparse.Namespace) -> None:
    """Get or set config values."""
    action = getattr(args, "config_action", None)
    if action == "get":

        cfg = AppConfig.load()
        d = cfg.to_dict()
        key = getattr(args, "key", None)
        sel().log_api_access(
            caller="cli",
            operation="config_get",
            outcome="allowed",
            source="cli",
            resources=key or "*",
        )
        if not key:
            print(json.dumps(d, indent=2))
            return
        val = _dict_get(d, key)
        if val is _MISSING:
            print(f"❌ Unknown key: {key}", file=sys.stderr)
            sys.exit(1)
        if isinstance(val, (dict, list)):
            print(json.dumps(val, indent=2))
        else:
            print(val)
    elif action == "set":

        file_path = getattr(args, "file", None)
        if file_path:
            fp = Path(file_path).expanduser().resolve()

            try:
                data = json.loads(safe_read_file(str(fp)))
            except PermissionError as e:
                print(f"❌ {e}", file=sys.stderr)
                sys.exit(1)
            except (json.JSONDecodeError, OSError) as e:
                print(f"❌ Invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)
            atomic_write(config_path(), json.dumps(data, indent=2) + "\n")
            sel().log_api_access(
                caller="cli",
                operation="config_set_file",
                outcome="allowed",
                source="cli",
                resources=str(fp),
            )
            print(f"✅ Config loaded from {file_path}")
        else:
            key = args.key
            value = args.value
            if not key or value is None:
                print("Usage: personalclaw config set <key> <value>", file=sys.stderr)
                print("       personalclaw config set --file <path.json>", file=sys.stderr)
                sys.exit(1)
            cfg = AppConfig.load()
            d = cfg.to_dict()
            parsed = _parse_value(value)
            # The dashboard's PATCH allowlist declares a type and bounds for 192 of these
            # keys. This path used to check only that the dotted key EXISTS, so the CLI
            # could write `agent.max_subagents 9999` past the 0..16 the API enforces on the
            # same field, and the next gateway start would read a number no UI could have
            # produced. Keys the allowlist does not declare keep today's behaviour: the
            # allowlist is the PATCH surface, not a complete config schema, and refusing
            # everything absent from it would break `config set` for most of the file.
            spec = _editable_spec(key)
            if spec is not None:
                from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value

                try:
                    parsed = coerce_edit_value(key, parsed, spec)
                except ConfigValueError as exc:
                    print(f"❌ {key}: {exc}", file=sys.stderr)
                    sel().log_api_access(
                        caller="cli",
                        operation="config_set",
                        outcome="denied",
                        source="cli",
                        resources=exc.resources or f"{key}={value}",
                    )
                    sys.exit(1)
            if not _dict_set(d, key, parsed):
                print(f"❌ Unknown key: {key}", file=sys.stderr)
                sys.exit(1)
            atomic_write(config_path(), json.dumps(d, indent=2) + "\n")
            sel().log_api_access(
                caller="cli",
                operation="config_set",
                outcome="allowed",
                source="cli",
                resources=f"{key}={json.dumps(parsed)}",
            )
            print(f"✅ {key} = {json.dumps(parsed)}")
    elif action == "edit":

        p = config_path()
        if not p.exists():
            cfg = AppConfig()
            cfg.save()
            print(f"Created default config: {p}")
        sel().log_api_access(
            caller="cli",
            operation="config_edit",
            outcome="allowed",
            source="cli",
            resources=str(p),
        )
        editor = os.environ.get("EDITOR", "vi")
        os.execvp(editor, [editor, str(p)])
    else:
        print("Usage: personalclaw config {get,set,edit}", file=sys.stderr)
        sys.exit(1)


def _editable_spec(key: str) -> dict | None:
    """The PATCH allowlist's spec for a dotted key, or None if it declares none.

    Imported lazily: the registry lives in a dashboard handler module (the inert-surface
    census parses that file for the `_EDITABLE_CONFIG` literal, so it cannot move), and
    `personalclaw config get` should not pay for importing aiohttp. A failure to import is
    not a reason to refuse a write — it means no spec is available, which is exactly the
    "key not declared" case.
    """
    try:
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        return _EDITABLE_CONFIG.get(key)
    except Exception:  # noqa: BLE001 — no spec available is the same as no spec declared
        return None


def _dict_get(d: dict, key: str) -> object:
    """Get a value from a nested dict using dot-separated key."""
    parts = key.split(".")
    cur: object = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return cur


def _dict_set(d: dict, key: str, value: object) -> bool:
    """Set a value in a nested dict using dot-separated key. Returns False if parent missing."""
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    if not isinstance(cur, dict):
        return False
    if parts[-1] not in cur:
        return False
    cur[parts[-1]] = value
    return True


def _parse_value(raw: str) -> object:
    """Parse a CLI value string into the appropriate Python type."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    return raw
