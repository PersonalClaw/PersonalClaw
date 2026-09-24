---
id: config-four-points
type: ai-coding-rule
statement: >
  A new field on a config dataclass with `_meta` must be wired through all four points:
  the dataclass field (with `_meta`), `AppConfig.load()`'s explicit mapping, `to_dict()`,
  and — if runtime-editable — the `_EDITABLE_CONFIG` PATCH allowlist.
appliesTo:
  - src/personalclaw/config/loader.py
  - src/personalclaw/dashboard/handlers/core.py
requiredTests:
  - tests/test_config_roundtrip.py
scanner: config-four-points
source: >
  The silent-drop gotcha: a field present in a dataclass but omitted from `to_dict()` is
  dropped on save; present in `to_dict()` but omitted from `load()`'s mapping reverts to
  default on reload. Recurs whenever a config field is added in a hurry.
expiry_condition: >
  Never expires while config is a hand-mapped dataclass. Retire only if load()/to_dict()
  become fully reflective (no per-field mapping to forget).
---

# Config fields are four-point contracts

PersonalClaw's config is a set of dataclasses (`config/loader.py`), each field carrying a
`_meta(label, help)` for the settings UI. There is **no reflection** — `AppConfig.load()`
maps JSON to fields *explicitly*, field by field, and `to_dict()` serializes each section.
Miss either half and the field silently misbehaves:

- In the dataclass but **not** in `to_dict()` → the value is dropped every time config is
  saved (the user's setting vanishes on the next write).
- In `to_dict()` but **not** in `load()`'s mapping → the value reverts to its default on
  every reload (the user sets it, it "sticks" until restart, then resets).

## What compliance looks like

For a new field `foo` on `LegibilityConfig` (the worked example the codebase already ships):

1. **Dataclass + `_meta`:** `foo: bool = field(default=True, metadata=_meta("Foo", "…"))`
   on the dataclass in `config/loader.py`.
2. **`load()` mapping:** `AppConfig.load()` reads it explicitly —
   `legibility=LegibilityConfig(foo=bool(legibility_data.get("foo", True)), …)`.
3. **`to_dict()`:** the section is serialized (`"legibility": asdict(self.legibility)`).
4. **`_EDITABLE_CONFIG`** (`dashboard/handlers/core.py`), **only if runtime-editable via
   PATCH:** add the dotted path with its validation spec —
   `"legibility.foo": {"type": "bool"}` — plus a frontend control if user-facing.

`tests/test_config_roundtrip.py` mutates every leaf field and asserts save→load preserves
it, so it catches points 1–3. Point 4 (editability) is a deliberate choice per field —
the scanner check flags a field that looks editable but is missing from the allowlist.
