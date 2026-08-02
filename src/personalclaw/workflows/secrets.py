"""Secrets hygiene for workflow specs (WF2-R14).

A workflow spec is the worst possible place for a credential: it is persisted as
`workflow.json`, copied into every run's `spec.json`, journaled, echoed into the Run
Ledger the flywheel later reads, and rendered in a UI. A token inline in a spec is a
token leaked to all of those at once.

So credentials are never IN a spec — a spec carries `{{secret:KEY}}`, resolved
server-side at dispatch against the credential store (`bindings.py` owns the resolution;
`controller._secret_resolver` is the injected seam). This module owns the three
surrounding disciplines:

* **Presence, not value, on read.** `strip_secrets` replaces a secret-bearing field with
  a boolean `_has*` flag, so a GET can render "an API key is set" without shipping it.
* **Re-injection on write, keyed by node id.** `reinject_secrets` restores stripped
  values from the stored spec by node id — so a mutation that MOVES or COPIES a node
  keeps its credentials, which a path-keyed map would lose the moment the tree changed.
* **A lint for the mistake itself.** `find_inline_secrets` flags credential-shaped
  literals at save time. Catching it here is the only cheap moment: once a spec is saved
  the value is already on disk, and every later defence is damage control.

The journal's `redact()` (defence in depth for secrets arriving via node OUTPUT — a
fetch response echoing a token) lives in `journal.py`, deliberately separate: this
module guards the SPEC seam, that one guards the WRITE seam, and neither can cover the
other.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Config keys whose values are credentials. Matched case-insensitively as a substring,
#: so `api_key`, `openai_api_key` and `apiKey` are all covered by one entry.
SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "private_key",
    "privatekey",
    "auth",
    "bearer",
    # Provider-specific credential shapes that none of the generic hints above match. Measured
    # while wiring the workspace env filter (S49): `GITHUB_PAT` read as NON-secret, so a run
    # declaring inherit-from-host would have passed a GitHub personal access token straight into a
    # leaf subagent's environment. A hint list is only as good as its worst-covered credential, and
    # the ones with bespoke names are exactly the ones a generic list misses.
    "_pat",
    "pat_",
    "session_key",
    "access_key",
    "refresh",
    "signing",
    "webhook",
)

#: Keys that LOOK secret-bearing but hold a reference, not a value. Stripping these would
#: destroy the very indirection that keeps credentials out of the spec.
SECRET_REF_KEYS = frozenset({"credential_ref", "secret_ref", "auth_mode", "auth_type"})

#: `{{secret:KEY}}` — the sanctioned indirection. A field holding one of these is already
#: safe and must NOT be reported as an inline secret.
SECRET_BINDING_RE = re.compile(r"\{\{\s*secret:([A-Za-z0-9_.\-]+)\s*\}\}")

#: Credential-shaped literals. Deliberately narrow — a lint that cries wolf on every long
#: string gets muted, and a muted lint protects nothing.
_INLINE_SECRET_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),  # OpenAI-style
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"),  # Anthropic
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),  # GitHub PAT
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),  # GitHub OAuth
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),  # Hugging Face
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),  # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),  # Google
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
)


def is_secret_key(key: str) -> bool:
    """Does this config key hold a credential VALUE (not a reference)?"""
    low = str(key or "").lower()
    if low in SECRET_REF_KEYS:
        return False
    return any(hint in low for hint in SECRET_KEY_HINTS)


def has_flag_name(key: str) -> str:
    """`api_key` → `_has_api_key`. The presence flag a GET ships instead of the value."""
    return f"_has_{str(key or '').lstrip('_')}"


def is_secret_binding(value: Any) -> bool:
    """True when the value is already the sanctioned `{{secret:KEY}}` indirection."""
    return isinstance(value, str) and bool(SECRET_BINDING_RE.search(value))


# ── strip (on read) ──────────────────────────────────────────────────────────


def strip_secrets(spec: Any) -> Any:
    """Return a copy with secret VALUES replaced by `_has*` presence flags.

    A `{{secret:KEY}}` binding is left INTACT: it holds no value, and stripping it would
    make a round-trip lossy — the client would save back a spec with the indirection
    gone, which is how a working template quietly becomes a broken one.
    """
    if isinstance(spec, dict):
        out: dict[str, Any] = {}
        for key, value in spec.items():
            if is_secret_key(key) and value not in (None, "") and not is_secret_binding(value):
                out[has_flag_name(key)] = True
                continue
            out[key] = strip_secrets(value)
        return out
    if isinstance(spec, list):
        return [strip_secrets(v) for v in spec]
    return spec


# ── re-inject (on write) ─────────────────────────────────────────────────────


def _by_node_id(
    spec: Any, into: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """node id → its config. Keyed by ID rather than path deliberately: a mutation that
    moves or copies a node changes its path, and a path-keyed map would drop the
    credentials of exactly the node the user just edited."""
    acc = {} if into is None else into
    if isinstance(spec, dict):
        node_id = spec.get("id")
        config = spec.get("config")
        if isinstance(node_id, str) and node_id and isinstance(config, dict):
            acc[node_id] = config
        for value in spec.values():
            _by_node_id(value, acc)
    elif isinstance(spec, list):
        for value in spec:
            _by_node_id(value, acc)
    return acc


def reinject_secrets(incoming: Any, stored: Any) -> Any:
    """Restore stripped secrets into `incoming` from `stored`, matched by node id.

    A `_has_<key>: True` flag with no accompanying value means "unchanged — put the
    stored one back". An explicit new value wins. A flag of `False` means "clear it",
    which is how a user removes a credential without a separate endpoint.
    """
    stored_configs = _by_node_id(stored)
    return _reinject(incoming, stored_configs)


def _reinject(node: Any, stored_configs: dict[str, dict[str, Any]]) -> Any:
    if isinstance(node, dict):
        out = {k: _reinject(v, stored_configs) for k, v in node.items()}
        node_id = node.get("id")
        config = out.get("config")
        if isinstance(node_id, str) and node_id and isinstance(config, dict):
            out["config"] = _merge_config(config, stored_configs.get(node_id) or {})
        return out
    if isinstance(node, list):
        return [_reinject(v, stored_configs) for v in node]
    return node


def _merge_config(incoming: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    merged = dict(incoming)
    for flag_key in [k for k in incoming if k.startswith("_has_")]:
        real_key = flag_key[len("_has_") :]
        keep = bool(merged.pop(flag_key))
        if real_key in incoming:
            continue  # an explicit new value was sent; it wins
        if keep and real_key in stored:
            merged[real_key] = stored[real_key]
    return merged


# ── lint (at save) ───────────────────────────────────────────────────────────


@dataclass
class InlineSecret:
    """One flagged literal. `node_id` and `key` locate it; the value is NEVER carried —
    an error message that quotes the credential leaks it into the logs that render it."""

    node_id: str = ""
    key: str = ""
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "key": self.key, "hint": self.hint}


def find_inline_secrets(spec: Any) -> list[InlineSecret]:
    """Flag credential-shaped literals in a spec (WF2-R14 spec lint).

    Two independent signals, because either alone misses real cases: a secret-NAMED key
    holding a literal, and any string matching a known credential shape wherever it sits
    (a token pasted into a prompt is just as leaked as one in an `api_key` field).
    """
    found: list[InlineSecret] = []
    _scan(spec, "", found)
    return found


def _scan(node: Any, node_id: str, found: list[InlineSecret]) -> None:
    if isinstance(node, dict):
        current = node.get("id") if isinstance(node.get("id"), str) else node_id
        for key, value in node.items():
            if isinstance(value, str):
                if is_secret_binding(value):
                    continue  # the sanctioned indirection — not a finding
                if is_secret_key(key) and value.strip():
                    found.append(
                        InlineSecret(
                            node_id=str(current or ""),
                            key=str(key),
                            hint="secret-named field holds a literal; use {{secret:KEY}}",
                        )
                    )
                    continue
                if looks_like_credential(value):
                    found.append(
                        InlineSecret(
                            node_id=str(current or ""),
                            key=str(key),
                            hint="value matches a known credential shape",
                        )
                    )
            else:
                _scan(value, str(current or ""), found)
    elif isinstance(node, list):
        for value in node:
            _scan(value, node_id, found)


def looks_like_credential(text: str) -> bool:
    """Does this string match a known credential shape?

    The ONE list of vendor key shapes in the workflows package — `validator.py`'s
    save-time lint reads it from here rather than keeping a second copy. Recognizing a
    vendor's key SHAPE is secret-DETECTION data, not vendor logic (the same judgment
    `security.py`'s token regexes carry); narrowing it would silently stop catching those
    providers' keys.
    """
    return any(rx.search(text) for rx in _INLINE_SECRET_RES)


def secret_keys_referenced(spec: Any) -> list[str]:
    """Every `{{secret:KEY}}` name a spec depends on.

    This is what a run-start preflight checks against the credential store, so a missing
    credential fails BEFORE tokens are spent rather than mid-run (Slice 6 consumes it).
    """
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            names.update(SECRET_BINDING_RE.findall(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    return sorted(names)
