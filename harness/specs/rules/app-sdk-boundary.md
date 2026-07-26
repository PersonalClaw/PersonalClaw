---
id: app-sdk-boundary
type: ai-coding-rule
statement: >
  Code under the repo-root `apps/` bundles may import core only via `personalclaw.sdk.*`
  — never a deep `personalclaw.<internal>` import. The SDK facade is the only supported
  surface between removable app bundles and the provider-agnostic core.
appliesTo:
  - apps/**/*.py
requiredTests:
  - tests/test_apps_import_boundary.py::test_apps_only_import_sdk
  - tests/test_apps_import_boundary.py::test_each_app_file_sdk_clean
scanner: app-sdk-boundary
source: >
  The provider-agnostic-core tenet: apps must be removable and must not couple to core
  internals. A deep import silently binds an app to a private module that can change or
  move, breaking the boundary the whole app platform rests on.
expiry_condition: never (this is a load-bearing architectural tenet, not a bug patch).
---

# Apps import core only through the SDK facade

The single most load-bearing boundary in the codebase: first-party and third-party app
bundles under `apps/` may reach core **only** through `personalclaw.sdk.*`
(`sdk.net`, `sdk.security`, and the other facade modules). A deep import like
`from personalclaw.loop.worktree import …` couples the app to a private internal that is
free to move — exactly what the boundary exists to prevent.

## What compliance looks like

Inside an app: `from personalclaw.sdk import net, security` (or the specific facade
symbol). If the SDK doesn't expose what the app needs, that is a gap to fill in the SDK
(a deliberate, reviewed addition) — never a reason to reach past it.

The deliberate in-core exceptions (things that legitimately live in core despite looking
vendor-ish) are enumerated in `docs/architecture/provider-boundary.md`; do not add to
them. `tests/test_apps_import_boundary.py` enforces this per app file; the scanner check
`app-sdk-boundary` promotes it to a diff-time check so a bad import is caught before the
test run.
