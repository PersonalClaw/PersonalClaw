# OSS-OPERATIONS — atomic plans

**Source plan:** [`OSS-OPERATIONS`](../plans/OSS-OPERATIONS.md)  
**Code:** `OO`  
**Source status:** done

OSS-OPERATIONS is fully DONE; decomposed into 7 done atoms (OO-1..OO-7) along feature/PR seams. Two tasks are permanently owner-descoped (T2.4 Discord, T3.2 continuity) and excluded; five non-code OWNER real-world items (Sponsors, App Dev category, welcome post, USPTO screen, CoC contact) are noted but not cut as engineering atoms. EXT edges: OO-1→CI-RELEASE (dco job into ci.yml), OO-6→CI-RELEASE (release.yml) + DISTRIBUTION (install one-liner). Plan file: /Users/golani/PersonalProjects/PersonalClaw/PersonalClaw/docs/roadmap/plans/OSS-OPERATIONS.md

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `OO-1` | ✅ | Core repo: stated model + hygiene set + DCO check + AGENTS.md | `EXT:CI-RELEASE:both ci.yml workflow files exist so the dco job can be added into them` | CONTRIBUTING has model+intake+DCO sections (no contradiction with the CONTRIBUTING breaking-changes section); bug/feature.yml + PR template render; setup_labels.sh idempotent; CODEOWNERS + CoC 2.1 present; unsigned scratch-PR commit fails the dco job, signed passes; an agent reading only AGENTS.md can run lint/test/build and knows the rejection list |
| `OO-2` | ✅ | Apps repo: hygiene mirror + front-door CONTRIBUTING | `OO-1` | apps repo has bug/feature/app-request.yml + PR template rendering; CONTRIBUTING front-door bar documented; CODEOWNERS+CoC+AGENTS.md present; dco job on apps ci.yml; manifest-validate green (38 manifests) |
| `OO-3` | ✅ | SKILL.md compatibility doc + parser consolidation (T2.1) | — | docs/reference/skill-format.md matches probed parser behavior; vanilla name+description SKILL.md imports cleanly and triggers is genuinely optional; test_skill_format_compat.py (20 cases) green; all callers delegate to the single parser; make lint + make test green with zero pre-existing skill-test assertion edits |
| `OO-4` | ✅ | Seed good-first-issues + run setup_labels.sh on both repos (T2.2) | `OO-1`, `OO-3` | >=8 good-first-issues live with the good-first-issue label; setup_labels.sh run on both repos (16 labels each); stale/already-fixed seeds verified and closed-with-evidence rather than filed (croniter DST, video kind, xfail list, --slack-only) |
| `OO-5` | ✅ | Structure Discussions + README community routing + roadmap-intake wiring (T2.3, T3.3) | — | README community block routes by category + good-first-issue front door; roadmap.md carries the maintainer-ownership-as-process intake paragraph with a resolving link; discussions-welcome-draft.md drafted (awaiting owner approval; categories are web-UI-only per GraphQL schema, so 'App Dev'/'Roadmap Input' remain optional owner actions) |
| `OO-6` | ✅ | Maintainer release runbook (T3.1) | `EXT:CI-RELEASE:release.yml jobs + protected environments (release, release-client)`, `EXT:DISTRIBUTION:install one-liner + compose pull for the post-release verification checklist` | a person with org access can cut a release from the doc alone; documents all release.yml jobs, both protected environments, the six version surfaces, the annotated-tag dereference trap, and the '## [X.Y.Z]' CHANGELOG-heading requirement; validated against v0.1.3 (GHCR pull step flagged as the one hand-confirm item) |
| `OO-7` | ✅ | README version-drift guard (version-consistency hardening) | — | README banner shows the current release version; test_version_consistency.py guards the README banner and acp/client.py CLIENT_VERSION (both mutation-verified to fail naming the file to fix); runbook bump table lists all six surfaces |

## Atom scopes

### `OO-1` — Core repo: stated model + hygiene set + DCO check + AGENTS.md

**Status:** done

Session 1 — Stated model + hygiene set (T1.1 CONTRIBUTING 'The model'+intake+DCO explainer; T1.2 issue/PR templates + setup_labels.sh; T1.4 CODEOWNERS+CoC (FUNDING deliberately omitted); T1.5 pure-git dco CI job; T1.6 AGENTS.md + one-line CLAUDE.md pointer) — core repo

