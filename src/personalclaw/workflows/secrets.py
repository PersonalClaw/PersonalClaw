"""Secrets hygiene for workflow specs.

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
  `service.author_def` runs it on every definition save, because the only read a client
  (the dashboard editor, a chat agent) can edit from is the stripped one; a flag it
  cannot restore is refused there (`unmatched_flags`) rather than written to disk.
* **A lint for the mistake itself.** `find_inline_secrets` flags credential-shaped
  literals at save time. Catching it here is the only cheap moment: once a spec is saved
  the value is already on disk, and every later defence is damage control.

The journal's `redact()` (defence in depth for secrets arriving via node OUTPUT — a
fetch response echoing a token) lives in `journal.py`, deliberately separate: this
module guards the SPEC seam, that one guards the WRITE seam, and neither can cover the
other.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Config keys whose values are credentials. Matched case-insensitively as a substring,
#: so `api_key`, `openai_api_key` and `apiKey` are all covered by one entry. Substring
#: matching is deliberate and stays: `apikey` has no separator to split on, and
#: `ACCESSTOKEN` would stop matching under any token-equality rule — a hint that only
#: matched whole tokens would quietly narrow this list into letting those through.
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
    # while wiring the workspace env filter: `GITHUB_PAT` read as NON-secret, so a run
    # declaring inherit-from-host would have passed a GitHub personal access token straight into a
    # leaf subagent's environment. A hint list is only as good as its worst-covered credential, and
    # the ones with bespoke names are exactly the ones a generic list misses.
    "session_key",
    "access_key",
    "refresh",
    "signing",
    "webhook",
)

#: Hints that mean a WORD, not a character run — matched against the `_`-delimited segments
#: of a name instead of the whole string. Exactly one hint needs this, and it needs it
#: badly: `pat` (a personal access token) was originally spelled as the substring pair
#: `_pat` / `pat_` to hand-roll a boundary, which also matched every `..._PATH`, `_PATTERN`
#: and `COMPAT_...` name. That made `DYLD_LIBRARY_PATH`, `LD_LIBRARY_PATH` and
#: `PKG_CONFIG_PATH` read as credentials, so `mcp_shared.leaf_env` stripped a batch leaf's
#: native-library search path and a native extension failing to load presented as a broken
#: leaf — the exact failure that function's docstring rejects an allowlist to avoid.
#:
#: Only this hint moves. A trailing digit still counts as part of the word so `GITHUB_PAT2`
#: keeps matching, because the collision family is always `pat` followed by a LETTER.
SECRET_KEY_WORD_HINTS = ("pat",)

#: `_`-delimited segments of a name, for `SECRET_KEY_WORD_HINTS`. Split on any non-alphanumeric
#: run so a `github-pat` or `github.pat` config key segments the same way a `GITHUB_PAT` env
#: var does — a separator the author did not think of is not a reason to let a token through.
_WORD_SEP_RE = re.compile(r"[^a-z0-9]+")

#: `pat`, `pat2` — the word plus an optional index, which is how a second account's token
#: gets named.
_WORD_HINT_RES = tuple(re.compile(rf"{re.escape(h)}\d*") for h in SECRET_KEY_WORD_HINTS)

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


def matches_secret_hint(name: str) -> bool:
    """Does this name's SHAPE mark it as credential-bearing?

    The one matcher over the one hint list. `is_secret_key` (spec config keys) and
    `workspace.looks_secret` (env var names) both route through here rather than each
    running their own `any(hint in name)` loop: a second matcher is a second policy, and
    the copy that drifted would be the one deciding whether a token reached a child.

    Two match modes, because the hints are not all the same kind of thing. Most are
    substrings (`api_key` has to match inside `openai_api_key`; `apikey` has no separator
    to split on at all). `pat` is a word — as a character run it matches `PATH`.
    """
    lowered = str(name or "").lower()
    if any(hint in lowered for hint in SECRET_KEY_HINTS):
        return True
    return any(rx.fullmatch(word) for word in _WORD_SEP_RE.split(lowered) for rx in _WORD_HINT_RES)


def is_secret_key(key: str) -> bool:
    """Does this config key hold a credential VALUE (not a reference)?"""
    low = str(key or "").lower()
    if low in SECRET_REF_KEYS:
        return False
    return matches_secret_hint(low)


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

#: The prefix `has_flag_name` gives every presence flag.
FLAG_PREFIX = "_has_"

#: Where a node dict holds its CHILD nodes, in `models.walk`'s order. A child is matched to its
#: stored counterpart on its own, so the walk re-anchors on entering one of these — and only on a
#: NODE: an action's `config.with.body` is request data that happens to share a name with a child
#: position, and re-anchoring there would look a value up on a node that does not exist.
_CHILD_KEYS = ("children", "body", "cases", "default")

#: The anchor for everything outside the node tree — declared inputs, metadata, runtime hints —
#: which is matched by its path in the document.
_DOC: tuple[str, ...] = ("doc",)

Anchor = tuple[str, ...]
RelPath = tuple[Any, ...]


def _is_flag(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(FLAG_PREFIX)


def _node_anchor(node: dict[str, Any], path: str) -> Anchor:
    """A node is found by its id, so a step that MOVES — or is copied into another definition —
    keeps its values; a path-keyed map would drop them for exactly the node the user just edited.
    A node with no id has nothing else to go by, so it falls back to its tree path."""
    node_id = node.get("id")
    return ("id", node_id) if isinstance(node_id, str) and node_id else ("path", path)


def _children(node: dict[str, Any], path: str) -> list[tuple[str, dict[str, Any]]]:
    """Every child node with its tree path, in `models.walk`'s grammar — so a flag this module
    locates names the same step the validator's issues do."""
    out: list[tuple[str, dict[str, Any]]] = []
    kids = node.get("children")
    if isinstance(kids, list):
        out += [(f"{path}.children[{i}]", k) for i, k in enumerate(kids) if isinstance(k, dict)]
    if isinstance(node.get("body"), dict):
        out.append((f"{path}.body", node["body"]))
    cases = node.get("cases")
    if isinstance(cases, dict):
        out += [
            (f"{path}.cases[{label}]", case)
            for label, case in cases.items()
            if isinstance(case, dict) and not _is_flag(label)
        ]
    if isinstance(node.get("default"), dict):
        out.append((f"{path}.default", node["default"]))
    return out


