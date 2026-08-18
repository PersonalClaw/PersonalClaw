# PUBLICATION — atomic plans

**Source plan:** [`PUBLICATION`](../plans/PUBLICATION.md)  
**Code:** `PUBL`  
**Source status:** in_progress

GitHub release of core + apps repos. Session 1 release mechanics fully executed (org, repos, URL/metadata reconcile, CHANGELOG, screenshots, public flip, v0.1.0-v0.1.3 to PyPI/GHCR/GitHub). Remaining are owner real-world steps only: the three Session-2 clean-machine verifications and the S1.9 publicize call gated on DISCOVERABILITY-LAUNCH.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PUBL-1` | ✅ | Create PersonalClaw org, fresh brand-cased repos, and reserve names | — | PersonalClaw GitHub org holds fresh PersonalClaw/PersonalClaw (core) + PersonalClaw/PersonalClawApps (apps) + empty personalclaw.dev, each seeded with one squashed initial commit; personalclaw.dev domain registered; PyPI + npm 'personalclaw' names reserved; old keyurgolani/* repos deleted after new pushes verified |
| `PUBL-2` | ✅ | Reconcile release URL, README clone links, homepage, and repo descriptions/topics | `PUBL-1` | hardcoded release URL at dashboard/handlers/core.py points at github.com/PersonalClaw/PersonalClaw/releases; core README Quickstart and apps/README.md use the real published URLs; homepage=personalclaw.dev set on both repos; descriptions + topics set on core and apps repos per S1.5 text |
| `PUBL-3` | ✅ | Create CHANGELOG.md (Keep-a-Changelog) read by the in-app Updates panel | — | CHANGELOG.md exists in Keep-a-Changelog format carrying v0.1.0 (through v0.1.3) entries; GET /api/changelog (updates.py) reads it instead of pointing at a missing file |
| `PUBL-4` | ✅ | Ship real dashboard screenshots plus SHOWCASE and reproducible CAPTURE doc | — | real captures ship as docs/screenshots/{light,dark}/* + SHOWCASE.md + reproducible docs/screenshots/CAPTURE.md (superseding the docs/assets/screenshot-dashboard.png placeholder); README screenshot references resolve |
| `PUBL-5` | ✅ | Verify main green and flip both repos to public visibility | `PUBL-1`, `EXT:CI-RELEASE-ENGINEERING:green main / red-test triage (S1)` | main confirmed green per CI-RELEASE-ENGINEERING S1 red-test triage; PersonalClaw/PersonalClaw and PersonalClaw/PersonalClawApps flipped to public visibility |
| `PUBL-6` | ✅ | Tag v0.1.0 and cut releases to PyPI, GHCR, and GitHub Releases | `PUBL-2`, `PUBL-3`, `EXT:CI-RELEASE-ENGINEERING:release pipeline`, `EXT:DISTRIBUTION:wheel bundles SPA / release artifacts` | v0.1.0 tagged on core (anchoring releases); v0.1.0-v0.1.3 published to PyPI (personalclaw core + personalclaw-client), GHCR images, and GitHub Releases with no force pushes to main |
| `PUBL-7` | ⬜ | Fresh-clone getting-started walkthrough on a clean machine and fix remote friction | `PUBL-6` | fresh clone from GitHub (not local) on a clean machine/venv runs the getting-started guide verbatim to a working gateway; any remote-specific friction fixed (URL casing, submodule-free clone, raw-content doc links) |
| `PUBL-8` | ✅ | Verify the self-update pipeline end-to-end against the real remote | `PUBL-6`, `EXT:DISTRIBUTION:tag-tracking self-update (S4)` | gateway on a clone one commit behind detects the update and apply pulls/rebuilds/re-execs successfully against the published remote |
| `PUBL-9` | ✅ | Verify Store git-source install from the published apps repo URL | `PUBL-1` | installing an app via POST /api/apps/sources git path against the published PersonalClawApps repo URL succeeds (previously validated only with local paths) |
| `PUBL-10` | ⬜ | Publicize once the DISCOVERABILITY-LAUNCH gate is green | `PUBL-6`, `EXT:DISCOVERABILITY-LAUNCH:site live + real screenshots + install one-liner (S1-3 gate)` | public launch announced after DISCOVERABILITY-LAUNCH S1-3 gate is met — marketing site live, real screenshots, install one-liner working |

## Atom scopes

### `PUBL-1` — Create PersonalClaw org, fresh brand-cased repos, and reserve names

**Status:** done

Session 1 §1; Executed 2026-07-19 (release mechanics)

**Done when:** PersonalClaw GitHub org holds fresh PersonalClaw/PersonalClaw (core) + PersonalClaw/PersonalClawApps (apps) + empty personalclaw.dev, each seeded with one squashed initial commit; personalclaw.dev domain registered; PyPI + npm 'personalclaw' names reserved; old keyurgolani/* repos deleted after new pushes verified

### `PUBL-2` — Reconcile release URL, README clone links, homepage, and repo descriptions/topics

**Status:** done

Session 1 §3-5

**Done when:** hardcoded release URL at dashboard/handlers/core.py points at github.com/PersonalClaw/PersonalClaw/releases; core README Quickstart and apps/README.md use the real published URLs; homepage=personalclaw.dev set on both repos; descriptions + topics set on core and apps repos per S1.5 text

### `PUBL-3` — Create CHANGELOG.md (Keep-a-Changelog) read by the in-app Updates panel

**Status:** done

Session 1 §7; Status line

**Done when:** CHANGELOG.md exists in Keep-a-Changelog format carrying v0.1.0 (through v0.1.3) entries; GET /api/changelog (updates.py) reads it instead of pointing at a missing file

### `PUBL-4` — Ship real dashboard screenshots plus SHOWCASE and reproducible CAPTURE doc

**Status:** done

Session 1 §6; Status line

**Done when:** real captures ship as docs/screenshots/{light,dark}/* + SHOWCASE.md + reproducible docs/screenshots/CAPTURE.md (superseding the docs/assets/screenshot-dashboard.png placeholder); README screenshot references resolve

### `PUBL-5` — Verify main green and flip both repos to public visibility

**Status:** done

Session 1 §2

**Done when:** main confirmed green per CI-RELEASE-ENGINEERING S1 red-test triage; PersonalClaw/PersonalClaw and PersonalClaw/PersonalClawApps flipped to public visibility

### `PUBL-6` — Tag v0.1.0 and cut releases to PyPI, GHCR, and GitHub Releases

**Status:** done

Session 1 §8; Status line

**Done when:** v0.1.0 tagged on core (anchoring releases); v0.1.0-v0.1.3 published to PyPI (personalclaw core + personalclaw-client), GHCR images, and GitHub Releases with no force pushes to main

### `PUBL-7` — Fresh-clone getting-started walkthrough on a clean machine and fix remote friction

**Status:** todo

Session 2 §1

**Done when:** fresh clone from GitHub (not local) on a clean machine/venv runs the getting-started guide verbatim to a working gateway; any remote-specific friction fixed (URL casing, submodule-free clone, raw-content doc links)

### `PUBL-8` — Verify the self-update pipeline end-to-end against the real remote

**Status:** done

Session 2 §2

**Done when:** gateway on a clone one commit behind detects the update and apply pulls/rebuilds/re-execs successfully against the published remote

### `PUBL-9` — Verify Store git-source install from the published apps repo URL

**Status:** done

Session 2 §3

**Done when:** installing an app via POST /api/apps/sources git path against the published PersonalClawApps repo URL succeeds (previously validated only with local paths)

### `PUBL-10` — Publicize once the DISCOVERABILITY-LAUNCH gate is green

**Status:** todo

Session 1 §9; Status line (owner call)

**Done when:** public launch announced after DISCOVERABILITY-LAUNCH S1-3 gate is met — marketing site live, real screenshots, install one-liner working

