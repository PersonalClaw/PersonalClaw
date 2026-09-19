"""CLI config subcommand — get, set, edit configuration values."""

import argparse
import json
import os
import sys
from pathlib import Path

from personalclaw.apps.secret_fields import (
    mask_bearing_paths,
    mask_secrets_in_document,
    preserve_unchanged_secrets_in_document,
)
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


def _report_withheld(masked_paths: list[str], key: str | None) -> None:
    """Say what was withheld, on STDERR, so the operator is not guessing.

    A redaction the reader cannot see is indistinguishable from a config that has no
    credentials in it — and the operator who needs the real value needs to be told the flag
    exists. STDERR rather than stdout because `config get > f.json` must stay valid JSON; that
    redirect is the documented round-trip and a note inside the document would break it.
    """
    if key is not None:
        # `providers[0].api_key` is under `providers`, so the subtree test has to admit the
        # index bracket as well as the dot separator.
        masked_paths = [p for p in masked_paths if p == key or p.startswith((f"{key}.", f"{key}["))]
    if not masked_paths:
        return
    shown = ", ".join(masked_paths[:6])
    if len(masked_paths) > 6:
        shown += f", … (+{len(masked_paths) - 6} more)"
    print(
        f"ℹ️  {len(masked_paths)} credential field(s) withheld: {shown}",
        file=sys.stderr,
    )
    print(
        "   `personalclaw config get --reveal` prints them; that is also the source to use "
        "for a file you intend to `config set --file` back.",
        file=sys.stderr,
    )


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
        reveal = bool(getattr(args, "reveal", False))
        sel().log_api_access(
            caller="cli",
            operation="config_get_reveal" if reveal else "config_get",
            outcome="allowed",
            source="cli",
            # The KEY, never the value — an audit trail that records the credential it was
            # written to watch over is the leak it is auditing. `--reveal` is a distinct
            # operation so a deliberate disclosure is visible in `personalclaw security events`.
            resources=key or "*",
        )
        # 🔴 …and the merged view is exactly why this must be masked. The blocks it adds are the
        # ones core does NOT model, which is the same set that holds the credentials: `providers`
        # (the only copy of an API key entered in the dashboard) and the legacy `slack` block.
        # #3119 made `config get` show what the file holds, which was right, and printed the keys
        # in it, which was not (#3125). Masked by DEFAULT, because the operator who needs a
        # plaintext config is the rare case and the one reading a terminal is not.
        masked_paths: list[str] = []
        if not reveal:
            d, masked_paths = mask_secrets_in_document(d)
        if not key:
            print(json.dumps(d, indent=2))
            _report_withheld(masked_paths, None)
            return
        val = _dict_get(d, key)
        if val is _MISSING:
            print(f"❌ Unknown key: {key}", file=sys.stderr)
            sys.exit(1)
        if isinstance(val, (dict, list)):
            print(json.dumps(val, indent=2))
        else:
            print(val)
        _report_withheld(masked_paths, key)
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
            try:
                stored = config_loader.read_config_for_merge(p)
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
            # 🔴 THE OTHER HALF OF MASKING, and the reason masking the read alone would have been
            # worse than the leak. `config get > f.json` → edit → `config set --file f.json` is a
            # documented loop, so the file arriving here is usually one `config get` printed — and
            # a masked one NAMES `providers`, which means the merge below (key-level and shallow
            # on purpose) sees the key already present and copies nothing forward. Without this,
            # the round-trip would persist the MASK over the only copy of the API key: a
            # disclosure bug traded for a data-loss bug. A masked field means "keep the stored
            # value", the
            # same rule the dashboard's PATCH has always applied to a round-tripped form.
            data, unresolved = preserve_unchanged_secrets_in_document(data, stored)
            if unresolved:
                # Fail CLOSED. A mask we cannot resolve to a stored value would otherwise be
                # written as the credential itself. `--reveal` is the round-trip source that has
                # no masks to resolve.
                print(
                    "❌ refusing to write config: "
                    f"{len(unresolved)} masked credential field(s) could not be matched to a "
                    f"value in {p.name}, and writing the mask would destroy them: "
                    + ", ".join(unresolved),
                    file=sys.stderr,
                )
                print(
                    "   Use `personalclaw config get --reveal` as the source of a file you "
                    "intend to write back.",
                    file=sys.stderr,
                )
                sel().log_api_access(
                    caller="cli",
                    operation="config_set_file",
                    outcome="denied",
                    source="cli",
                    resources=str(fp),
                )
                sys.exit(1)
            # Same merge as `set <key> <value>`, for the same reason and then one more: the file
            # an operator hands back here can be missing a block `config get` did not print.
            # Replacing the document wholesale would delete `providers[]` by omission on the one
            # path whose whole purpose is restoring a config the operator believes is complete
            # (#951).
            data = config_loader.merge_unmodeled_top_keys(data, stored)
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
            # The same "a mask never reaches disk" rule as `--file`, at the one other place a
            # masked value can arrive: an operator who copied what `config get` printed. Writing
            # it would replace the credential with bullets and report ✅.
            if mask_bearing_paths({key: parsed}):
                print(
                    f"❌ {key}: that is the placeholder `config get` prints for a credential it "
                    "withheld, not a value. Pass the real value, or leave the field alone.",
                    file=sys.stderr,
                )
                sel().log_api_access(
                    caller="cli",
                    operation="config_set",
                    outcome="denied",
                    source="cli",
                    resources=key,
                )
                sys.exit(1)
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