def _index_nodes(node: dict[str, Any], path: str, into: dict[Anchor, Any]) -> None:
    into[_node_anchor(node, path)] = node
    for child_path, child in _children(node, path):
        _index_nodes(child, child_path, into)


def _lookup(anchors: dict[Anchor, Any], anchor: Anchor, rel: RelPath, flag: str) -> Any:
    """The stored key and value `flag` stands for, or ``None``.

    Matched through `has_flag_name` rather than by slicing the prefix off, because the flag is
    built with the key's leading underscores stripped: `_token` and `token` both flag as
    `_has_token`, and a slice would only ever find the second."""
    base = anchors.get(anchor)
    for step in rel:
        if isinstance(base, dict) and step in base:
            base = base[step]
        elif isinstance(base, list) and isinstance(step, int) and 0 <= step < len(base):
            base = base[step]
        else:
            return None
    if not isinstance(base, dict):
        return None
    for key, value in base.items():
        if not _is_flag(key) and has_flag_name(key) == flag:
            return key, value
    return None


def reinject_secrets(incoming: Any, stored: Any) -> Any:
    """Restore the values a read stripped into `incoming`, from the `stored` definition.

    A `_has_<key>: True` flag with no accompanying value means "unchanged — put the stored
    one back". An explicit new value wins. A flag of `False` means "clear it", which is how
    a user removes a credential without a separate endpoint.

    The flag can sit at any depth, because `strip_secrets` strips at any depth — the shipped
    `paper-ingest` hides `config.schema.authors`, two levels below `config` — and outside the
    node tree too (a declared input named like a credential). A value that belongs to a node is
    found through the node (`_node_anchor`) and its place inside that node; anything else by
    its path in the document.

    A flag that finds nothing is LEFT in place rather than dropped: dropping it would lose a
    value the caller asked to keep, and `unmatched_flags` is how the save path finds it and
    refuses the save.
    """
    if not isinstance(incoming, dict):
        return incoming
    anchors: dict[Anchor, Any] = {_DOC: stored if isinstance(stored, dict) else {}}
    if isinstance(stored, dict) and isinstance(stored.get("root"), dict):
        _index_nodes(stored["root"], "root", anchors)
    return _restore(incoming, anchor=_DOC, rel=(), anchors=anchors, node_path=None)


def _restore(
    value: Any,
    *,
    anchor: Anchor,
    rel: RelPath,
    anchors: dict[Anchor, Any],
    node_path: str | None,
) -> Any:
    """`node_path` is set exactly when `value` IS a node (it is that node's tree path)."""
    if isinstance(value, list):
        return [
            _restore(v, anchor=anchor, rel=rel + (i,), anchors=anchors, node_path=None)
            for i, v in enumerate(value)
        ]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        if _is_flag(key):
            continue
        if anchor == _DOC and not rel and key == "root" and isinstance(item, dict):
            out[key] = _restore_node(item, "root", anchors)
        elif node_path is not None and key in _CHILD_KEYS:
            out[key] = _restore_children(key, item, node_path, anchor, anchors)
        else:
            out[key] = _restore(
                item, anchor=anchor, rel=rel + (key,), anchors=anchors, node_path=None
            )
    _restore_flags(value, out, anchor=anchor, rel=rel, anchors=anchors)
    return out


