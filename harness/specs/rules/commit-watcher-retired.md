---
id: commit-watcher-retired
type: ai-coding-rule
statement: >
  The interim self-QA commit-watcher cron script (`selfqa/scripts/selfqa_commit_watch.py`)
  is retired and must not come back. Now that the `vcs` file-watch trigger kind exists, the
  bundled `self-qa` template watches `.git/refs/heads/*` through the AUTOMATION-SUBSTRATE vcs
  preset (a `file`-kind trigger) — no standalone cron script ships, and the installer must
  never re-materialize one.
appliesTo:
  - src/personalclaw/selfqa/**
source: >
  SV-11 (SELF-VERIFICATION §6 retirement). AUTO-R12 gave the engine a first-class `vcs`
  trigger kind, so the interim `selfqa_commit_watch.py` cron script that predated it was
  deleted and the `self-qa` template rebound to the vcs preset. A re-added script — or an
  installer that re-materializes one — would resurrect the exact "present but superseded"
  out-of-band control the retirement removed, the inert-duplicate shape this harness exists
  to catch. This rule is the harness-native invariant the retirement always promised, so the
  migration is policed by the harness rather than only by a passing test.
requiredTests:
  - tests/test_selfqa_companion.py::TestTheInterimScriptStaysRetired::test_the_vcs_trigger_kind_exists
  - tests/test_selfqa_companion.py::TestTheInterimScriptStaysRetired::test_no_commit_watch_script_ships
  - tests/test_selfqa_companion.py::TestTheInterimScriptStaysRetired::test_install_no_longer_materializes_scripts
expiry_condition: never (the retirement is permanent; the vcs preset is the sole commit watcher).
---

# The self-QA commit-watcher cron script stays retired

The self-QA companion once shipped a standalone cron script,
`src/personalclaw/selfqa/scripts/selfqa_commit_watch.py`, to fire on a new commit. That was
an **interim** stand-in from before the engine had a real version-control trigger. AUTO-R12
(AUTOMATION-SUBSTRATE) added a first-class `vcs` file-watch trigger kind, so the interim
script was deleted and the bundled `self-qa` template was rebound to the vcs preset — a
`file`-kind trigger whose spec watches `.git/refs/heads/*` with content dedup. There is now
exactly one commit watcher, and it is the engine's own trigger, not an out-of-band script.

Re-introducing the script (or an installer path that writes one to disk) would recreate the
duplicate, superseded control the retirement removed — the same "an inert thing sits beside
the real thing" shape the harness exists to prevent.

## What compliance looks like

- No `selfqa_commit_watch*` file ships anywhere under `src/` (no `src/personalclaw/selfqa/scripts/`
  directory materializing one).
- `selfqa/install.py`'s `reconcile()` binds the template's trigger to `kind="file"` with the
  AUTOMATION-SUBSTRATE vcs preset (`paths=vcs_patterns(...)`, `dedup="content"`), swapping the
  kind in place on the stable id `system:selfqa-commit-watch` — it never materializes a cron
  script.
- The retired filename may appear only in a cleanup/retirement allowlist (e.g. a
  `_RETIRED_CRON_FILES` tuple) or in the tests that assert its absence — never as a shipped,
  importable, or installer-emitted module.

The three `requiredTests` above pin this: the vcs trigger kind exists, no commit-watch script
ships under `src/`, and the installer no longer materializes scripts. If any regresses, the
rule's `harness validate`/`run` fails with this file named, so the migration is enforced as a
versioned invariant rather than resting on a test nobody is pointed at.
