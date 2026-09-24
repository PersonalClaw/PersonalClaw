"""CLI config subcommand — get, set, edit configuration values."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

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


def _refuse(operation: str, resources: str, message: str, outcome: str = "error") -> NoReturn:
    """Print *message*, audit the refusal, and exit 1 — the shape every refusal here shares.

    Written once because the print/audit/exit triple was copied at five sites and the audit row is
    the part a sixth would forget: an unaudited silent refusal is indistinguishable from a working
    write, which is the defect family this whole module is scar tissue from. `NoReturn` so a caller
    cannot read the line after it as reachable.
    """
    print(message, file=sys.stderr)
    sel().log_api_access(
        caller="cli",
        operation=operation,
        outcome=outcome,
        source="cli",
        # The KEY or the path, never a value. An audit row that quoted the credential it was
        # written to watch over is the leak it is auditing.
        resources=resources,
    )
    sys.exit(1)


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
                _refuse("config_set_file", str(fp), f"❌ {exc}")
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
                _refuse(
                    "config_set_file",
                    str(fp),
                    "❌ refusing to write config: "
                    f"{len(unresolved)} masked credential field(s) could not be matched to a "
                    f"value in {p.name}, and writing the mask would destroy them: "
                    + ", ".join(unresolved)
                    + "\n   Use `personalclaw config get --reveal` as the source of a file you "
                    "intend to write back.",
                    outcome="denied",
                )
            # 🔴 OMISSION CANNOT MEAN REMOVAL HERE, SO IT MUST NOT READ AS SUCCESS. The merge
            # below copies every top-level block this document does not name forward, which is
            # what stops a handed-back file deleting `providers[]` by omission (#951) — and is
            # exactly why a block the operator DELETED on purpose survived at `✅` exit 0 (#3125).
            # One signal cannot mean both "leave alone" and "delete", so this path keeps
            # preservation and stops pretending: the write is refused, the blocks it could not
            # apply are named, and `config unset` is the verb that removes one. Nonzero, because
            # an operator who removed a credential and was told ✅ still has the secret on disk.
            #
            # AFTER the mask check above, deliberately. An unresolvable mask is the fail-closed
            # case — it would destroy the only copy of a credential — so it earns the more
            # specific message when a document manages to be wrong in both ways at once.
            dropped = sorted(k for k in stored if k not in data)
            if dropped:
                _refuse(
                    "config_set_file",
                    str(fp),
                    f"❌ refusing to write config: {len(dropped)} top-level block(s) in {p.name} "
                    f"are missing from {fp.name}, and this path preserves blocks it is not shown "
                    "rather than deleting them: " + ", ".join(dropped) + "\n   To remove one, run "
                    f"`personalclaw config unset {dropped[0]}`. To apply the rest of this file, "
                    "put the block back.",
                    outcome="denied",
                )
            # Belt and braces, kept on purpose rather than deleted as unreachable: the refusal
            # above makes this a no-op only for as long as its comparison stays exactly as wide as
            # this merge's. #951 is the bug where that invariant was held in one place and broken
            # in another, so the guarantee is stated twice and the census in
            # `test_config_file_roundtrip_preserves_unmodeled_blocks.py` reads this line.
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
                _refuse("config_set", f"{key}={value}", f"❌ {exc}")
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
    elif action == "unset":

        # 🔴 THE ESCAPE HATCH REMOVAL NEVER HAD. `config` shipped `{get,set,edit}`, and no spelling
        # of removal existed anywhere: `unset`, `--replace` and `--remove` all grepped to zero. So
        # the only documented way to hand back an edited config — `config get > f.json` → edit →
        # `config set --file f.json` — could not express "delete this", and answered `✅` while a
        # `bot_token` the operator had removed stayed on disk (#3125).
        #
        # Deliberately ONE verb rather than a `--prune` flag on `--file` as well: two spellings of
        # removal is the shape of bug this file already carries scars from (two config readers that
        # disagreed on the exit contract). `--file` preserves and refuses; `unset` removes.
        key = args.key
        p = config_path()
        try:
            doc = config_loader.read_config_for_merge(p)
        except config_loader.ConfigPreserveError as exc:
            # Same rule as every other write: unreadable means the damage is unknowable.
            _refuse("config_unset", key, f"❌ {exc}")
        # A key that is not in the file is REFUSED, not shrugged off. `config unset slak` answering
        # success while `slack` survives is the same false-success defect this verb exists to end —
        # and the one where being wrong leaves a credential on disk.
        if _dict_pop(doc, key) is _MISSING:
            _refuse(
                "config_unset",
                key,
                f"❌ Not set in {p.name}: {key}\n"
                "   `personalclaw config get` shows what the file holds. A modelled key absent "
                "from the file is already at its default.",
                outcome="denied",
            )
        atomic_write(p, json.dumps(doc, indent=2) + "\n")
        sel().log_api_access(
            caller="cli",
            operation="config_unset",
            outcome="allowed",
            source="cli",
            # The KEY only. An unset whose audit row quoted the credential it removed would
            # preserve in the log exactly what the operator asked to be rid of.
            resources=key,
        )
        print(f"✅ Removed {key}")
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
        print("Usage: personalclaw config {get,set,unset,edit}", file=sys.stderr)
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


def _dict_pop(d: dict, key: str) -> object:
    """Remove the dot-separated *key* from *d*, returning its value or :data:`_MISSING`.

    Reads the RAW config document, not `AppConfig.to_dict()` — the opposite of the Unknown-key
    check `config set` makes. Every modelled section is materialised in the model dict, so
    resolving there would report a key as removable that the file has never held, and the write
    would rewrite the document unchanged at `✅`. "Present in the file" is the only question that
    has an answer here, because the file is what is being edited.

    Parents are left in place when they empty out. An empty `{}` section is what the loader reads
    as "every field at its default", which is the state `unset` is asked to produce; pruning the
    parent as well would turn one removal into a second, unasked-for one.
    """
    parts = key.split(".")
    cur: object = d
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return _MISSING
    return cur.pop(parts[-1])


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