def _restore_node(node: dict[str, Any], path: str, anchors: dict[Anchor, Any]) -> Any:
    return _restore(node, anchor=_node_anchor(node, path), rel=(), anchors=anchors, node_path=path)


def _restore_children(
    key: str, item: Any, path: str, parent: Anchor, anchors: dict[Anchor, Any]
) -> Any:
    if key == "children" and isinstance(item, list):
        return [
            _restore_node(c, f"{path}.children[{i}]", anchors) if isinstance(c, dict) else c
            for i, c in enumerate(item)
        ]
    if key == "cases" and isinstance(item, dict):
        out = {
            label: (
                _restore_node(case, f"{path}.cases[{label}]", anchors)
                if isinstance(case, dict)
                else case
            )
            for label, case in item.items()
            if not _is_flag(label)
        }
        # A case LABEL can itself read like a credential (`auth_failed`), and then the read
        # hid the whole case node: it belongs to the branch, at `cases`.
        _restore_flags(item, out, anchor=parent, rel=("cases",), anchors=anchors)
        return out
    if key in ("body", "default") and isinstance(item, dict):
        return _restore_node(item, f"{path}.{key}", anchors)
    return item


def _restore_flags(
    src: dict[str, Any],
    out: dict[str, Any],
    *,
    anchor: Anchor,
    rel: RelPath,
    anchors: dict[Anchor, Any],
) -> None:
    for flag, keep in src.items():
        if not _is_flag(flag):
            continue
        if any(not _is_flag(k) and has_flag_name(k) == flag for k in src):
            continue  # an explicit new value was sent; it wins
        if not keep:
            continue  # a false flag clears the value
        found = _lookup(anchors, anchor, rel, flag)
        if found is None:
            out[flag] = keep  # left for `unmatched_flags` to report
        else:
            real, stored_value = found
            out[real] = copy.deepcopy(stored_value)


def unmatched_flags(spec: Any) -> list[tuple[str, str, str]]:
    """Every presence flag `reinject_secrets` could not restore, as ``(where, node_id, field)``.

    ``where`` is the node's tree path in `models.walk`'s grammar — the same path the validator's
    issues carry, so a caller can pin the refusal to the step it belongs to — or, outside the node
    tree, the top-level key (``inputs``, ``metadata``). ``field`` is the value's dotted place inside
    it, e.g. ``config.schema.authors``.
    """
    found: list[tuple[str, str, str]] = []
    if not isinstance(spec, dict):
        return found
    for key, value in spec.items():
        if key == "root" and isinstance(value, dict):
            _unmatched_in_node(value, "root", found)
        elif _is_flag(key):
            if value:
                real = str(key[len(FLAG_PREFIX) :])
                found.append((real, "", real))
        else:
            _unmatched_in(value, str(key), "", (key,), found)
    return found


def _unmatched_in_node(node: dict[str, Any], path: str, found: list[tuple[str, str, str]]) -> None:
    node_id = str(node.get("id") or "")
    for key, value in node.items():
        if _is_flag(key):
            if value:
                found.append((path, node_id, _dotted((), key)))
            continue
        if key == "cases" and isinstance(value, dict):
            for label, case in value.items():
                if _is_flag(label) and case:
                    found.append((path, node_id, _dotted(("cases",), label)))
        if key not in _CHILD_KEYS:
            _unmatched_in(value, path, node_id, (key,), found)
    for child_path, child in _children(node, path):
        _unmatched_in_node(child, child_path, found)


def _unmatched_in(
    value: Any, where: str, node_id: str, rel: RelPath, found: list[tuple[str, str, str]]
) -> None:
    if isinstance(value, list):
        for i, item in enumerate(value):
            _unmatched_in(item, where, node_id, rel + (i,), found)
    elif isinstance(value, dict):
        for key, item in value.items():
            if _is_flag(key):
                if item:
                    found.append((where, node_id, _dotted(rel, key)))
            else:
                _unmatched_in(item, where, node_id, rel + (key,), found)


def _dotted(rel: RelPath, flag: str) -> str:
    parts = ""
    for step in rel:
        parts += f"[{step}]" if isinstance(step, int) else (f".{step}" if parts else str(step))
    real = flag[len(FLAG_PREFIX) :]
    return f"{parts}.{real}" if parts else real


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
