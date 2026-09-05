"""The ONE validator for a declared config/settings schema.

Two implementations of the same JSON-Schema subset used to exist side by side, and they had
already drifted:

* ``apps.app_config.validate_config`` — an app manifest's ``configSchema``.
* ``providers.settings.ProviderSettings.validate`` — a provider manifest's ``settingsSchema``.

Which one matters is a measured question, and the answer is the opposite of where #616 points.
Across every shipped manifest: **0 of 30 core natives and 0 of 54 first-party apps declare a
``configSchema``, while 64 declare a provider ``settingsSchema``** (9 of those declare
``required``). So the reachable validator was the provider copy — and it enforced **no bound at
all** and accepted ``True`` for an ``integer``, because ``bool`` is an ``int`` subclass.

That is not an author trap in the abstract; two shipped apps declare bounds the platform
ignored. Measured through the real write path before this module existed::

    native-vector-memory  confidence_threshold  number, minimum 0.0, maximum 1.0
        validate({"confidence_threshold": 5})     -> []      # and -1, and True
    browse-action         max_steps             integer, minimum 1
        validate({"max_steps": 0})                -> []      # and -5, and True

This module owns the per-property rules, so they cannot drift again. It deliberately does NOT
own the two OBJECT-level policies, which differ on purpose and stay documented at their call
sites:

* an app with no ``configSchema`` takes no config at all (an empty schema rejects a non-empty
  object), while a provider with no ``settingsSchema`` is simply unvalidated;
* the app path REFUSES an unknown key, while the provider path IGNORES one, so a stored config
  carrying a key from an older manifest still loads.

Messages name the field's ``x-meta.label``, falling back to the key. That is the same string
both forms already render as the row's label (``meta.label || key`` in ``appConfigForm.tsx`` and
``ProviderConfigForm.tsx``), so a refusal points at the control the user is looking at instead
of sending them into a manifest to decode a raw key (#491).
"""

from __future__ import annotations

import logging
import re
from typing import Any, TypeGuard

logger = logging.getLogger(__name__)

#: Every keyword this validator enforces. Named because a declared keyword the platform
#: silently ignores is the whole defect (#616): an author who writes one has been misled. A rail
#: gives each entry a violating value, so a keyword cannot be listed without being implemented.
ENFORCED_KEYWORDS: tuple[str, ...] = (
    "type",
    "enum",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
)

#: JSON-Schema ``type`` → the Python types accepted for it.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}

#: ``keyword -> (phrasing, comparison)``. One table so a bound cannot be enforced without a
#: message, or worded in one place and compared in another.
_NUMERIC_BOUNDS: tuple[tuple[str, str, Any], ...] = (
    ("minimum", "at least", lambda v, b: v >= b),
    ("maximum", "at most", lambda v, b: v <= b),
    ("exclusiveMinimum", "greater than", lambda v, b: v > b),
    ("exclusiveMaximum", "less than", lambda v, b: v < b),
)


def _is_number(v: Any) -> TypeGuard[float]:
    """A real number, not a boolean — the same ``bool``-is-an-``int`` trap, on the SCHEMA side."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v: Any) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def field_label(spec: Any, key: str) -> str:
    """The human label for a property, falling back to its key."""
    if isinstance(spec, dict):
        meta = spec.get("x-meta")
        if isinstance(meta, dict):
            label = meta.get("label")
            if isinstance(label, str) and label.strip():
                return label
    return key


def validate_properties(
    values: dict[str, Any], properties: dict[str, Any], required: Any = ()
) -> list[str]:
    """Per-property validation shared by both config paths. Returns human-readable errors.

    Only PRESENT values are checked; a missing key is either required (reported once) or simply
    absent. A value that failed its type check is not then measured against a bound, because
    "must be a number" and "must be at least 3" for one field is one fault reported twice.
    """
    errors: list[str] = []
    req = required if isinstance(required, (list, tuple, set)) else ()

    for key in req:
        if key not in values:
            errors.append(f"Missing required field: {field_label(properties.get(key), key)}")

    for key, value in values.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        label = field_label(spec, key)

        expected = spec.get("type")
        typed_ok = True
        if isinstance(expected, str) and expected in _TYPE_MAP:
            article = "an" if expected[0] in "aeiou" else "a"
            if expected in ("number", "integer") and isinstance(value, bool):
                # `bool` is an `int` subclass, so a bare isinstance check accepts True for an
                # integer — measured live on the provider path, which stored a boolean into a
                # numeric field without complaint.
                errors.append(f"{label}: must be {article} {expected}, not a boolean")
                typed_ok = False
            elif not isinstance(value, _TYPE_MAP[expected]):
                errors.append(f"{label}: must be {article} {expected}")
                typed_ok = False

        enum = spec.get("enum")
        if isinstance(enum, list) and enum and value not in enum:
            errors.append(f"{label}: must be one of {enum}")

        if not typed_ok:
            continue

        if _is_number(value):
            for keyword, phrasing, holds in _NUMERIC_BOUNDS:
                bound = spec.get(keyword)
                if _is_number(bound) and not holds(value, bound):
                    errors.append(f"{label}: must be {phrasing} {bound}")

        if isinstance(value, str):
            min_len = spec.get("minLength")
            if _is_int(min_len) and len(value) < min_len:
                errors.append(f"{label}: must be at least {min_len} characters")
            max_len = spec.get("maxLength")
            if _is_int(max_len) and len(value) > max_len:
                errors.append(f"{label}: must be at most {max_len} characters")
            pattern = spec.get("pattern")
            if isinstance(pattern, str) and pattern:
                try:
                    # Unanchored, per JSON Schema semantics.
                    matched = re.search(pattern, value) is not None
                except re.error:
                    # A broken regex is the MANIFEST author's defect, not this user's input.
                    # Refusing every value would wall the user off behind a bug only the author
                    # can fix, so the constraint is skipped and the siblings still apply.
                    logger.warning(
                        "property %r declares an invalid pattern %r; skipping", key, pattern
                    )
                    matched = True
                if not matched:
                    errors.append(f"{label}: does not match the required format")

    return errors