**Done when:** CONTRIBUTING has model+intake+DCO sections (no contradiction with the CONTRIBUTING breaking-changes section); bug/feature.yml + PR template render; setup_labels.sh idempotent; CODEOWNERS + CoC 2.1 present; unsigned scratch-PR commit fails the dco job, signed passes; an agent reading only AGENTS.md can run lint/test/build and knows the rejection list

### `OO-2` — Apps repo: hygiene mirror + front-door CONTRIBUTING

**Status:** done

Session 1 — Stated model + hygiene set (T1.3 apps mirror + app-request.yml + apps CONTRIBUTING front-door bar: SDK-only imports, per-app tests, manifest completeness, README/LICENSE; plus apps portions of T1.4 CODEOWNERS/CoC, T1.5 dco job, T1.6 AGENTS.md/CLAUDE.md)

**Done when:** apps repo has bug/feature/app-request.yml + PR template rendering; CONTRIBUTING front-door bar documented; CODEOWNERS+CoC+AGENTS.md present; dco job on apps ci.yml; manifest-validate green (38 manifests)

### `OO-3` — SKILL.md compatibility doc + parser consolidation (T2.1)

**Status:** done

Session 2 — Contribution ramps (T2.1: verify skills/loader.py frontmatter tolerance for missing triggers, write docs/reference/skill-format.md, add tests/test_skill_format_compat.py). DEVIATION (owner-authorized): collapsed 5 duplicate frontmatter parsers onto one and closed 4 silent field-loss defects (BOM, leading blank line, YAML block-list triggers, hoisted nested keys) + a raw-YAML prompt-leak

**Done when:** docs/reference/skill-format.md matches probed parser behavior; vanilla name+description SKILL.md imports cleanly and triggers is genuinely optional; test_skill_format_compat.py (20 cases) green; all callers delegate to the single parser; make lint + make test green with zero pre-existing skill-test assertion edits

### `OO-4` — Seed good-first-issues + run setup_labels.sh on both repos (T2.2)

**Status:** done

Session 2 — Contribution ramps (T2.2: file the Design good-first-issue list with context/file-pointers/done-when and label them) + completes V1's deferred GitHub-side label/render step

**Done when:** >=8 good-first-issues live with the good-first-issue label; setup_labels.sh run on both repos (16 labels each); stale/already-fixed seeds verified and closed-with-evidence rather than filed (croniter DST, video kind, xfail list, --slack-only)

### `OO-5` — Structure Discussions + README community routing + roadmap-intake wiring (T2.3, T3.3)

**Status:** done

Session 2 (T2.3: structure Discussions categories, README/site links, owner-voice welcome draft) + Session 3 — Governance floor (T3.3: roadmap.md 'Proposing roadmap changes' intake paragraph linked to Discussions). Settings flip was owner-done 2026-07-31 (has_discussions:true both repos)

**Done when:** README community block routes by category + good-first-issue front door; roadmap.md carries the maintainer-ownership-as-process intake paragraph with a resolving link; discussions-welcome-draft.md drafted (awaiting owner approval; categories are web-UI-only per GraphQL schema, so 'App Dev'/'Roadmap Input' remain optional owner actions)

### `OO-6` — Maintainer release runbook (T3.1)

**Status:** done

Session 3 — Governance floor (T3.1: docs/maintainers/release-runbook.md — tag → environment approval → post-release verification checklist) + V3 (walk it against the shipped v0.1.3)

**Done when:** a person with org access can cut a release from the doc alone; documents all release.yml jobs, both protected environments, the six version surfaces, the annotated-tag dereference trap, and the '## [X.Y.Z]' CHANGELOG-heading requirement; validated against v0.1.3 (GHCR pull step flagged as the one hand-confirm item)

### `OO-7` — README version-drift guard (version-consistency hardening)

**Status:** done

Execution log [2026-07-31][README version] deviation: fix the stale pre-1.0 banner and mechanically prevent recurrence via tests/test_version_consistency.py (extended 4 → 6 surfaces: README banner + acp/client.py CLIENT_VERSION)

**Done when:** README banner shows the current release version; test_version_consistency.py guards the README banner and acp/client.py CLIENT_VERSION (both mutation-verified to fail naming the file to fix); runbook bump table lists all six surfaces

