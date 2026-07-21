"""Design-system token resolution for the Design loop kind.

PersonalClaw ships a comprehensive default token set (``config/design/default-tokens.json``)
covering every look-and-feel axis. A Design loop records the user's chosen overrides in
``kind_config.token_overrides`` — a partial document with the same shape — and this module
deep-merges them over the defaults and resolves ``{dot.path}`` references into a flat,
literal token tree ready to emit as CSS variables, a Tailwind theme, or a JSON artifact.
"""

from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DESIGN_DIR = Path(__file__).resolve().parent.parent / "config" / "design"
_DEFAULT_TOKENS_PATH = _DESIGN_DIR / "default-tokens.json"
_SCHEMA_PATH = _DESIGN_DIR / "tokens.schema.json"

# A {ref} value is a single token reference filling the whole string, e.g.
# "{color.primitive.brand.500}". References embedded mid-string (gradients,
# transitions) are resolved by substring replacement instead.
_REF_OPEN, _REF_CLOSE = "{", "}"


@functools.lru_cache(maxsize=1)
def default_tokens() -> dict:
    """The canonical bundled default token set (cached). Empty dict on any failure."""
    try:
        return json.loads(_DEFAULT_TOKENS_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("design: could not load default tokens", exc_info=True)
        return {}


@functools.lru_cache(maxsize=1)
def tokens_schema() -> dict:
    """The token JSON schema (cached). Empty dict on any failure."""
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("design: could not load tokens schema", exc_info=True)
        return {}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto a copy of ``base`` (dicts merge key-wise;
    every other value, incl. lists, replaces). Pure — never mutates the inputs."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _lookup(tree: dict, dotted: str):
    """Resolve a dotted path (``color.primitive.brand.500``) against the token tree.
    Returns None if any segment is missing."""
    node = tree
    for seg in dotted.split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return None
    return node


def _resolve_str(value: str, tree: dict, _depth: int = 0) -> str:
    """Resolve ``{dot.path}`` references inside a string against the tree. Handles a
    whole-string ref, embedded refs (gradients/transitions), and chained refs (a role
    pointing at a primitive that is itself a literal). Cycle-guarded by depth."""
    if _REF_OPEN not in value or _depth > 12:
        return value
    out = value
    # Replace each {ref} occurrence with its resolved literal.
    start = out.find(_REF_OPEN)
    while start != -1:
        end = out.find(_REF_CLOSE, start)
        if end == -1:
            break
        path = out[start + 1 : end]
        resolved = _lookup(tree, path)
        if isinstance(resolved, str):
            resolved = _resolve_str(resolved, tree, _depth + 1)
            out = out[:start] + resolved + out[end + 1 :]
            start = out.find(_REF_OPEN, start + len(resolved))
        else:
            # Unresolvable / non-string target: leave the ref literal, move past it.
            start = out.find(_REF_OPEN, end + 1)
    return out


def _resolve_tree(node, root: dict):
    """Deep-resolve every string value in the token tree against the (already merged) root."""
    if isinstance(node, str):
        return _resolve_str(node, root)
    if isinstance(node, dict):
        return {k: _resolve_tree(v, root) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_tree(v, root) for v in node]
    return node


# Ordered magnitude scales whose numeric KEY encodes the value on a fixed convention
# (spacing's 4px grid, the type-size t-shirt ramp). A PARTIAL key-merge of an override
# onto these silently corrupts them: if the override numbers its steps on a different
# convention than the default, the merged scale is non-monotonic and self-contradicting
# (an override 4=0.5rem sitting next to the inherited default 3=0.75rem → step 3 > step 4).
# So when an override supplies one of these scales we REPLACE it wholesale (the override is
# the authoritative full scale per the planner brief), preserving only the default's
# non-value meta keys (e.g. "comment"). Leaf token families (color/radius/family/…) are
# unaffected — those still key-merge so "set only what you change" keeps working.
_REPLACE_SCALE_PATHS = (("spacing",), ("typography", "size"))


def _replace_ordered_scales(merged: dict, override: dict) -> dict:
    """For each magnitude-scale axis the override touches, drop the default's STEP keys
    (those whose value is a dimension) and use the override's scale, keeping only the
    default's non-value meta keys (e.g. ``comment``). Pure."""
    for path in _REPLACE_SCALE_PATHS:
        ov: Any = override
        for seg in path:
            ov = ov.get(seg) if isinstance(ov, dict) else None
        if not isinstance(ov, dict) or not ov:
            continue  # override didn't set this scale — inherit the default unchanged
        # Walk to the scale node in `merged`, building parents as needed.
        node = merged
        for seg in path[:-1]:
            node = node.setdefault(seg, {})
        leaf = path[-1]
        default_scale = _ds if isinstance((_ds := node.get(leaf)), dict) else {}
        # Keep the default's non-value meta keys (comment, etc.), then lay the override's
        # full scale on top — no default step survives to collide with it. Step detection
        # is VALUE-based (is the value a dimension?), so it works for numeric-key scales
        # (spacing: 1/2/4…) AND t-shirt-key scales (typography.size: xs/2xl/3xl…) alike.
        meta = {k: v for k, v in default_scale.items() if not _is_scale_step(v)}
        node[leaf] = {**meta, **ov}
    return merged


# A scale STEP value is a CSS length/number; a META value is anything else (e.g. the
# prose in a "comment" key). Value-based so it catches both numeric-key and named-key
# (t-shirt) scales — a key-based check missed typography.size's xs/2xl/3xl keys, letting
# the default high steps survive a partial redefinition and go non-monotonic (2xl > 3xl).
_DIMENSION_UNITS = ("rem", "px", "em", "%", "vw", "vh", "ch", "vmin", "vmax")


def _is_scale_step(value) -> bool:
    """True when ``value`` is a magnitude-scale step value — a CSS dimension (``1.5rem``,
    ``24px``) or a bare number (``0``). False for prose/meta values, so meta keys survive
    a wholesale scale replacement while every step key is dropped."""
    if isinstance(value, (int, float)):
        return True
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return False
    if v.endswith(_DIMENSION_UNITS):
        return True
    try:
        float(v)  # bare numeric string like "0"
        return True
    except ValueError:
        return False


def resolve(token_overrides: dict | None = None) -> dict:
    """Return the full token tree: defaults deep-merged with ``token_overrides``, then
    every ``{ref}`` resolved to a literal. The Design loop's single source of truth for
    what the design system actually looks like. Ordered magnitude scales (spacing,
    type sizes) are REPLACED wholesale rather than key-merged so a partial override on a
    different numbering convention can't scramble the inherited scale into non-monotonicity."""
    overrides = token_overrides or {}
    merged = deep_merge(default_tokens(), overrides)
    merged = _replace_ordered_scales(merged, overrides)
    return _resolve_tree(merged, merged)


def _flatten(node, prefix: str, out: dict) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "comment":
                continue
            _flatten(v, f"{prefix}-{k}" if prefix else k, out)
    elif isinstance(node, (str, int, float)):
        out[prefix] = node


def to_css_variables(token_overrides: dict | None = None, *, scheme: str = "light") -> str:
    """Emit the resolved tokens as a CSS ``:root`` custom-property block for the given
    scheme (semantic color roles flatten to that scheme's values). Names are the dotted
    path with dots/slashes → dashes, prefixed ``--pc-``."""
    tree = resolve(token_overrides)
    flat: dict = {}
    # Everything except color.semantic (scheme-specific) flattens directly.
    for top, node in tree.items():
        if top == "color":
            prim = node.get("primitive", {}) if isinstance(node, dict) else {}
            _flatten(prim, "color-primitive", flat)
            sem = node.get("semantic", {}) if isinstance(node, dict) else {}
            roles = sem.get(scheme, {}) if isinstance(sem, dict) else {}
            for role, val in roles.items():
                flat[f"color-{role}"] = val
        elif top == "meta":
            continue
        else:
            _flatten(node, top, flat)
    lines = [":root {"]
    for name in sorted(flat):
        css_name = "--pc-" + name.replace(".", "-").replace("/", "-")
        lines.append(f"  {css_name}: {flat[name]};")
    lines.append("}")
    return "\n".join(lines)
