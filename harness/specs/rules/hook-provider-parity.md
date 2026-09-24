---
id: hook-provider-parity
type: ai-coding-rule
statement: >
  Every action provider registered via `register_action_provider` must appear in the
  `ALLOWED_HOOK_PROVIDERS` frozenset, or hook create/update validation rejects any hook
  that uses it.
appliesTo:
  - src/personalclaw/action_providers/registry.py
  - src/personalclaw/validation.py
requiredTests:
  - tests/test_native_hook_providers.py::test_hook_provider_allowlist_includes_all_action_providers
scanner: hook-provider-parity
source: >
  A provider can be registered in the registry yet absent from the validation allowlist,
  so hooks referencing it fail create/update with a confusing "unknown provider" error
  even though the executor exists.
expiry_condition: >
  Retire if the allowlist is derived from the registry at import time (single source of
  truth) instead of being a hand-maintained frozenset.
---

# Action providers must be in the hook allowlist

Two structures must agree: the action-provider **registry**
(`action_providers/registry.py` — `register_action_provider`, `list_action_providers`)
and the **validation allowlist** `ALLOWED_HOOK_PROVIDERS` (a `frozenset` in
`validation.py`, consumed by `HOOK_CREATE_SCHEMA`/`HOOK_UPDATE_SCHEMA`). The allowlist is
hand-maintained; the registry is where providers actually register.

## What compliance looks like

When you add a new action provider, add its name to `ALLOWED_HOOK_PROVIDERS` in the same
change. The proof is `test_hook_provider_allowlist_includes_all_action_providers`
(`tests/test_native_hook_providers.py`), which asserts
`set(list_action_providers()) - set(ALLOWED_HOOK_PROVIDERS)` is empty.

**Note (premise correction, 2026-07-26):** this invariant is *not* checked by
`tests/test_action_schema_executor_parity.py` — that test guards a different invariant
(an executor's `action_config` reads ⊆ its `app.json` `settingsSchema`). Cite the
`test_native_hook_providers.py` node-id above, not the parity test.
