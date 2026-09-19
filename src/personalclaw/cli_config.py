"""CLI config subcommand — get, set, edit configuration values."""

import argparse
import json
import os
import sys
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.hooks import safe_read_file
from personalclaw.sel import sel


def config_path() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


_MISSING = object()


def _merged_view(cfg: AppConfig) -> dict:
    """*cfg* as a dict, plus every top-level key of the on-disk file it does not model.

    READ-ONLY companion to the write paths' merge. An unreadable config.json degrades to the
    model view here rather than refusing: `AppConfig.load()` has already handled that file
    (warned and fallen back to defaults), and a `config get` that errors on a corrupt file tells
    the operator less than one that shows what the loader actually resolved. That is the opposite
    of the write case, where a failed read means the damage is unknowable — see
    :func:`personalclaw.config.loader.read_config_for_merge`.
    """
    d = cfg.to_dict()
    try:
        existing = config_loader.read_config_for_merge(config_path())
    except config_loader.ConfigPreserveError:
        return d
    return config_loader.merge_unmodeled_top_keys(d, existing)


def _config_cmd(args: argparse.Namespace) -> None:
    """Get or set config values."""
    action = getattr(args, "config_action", None)
    if action == "get":

        cfg = AppConfig.load()
        # 🔴 The MERGED view, not the model snapshot. `to_dict()` names only the ~40 sections the
        # dataclass models, so a bare `config get providers` answered "❌ Unknown key" for a block
        # sitting in the file — and `config get` with no key printed a document missing it, which
        # made the documented `config get > f.json` → edit → `config set --file f.json` loop a
        # provider-deleting round-trip. #3103 fixed the `config set <key> <value>` write and left
        # both of these, so the round-trip still deleted `providers` at ✅ exit 0 (#951). A display
        # path must show what the file holds.
        d = _merged_view(cfg)
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
            if not isinstance(data, dict):
                print("❌ Invalid JSON: config must be a JSON object", file=sys.stderr)
                sys.exit(1)
            p = config_path()
            # Same merge as `set <key> <value>`, for the same reason and then one more: `config
            # get` could not even PRINT the unmodeled blocks, so the file an operator hands back
            # here is SYSTEMATICALLY missing them. Replacing the document wholesale would delete
            # `providers[]` by omission on the one path whose whole purpose is restoring a config
            # the operator believes is complete (#951).
            try:
                data = config_loader.merge_unmodeled_top_keys(
                    data, config_loader.read_config_for_merge(p)
                )
            except config_loader.ConfigPreserveError as exc:
                # Refusing beats writing blind: a config whose content cannot be read is exactly
                # the case where we cannot know what the write would destroy.
                print(f"❌ {exc}", file=sys.stderr)
                sel().log_api_access(
                    caller="cli",
                    operation="config_set_file",
                    outcome="error",
                    source="cli",
                    resources=str(fp),
                )
                sys.exit(1)
            atomic_write(p, json.dumps(data, indent=2) + "\n")
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
            # The MODEL dict answers "is this a real key?" — every section is materialised
            # there, so a leaf the operator has never written still resolves.
            if _dict_get(d, key) is _MISSING:
                print(f"❌ Unknown key: {key}", file=sys.stderr)
                sys.exit(1)
            # 🔴 …but the write lands on the RAW document, not on `d`. `d` is
            # `AppConfig.to_dict()`, a fixed literal of the 40-odd sections the loader models,
            # and serialising it over config.json OMITTED every top-level key that literal
            # does not name: `providers`, `use_cases`, `slack`, `meta`. `providers` is the
            # canonical store for model-provider instances and, for `openai_compatible`, the
            # only copy of an API key entered in the dashboard — so one
            # `config set agent.log_level DEBUG` destroyed ten instances and their keys, and
            # printed ✅ (#951). The blocks were not emptied, they were never serialised.
            #
            # Read → apply one field → write the merged document is what the dashboard PATCH
            # already does. Doing it here too is what makes the two write paths agree; the
            # divergence is why the API got fixed while the CLI stayed destructive.
            p = config_path()
            try:
                doc = config_loader.read_config_for_merge(p)
            except config_loader.ConfigPreserveError as exc:
                # Absent is safe to write over, unreadable is not — the rule `AppConfig.save()`
                # already enforces, now stated once in the loader and shared by all three writes.
                print(f"❌ {exc}", file=sys.stderr)
                sel().log_api_access(
                    caller="cli",
                    operation="config_set",
                    outcome="error",
                    source="cli",
                    resources=f"{key}={value}",
                )
                sys.exit(1)
            _dict_put(doc, key, parsed)
            atomic_write(p, json.dumps(doc, indent=2) + "\n")
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


def _dict_put(d: dict, key: str, value: object) -> None:
    """Set a dot-separated key in `d`, creating the objects the path needs on the way.

    Deliberately NOT the "refuse a missing parent" setter this replaced. That refusal was the
    Unknown-key check, and it belongs on `AppConfig.to_dict()`, where every section is
    materialised — not on the raw config.json, where a section the operator has never touched
    is legitimately absent. Conflating the two is what forced the write to serialise the model
    dict, which is what deleted `providers` (#951). The caller has already resolved `key`
    against the model, so the path is known-good by the time it gets here.
    """
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            # The model says this path is an object, so whatever non-object the file holds
            # here is not config the loader could read. Same call the dashboard PATCH makes.
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


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
