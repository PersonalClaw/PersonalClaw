# Plan: Open-Source Operations — Contribution Model, Hygiene, Governance

**Status:** DESIGNED — deepened 2026-07-18 (initial PROPOSED 2026-07-18 from the pre-launch investigation & owner alignment review)
**Created:** 2026-07-18
**Wave:** 0 — the model must be stated before the first external contributor arrives.
**Depends on:** CI-RELEASE-ENGINEERING S1 (green main precedes inviting others to keep it green). Coordinates with ECOSYSTEM-TOOLING (front-door tooling) and DISCOVERABILITY-LAUNCH (community links).
**Scope:** state and implement the contribution model, ship the hygiene set in both repos, put minimum-viable governance in place. **Development-model decision (owner, 2026-07-18):** public repos are the working trees; worktrees locally, feature branches remotely, merged to `main`; real history from v0.1.0. A feature/bugfix/improvement branch carries **one conceptual commit**, amended and force-pushed **with lease** as it iterates; **`main` is never force-pushed** — the self-updater's `git pull` depends on its linear history. **Soul guardrail:** governance sized for a solo maintainer growing first contributors — no committees, no RFC process, no CLA. The core doctrine bar is NOT lowered; the newcomer ramp is the apps repo, not a softer core.

---

## Design

- **The stated model (README section "Contributing", both repos + CONTRIBUTING update):** core = high-doctrine working tree, PRs welcome under the validation bar, roadmap maintainer-owned with a written intake path (issue → discussion → maintainer files/updates a plan — so "maintainer-owned" reads as process, not opacity); apps repo = the community front door (SDK-contract bar, per-app CI, faster review promises).
- **Hygiene set (both repos):** issue templates — `bug.yml` (version/install-kind/OS + repro + logs-with-redaction-warning fields), `feature.yml`, `app-request.yml` (apps repo); `PULL_REQUEST_TEMPLATE.md` mirroring the bar: *what changed / change class (R-B-S) / what you validated as a user / docs touched*; labels (area:\*, wave:\*, good-first-issue, needs-triage, app:\*); `CODEOWNERS` (owner on `/`, explicit on `docs/roadmap/` to signal roadmap ownership); `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, enforcement contact = owner email); `FUNDING.yml` (GitHub Sponsors); SECURITY.md links (plan 35).
- **DCO, not CLA:** `Signed-off-by` required via a DCO check action; CONTRIBUTING explains the one-liner (`git commit -s`) and why (MIT provenance without paperwork). Adopted now while external commits = 0.
- **AGENTS.md (both repos) + CLAUDE.md pointer:** the agent-facing contributor brief — build/test/lint commands, doctrine one-pager (clean-break-within-class, provider boundary, sdk-only app imports, validation bar), links to EXECUTION-PROTOCOL for roadmap work, repo layout map, "what will get your PR rejected" list. `CLAUDE.md` = one line pointing at AGENTS.md (single source).
- **SKILL.md compatibility doc:** the `triggers:` frontmatter field is a PersonalClaw extension; document: vanilla ecosystem SKILL.md files import cleanly (triggers optional — verify against `skills/loader.py` parsing and record the truth), what foreign harnesses can consume of ours (everything minus `triggers`), and the skills.sh bridge expectations. Lands in `docs/reference/skill-format.md`.
- **Good-first-issue seeds (real backlog, not manufactured):** CHANGELOG backfill detail passes, WSL2 doc verification (plan 39 B2 subtasks), screenshot alt-text/doc polish, each `xfail`-annotated test from CI triage (one issue each, already filed by plan 33 T1.2), docs-drift fixes found by the stability sweep, `--slack-only` removal follow-through.
- **Community surface:** GitHub Discussions on (categories: Q&A, Show & Tell, App Dev, Roadmap Input) + Discord (channels: #support, #app-dev, #showcase, #roadmap; moderation = CoC; invite links from README/site). Discussions is the async/searchable record; Discord is the pulse — the split stated openly.
- **Repo-maintenance hygiene (owner-directed 2026-07-19; intuitive for humans + agents, enforced by mechanism):** a `docs/maintainers/repo-hygiene.md` (human-facing) mirrored by EXECUTION-PROTOCOL §7 (agent-facing), codifying: **branch naming** (`feature-` / `bugfix-` / `improvement-`, one concern per branch, off `main`); **one conceptual commit per branch** (amend + `git push --force-with-lease` as it iterates — never a bare `--force`; **`main` alone is never force-pushed**, the self-updater depends on its linear history); **clean authorship** (owner-only author + committer, no agent co-author/session trailers); imperative commit subjects; the **npm-workspace single-root-lockfile rule** (members carry no lockfile; build from root — npm/cli#4828). **Enforcement (CI checks, this plan + CI-RELEASE):** a branch-name lint on PRs, a commit-author/trailer lint (fails on non-owner author or agent trailers), a protected-`main` rule (reject force-pushes to `main`), and a stray-member-lockfile check (fails if `web/`|`desktop/package-lock.json` reappears). Documentation without enforcement drifts; both ship together.
- **Continuity floor:** documented org-recovery path (second org owner — a trusted human — or GitHub's account-recovery posture + a sealed credentials note), PyPI trusted-publishing (no long-lived tokens to lose), a named co-maintainer *path* for the apps repo (criteria: sustained quality PRs; role: triage + app reviews), release runbook (`docs/maintainers/release-runbook.md`: tag → approve environment → verify checklist).

## Contracts & artifacts (doc/config artifacts; the structured pieces pinned)

- **Hygiene file set (exact paths, both repos unless noted):** `.github/ISSUE_TEMPLATE/{bug,feature}.yml` (+ `app-request.yml` apps repo), `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS`, `.github/FUNDING.yml`, `.github/dependabot.yml` (plan 33), `CODE_OF_CONDUCT.md`, `AGENTS.md`, `CLAUDE.md` (one-line pointer), `docs/reference/skill-format.md`, `docs/maintainers/{release-runbook,continuity,usability-kit}.md`.
- **PR template required fields (the contract every PR fills):** *What changed* · *Change class (R/B/S per plan 31)* · *What you validated as a user* · *Docs touched*. This mirrors EXECUTION-PROTOCOL's definition-of-done — it's how a reviewer (or the owner auditing delegated work) checks a PR at a glance.
- **AGENTS.md content contract:** build/test/lint commands, the doctrine one-pager (clean-break-within-class, provider boundary, sdk-only app imports, validation bar), a pointer to EXECUTION-PROTOCOL for roadmap work, the repo-layout map, and the explicit "what gets your PR rejected" list. `CLAUDE.md` = single line → AGENTS.md (one source).
- **DCO:** `Signed-off-by` enforced by a CI check (plan 33 CI files); the contract is `git commit -s`.
- **Integration points:** SECURITY.md links from plan 35; Discord/Discussions links feed DISCOVERABILITY (36); the continuity doc references plan 47's signing-key safeguarding; the skill-format doc is verified against `skills/loader.py` (T2.1 — record the parser's real tolerance, don't assume).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Stated model + hygiene set

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | CONTRIBUTING update (core): add "The model" section (stated model per Design), roadmap intake path, DCO explainer; keep the doctrine section as amended by plan 31 T1.7 | `CONTRIBUTING.md` | model + intake + DCO sections present; no contradictions with change-lifecycle.md |
| T1.2 | Issue/PR templates + labels (core): the three YAML forms with the listed fields (bug form includes a "redact tokens/paths before pasting logs" warning), PR template with the four sections; create labels via `gh label create` script | `.github/ISSUE_TEMPLATE/{bug,feature}.yml`, `.github/PULL_REQUEST_TEMPLATE.md`, `scripts/setup_labels.sh` | forms render on New Issue; PR template appears; labels exist |
| T1.3 | Same for apps repo + `app-request.yml`; apps CONTRIBUTING (front-door bar: SDK-only imports, per-app tests, manifest completeness, README per app) | apps repo mirrors + `CONTRIBUTING.md` | renders; bar documented |
| T1.4 | CODEOWNERS, CoC (Covenant 2.1 + contact), FUNDING.yml — both repos | `.github/CODEOWNERS`, `CODE_OF_CONDUCT.md`, `.github/FUNDING.yml` | files present; Sponsors button renders once owner task 2 done |
| T1.5 | DCO check: add the DCO GitHub App or `dco-check` action job to both CI files; CONTRIBUTING one-liner | workflows, CONTRIBUTING | unsigned commit on a scratch PR fails the check; signed passes |
| T1.6 | AGENTS.md (core + apps) per Design spec + one-line CLAUDE.md pointers | `AGENTS.md`, `CLAUDE.md` both repos | an agent reading only AGENTS.md can run lint/test/build and knows the rejection list |
| V1 | Validation: open a scratch issue via each form, a scratch PR touching docs — every template/check/label behaves; then close/delete scratch artifacts | — | holds |

### Session 2 — Contribution ramps

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | SKILL.md compat doc: verify `skills/loader.py` frontmatter parsing tolerance for missing `triggers` (read the code; record truth), then write `docs/reference/skill-format.md` per Design | doc + Execution log note | doc matches verified parser behavior; vanilla-skill import test added (`tests/test_skill_format_compat.py`, minimal fixture) |
| T2.2 | Seed good-first-issues: file the Design list (each: context, pointer to files, done-when — reuse plan task lines where they exist), label them | GitHub issues (executor via `gh issue create`) | ≥8 issues live with good-first-issue label |
| T2.3 | Enable + structure Discussions (categories per Design); README/site links; welcome pinned post (drafted for owner voice) | GitHub settings + `README.md` | categories exist; pinned draft awaiting owner approval |
| T2.4 | Discord scaffolding docs: channel list, CoC link, invite placement (server creation itself = owner task 3); permanent invite wired into README/site once created | `README.md`, site repo footer | links live after owner task 3 |
| V2 | Validation: stranger's path — README → CONTRIBUTING → a good-first-issue → knows exactly what to do; timed read ≤10 min | — | recorded |

### Session 3 — Continuity + governance floor

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Release runbook: tag → environment approval → post-release verification checklist (install one-liner, compose pull, changelog panel) | create `docs/maintainers/release-runbook.md` | a person with org access can cut a release from the doc alone |
| T3.2 | Continuity doc: org-recovery path, credential inventory (what exists, where, recovery), co-maintainer criteria + scope for the apps repo | create `docs/maintainers/continuity.md` (no secrets in it — inventory names, not values) | reviewed by owner (owner task 4); no secret material present (grep for token-like strings) |
| T3.3 | Roadmap-intake wiring: Discussions "Roadmap Input" category linked from roadmap.md header; one-paragraph policy in roadmap.md | `docs/roadmap/roadmap.md` | paragraph present; link resolves |
| V3 | Validation: continuity doc dry-run — owner confirms each recovery path is actionable as written | — | owner-confirmed |

## Owner tasks (real world)

1. **Set the CoC enforcement contact** (an email you'll actually read) — T1.4 needs it.
2. **Enroll in GitHub Sponsors** (or explicitly skip — then FUNDING.yml is omitted, not stubbed).
3. **Create the Discord server** (or choose Zulip/none — decision), generate a permanent invite for T2.4.
4. **Review the continuity doc** and, when someone fits, **designate the second org owner** — the single highest-leverage bus-factor action.
5. **Approve + post the Discussions welcome message** (your voice).
6. Decide the **USPTO/EUIPO screen** on "PersonalClaw": do it (~$0 self-search / ~$250-2k with counsel) or accept the risk knowingly — record either way.

## Risks & open questions

- **Empty-room risk:** Discussions/Discord launched before users exist look dead; mitigation: launch them *with* the public push (plan 36 S4 gate), owner seeds first threads.
- **Open:** require DCO on the apps repo too? Default yes (same provenance logic); revisit only if it measurably deters app contributors. **Resolved 2026-07-22: DCO enforced on the apps repo too (T1.5 mirror).**

## Execution log

- [2026-07-22][T1.1] DONE: core `CONTRIBUTING.md` gains "The model" (core=high-doctrine working tree, roadmap maintainer-owned with issue→Discussions→plan intake, apps=newcomer ramp), a DCO explainer (`git commit -s`), and an AGENTS.md pointer. Existing doctrine/setup sections untouched.
- [2026-07-22][T1.2] DONE: core `.github/ISSUE_TEMPLATE/{bug,feature}.yml` (bug carries version/install-kind/OS/repro + a redact-tokens-before-pasting-logs warning), `.github/PULL_REQUEST_TEMPLATE.md` with the four contract sections (what changed / change class R·B·S / validated-as-user / docs touched), and `scripts/setup_labels.sh` (idempotent `gh label create --force`: triage/type/area:*/wave:* taxonomy). YAML validated.
- [2026-07-22][T1.3] DONE: apps repo mirror — `CONTRIBUTING.md` (front-door bar: SDK-only imports, minimum permissions, manifest deps, per-app tests without vendor SDKs, README+LICENSE), `.github/ISSUE_TEMPLATE/{bug,feature,app-request}.yml`, `.github/PULL_REQUEST_TEMPLATE.md` (app-bar checklist). manifest-validate still green (38 manifests).
- [2026-07-22][T1.4] DONE: `.github/CODEOWNERS` (owner on `/`, explicit on `docs/roadmap/`) + `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, contact keyurrgolani@gmail.com) in both repos. **FUNDING.yml deliberately NOT created** — per the plan, Sponsors is omitted-not-stubbed until the owner enrolls (owner task 2, pending). CoC contact email is the repo git identity as a sensible default; flagged for owner confirmation (owner task 1).
- [2026-07-22][T1.5] DONE: DCO enforcement — a pure-git `dco` job (PR-only, no external action) added to BOTH `ci.yml` files: diffs the PR range against base, fails any commit lacking a `Signed-off-by` matching its author. Verified locally against real signed history (passes). CONTRIBUTING carries the `git commit -s` one-liner.
- [2026-07-22][T1.6] DONE: `AGENTS.md` (core + apps) — build/test/lint commands, doctrine one-pager, git/PR rules, repo map, and the explicit "what gets your PR rejected" list; `CLAUDE.md` in each repo is a one-line pointer to it (single source). NOTE: this is the in-repo CLAUDE.md (distinct from the workspace-root CLAUDE.md, which is not in any repo).
- [2026-07-22][V1] PARTIAL: local validation done — all YAML parses, label script `bash -n` clean, DCO logic verified against signed commits, `make lint` green (core), manifest-validate green (apps). The GitHub-side render check (open a scratch issue via each form / scratch PR exercises the templates + DCO check + labels) requires the PRs to be merged and `setup_labels.sh` run — deferred to post-merge; recorded here so it isn't skipped.
- [2026-07-22][owner-tasks] STATUS: (1) CoC contact — defaulted to owner email, needs confirm. (2) GitHub Sponsors — PENDING owner decision; FUNDING.yml omitted until then. (3) Discord server — S2 (T2.4), not this session. (4) **Second org owner — BLOCKED: no second trusted member exists yet (owner, 2026-07-22); continuity doc (S3/T3.2) will document the recovery path without a second owner, revisit when a member fits.** (5) Discussions welcome post — S2. (6) USPTO/EUIPO screen — owner decision, not code-blocking.
- [2026-07-22][SCOPE] Session 1 of 3 executed (stated model + hygiene set + DCO + AGENTS). Sessions 2 (contribution ramps: skill-format doc, good-first-issues, Discussions/Discord) and 3 (continuity + release runbook) remain; several of their tasks are gated on owner decisions (Discord/Sponsors) and the public-launch gate (plan 36 S4, empty-room risk).
