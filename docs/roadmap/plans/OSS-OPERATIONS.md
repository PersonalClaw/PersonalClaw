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
- **Community surface:** GitHub Discussions on (categories: Q&A, Show & Tell, App Dev, Roadmap Input) — the async, searchable, in-repo record. **Owner-descoped 2026-07-31: a chat server (Discord/Zulip/etc.) is OUT OF THIS PLAN** — the owner handles real-time community surfaces separately, so no task here creates, documents, or wires one. Discussions is this plan's whole community deliverable.
- **Repo-maintenance hygiene (owner-directed 2026-07-19; intuitive for humans + agents, enforced by mechanism):** a `docs/maintainers/repo-hygiene.md` (human-facing) mirrored by EXECUTION-PROTOCOL §7 (agent-facing), codifying: **branch naming** (`feature-` / `bugfix-` / `improvement-`, one concern per branch, off `main`); **one conceptual commit per branch** (amend + `git push --force-with-lease` as it iterates — never a bare `--force`; **`main` alone is never force-pushed**, the self-updater depends on its linear history); **clean authorship** (owner-only author + committer, no agent co-author/session trailers); imperative commit subjects; the **npm-workspace single-root-lockfile rule** (members carry no lockfile; build from root — npm/cli#4828). **Enforcement (CI checks, this plan + CI-RELEASE):** a branch-name lint on PRs, a commit-author/trailer lint (fails on non-owner author or agent trailers), a protected-`main` rule (reject force-pushes to `main`), and a stray-member-lockfile check (fails if `web/`|`desktop/package-lock.json` reappears). Documentation without enforcement drifts; both ship together.
- **Release runbook:** `docs/maintainers/release-runbook.md` — tag → approve environment → verify checklist. (PyPI trusted-publishing means there are no long-lived tokens to lose, so the runbook carries no credential material.)
- **Continuity floor — owner-descoped 2026-07-31, OUT OF THIS PLAN.** The org-recovery path, credential inventory, second-org-owner designation, and the apps-repo co-maintainer path are handled by the owner separately. They were only ever gated on a second trusted human existing (logged BLOCKED 2026-07-22), which is not an engineering task. No task here writes `docs/maintainers/continuity.md`.

## Contracts & artifacts (doc/config artifacts; the structured pieces pinned)

- **Hygiene file set (exact paths, both repos unless noted):** `.github/ISSUE_TEMPLATE/{bug,feature}.yml` (+ `app-request.yml` apps repo), `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS`, `.github/FUNDING.yml`, `.github/dependabot.yml` (plan 33), `CODE_OF_CONDUCT.md`, `AGENTS.md`, `CLAUDE.md` (one-line pointer), `docs/reference/skill-format.md`, `docs/maintainers/{release-runbook,usability-kit}.md`. (`continuity.md` descoped 2026-07-31 — see Design.)
- **PR template required fields (the contract every PR fills):** *What changed* · *Change class (R/B/S per plan 31)* · *What you validated as a user* · *Docs touched*. This mirrors EXECUTION-PROTOCOL's definition-of-done — it's how a reviewer (or the owner auditing delegated work) checks a PR at a glance.
- **AGENTS.md content contract:** build/test/lint commands, the doctrine one-pager (clean-break-within-class, provider boundary, sdk-only app imports, validation bar), a pointer to EXECUTION-PROTOCOL for roadmap work, the repo-layout map, and the explicit "what gets your PR rejected" list. `CLAUDE.md` = single line → AGENTS.md (one source).
- **DCO:** `Signed-off-by` enforced by a CI check (plan 33 CI files); the contract is `git commit -s`.
- **Integration points:** SECURITY.md links from plan 35; the Discussions link feeds DISCOVERABILITY (36); the skill-format doc is verified against `skills/loader.py` (T2.1 — record the parser's real tolerance, don't assume). **Note for plan 47 (SECURITY-HARDENING):** its owner task 2 currently says the signing key's recovery is documented in "the continuity doc (plan 37)". That doc is descoped here as of 2026-07-31, so plan 47 owns its own key-safeguarding note.

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
| T2.3 | Structure Discussions (categories per Design); README/site links; welcome pinned post (drafted for owner voice). **Discussions was enabled on both repos by the owner 2026-07-31** — the settings flip is done; the structuring and links are the task | GitHub settings + `README.md` | categories exist; pinned draft awaiting owner approval |
| ~~T2.4~~ | ~~Discord scaffolding docs~~ — **DESCOPED 2026-07-31 (owner): a chat server is handled separately and is not part of this plan.** No replacement task | — | n/a — removed from scope |
| V2 | Validation: stranger's path — README → CONTRIBUTING → a good-first-issue → knows exactly what to do; timed read ≤10 min | — | recorded |

### Session 3 — Governance floor

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Release runbook: tag → environment approval → post-release verification checklist (install one-liner, compose pull, changelog panel) | create `docs/maintainers/release-runbook.md` | a person with org access can cut a release from the doc alone |
| ~~T3.2~~ | ~~Continuity doc~~ — **DESCOPED 2026-07-31 (owner): org recovery, credential inventory, second org owner and the co-maintainer path are handled separately.** No replacement task | — | n/a — removed from scope |
| T3.3 | Roadmap-intake wiring: Discussions "Roadmap Input" category linked from roadmap.md header; one-paragraph policy in roadmap.md | `docs/roadmap/roadmap.md` | paragraph present; link resolves |
| V3 | Validation: a release can be cut from T3.1's runbook alone (walk it against the v0.1.3 release as the reference run) | — | recorded |

## Owner tasks (real world)

1. **Set the CoC enforcement contact** (an email you'll actually read) — T1.4 needs it.
2. **Enroll in GitHub Sponsors** (or explicitly skip — then FUNDING.yml is omitted, not stubbed).
3. ✅ **Enable GitHub Discussions** — DONE 2026-07-31 (both repos).
4. **Approve + post the Discussions welcome message** (your voice).
5. Decide the **USPTO/EUIPO screen** on "PersonalClaw": do it (~$0 self-search / ~$250-2k with counsel) or accept the risk knowingly — record either way.

**Descoped from this plan 2026-07-31 (owner handles separately — do not re-surface as open items):** the
chat/Discord community server, and the continuity floor (org-recovery path, credential inventory,
second org owner, apps-repo co-maintainer path).

## Risks & open questions

- **Empty-room risk:** Discussions launched before users exist looks dead; mitigation: the owner seeds the first threads, and the welcome post lands with the public push (plan 36 S4 gate). **Owner decision 2026-07-31: Discussions is enabled now** — accepting the quiet-room period rather than gating the surface on the launch.
- **Open:** require DCO on the apps repo too? Default yes (same provenance logic); revisit only if it measurably deters app contributors. **Resolved 2026-07-22: DCO enforced on the apps repo too (T1.5 mirror).**

## Execution log

- [2026-07-22][T1.1] DONE: core `CONTRIBUTING.md` gains "The model" (core=high-doctrine working tree, roadmap maintainer-owned with issue→Discussions→plan intake, apps=newcomer ramp), a DCO explainer (`git commit -s`), and an AGENTS.md pointer. Existing doctrine/setup sections untouched.
- [2026-07-22][T1.2] DONE: core `.github/ISSUE_TEMPLATE/{bug,feature}.yml` (bug carries version/install-kind/OS/repro + a redact-tokens-before-pasting-logs warning), `.github/PULL_REQUEST_TEMPLATE.md` with the four contract sections (what changed / change class R·B·S / validated-as-user / docs touched), and `scripts/setup_labels.sh` (idempotent `gh label create --force`: triage/type/area:*/wave:* taxonomy). YAML validated.
- [2026-07-22][T1.3] DONE: apps repo mirror — `CONTRIBUTING.md` (front-door bar: SDK-only imports, minimum permissions, manifest deps, per-app tests without vendor SDKs, README+LICENSE), `.github/ISSUE_TEMPLATE/{bug,feature,app-request}.yml`, `.github/PULL_REQUEST_TEMPLATE.md` (app-bar checklist). manifest-validate still green (38 manifests).
- [2026-07-22][T1.4] DONE: `.github/CODEOWNERS` (owner on `/`, explicit on `docs/roadmap/`) + `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1, contact keyurrgolani@gmail.com) in both repos. **FUNDING.yml deliberately NOT created** — per the plan, Sponsors is omitted-not-stubbed until the owner enrolls (owner task 2, pending). CoC contact email is the repo git identity as a sensible default; flagged for owner confirmation (owner task 1).
- [2026-07-22][T1.5] DONE: DCO enforcement — a pure-git `dco` job (PR-only, no external action) added to BOTH `ci.yml` files: diffs the PR range against base, fails any commit lacking a `Signed-off-by` matching its author. Verified locally against real signed history (passes). CONTRIBUTING carries the `git commit -s` one-liner.
- [2026-07-22][T1.6] DONE: `AGENTS.md` (core + apps) — build/test/lint commands, doctrine one-pager, git/PR rules, repo map, and the explicit "what gets your PR rejected" list; `CLAUDE.md` in each repo is a one-line pointer to it (single source). NOTE: this is the in-repo CLAUDE.md (distinct from the workspace-root CLAUDE.md, which is not in any repo).
- [2026-07-22][V1] PARTIAL: local validation done — all YAML parses, label script `bash -n` clean, DCO logic verified against signed commits, `make lint` green (core), manifest-validate green (apps). The GitHub-side render check (open a scratch issue via each form / scratch PR exercises the templates + DCO check + labels) requires the PRs to be merged and `setup_labels.sh` run — deferred to post-merge; recorded here so it isn't skipped.
- [2026-07-22][owner-tasks] STATUS: (1) CoC contact — defaulted to owner email, needs confirm. (2) GitHub Sponsors — PENDING owner decision; FUNDING.yml omitted until then. (3) Discord server — S2 (T2.4), not this session. (4) **Second org owner — BLOCKED: no second trusted member exists yet (owner, 2026-07-22); continuity doc (S3/T3.2) will document the recovery path without a second owner, revisit when a member fits.** (5) Discussions welcome post — S2. (6) USPTO/EUIPO screen — owner decision, not code-blocking. *(Superseded 2026-07-31: items 3 and 4 are DESCOPED from this plan — see the entry below. This line is kept as the historical record; do not action it.)*
- [2026-07-22][SCOPE] Session 1 of 3 executed (stated model + hygiene set + DCO + AGENTS). Sessions 2 (contribution ramps: skill-format doc, good-first-issues, Discussions/Discord) and 3 (continuity + release runbook) remain; several of their tasks are gated on owner decisions (Discord/Sponsors) and the public-launch gate (plan 36 S4, empty-room risk). *(Superseded 2026-07-31 — see below.)*
- **[2026-07-31][SCOPE — owner descope + Discussions enabled] Two items leave this plan permanently.**
  The owner directed (2026-07-31) that the **chat/Discord community server** (T2.4) and the
  **continuity floor** (T3.2 — org-recovery path, credential inventory, second org owner, apps-repo
  co-maintainer path) are handled separately and are no longer tracked here. Both task rows are struck
  through, the Design bullets rewritten, and the owner-task list renumbered so neither resurfaces as an
  open item in a future audit. **Neither was ever an engineering blocker:** T2.4 waited on a server
  existing, T3.2 on a second trusted human existing (logged BLOCKED 2026-07-22). One consequence
  recorded rather than left dangling: SECURITY-HARDENING's owner task 2 pointed at the continuity doc
  for signing-key recovery — plan 47 now owns that note (flagged in Integration points).
  **GitHub Discussions was ENABLED on both repos** the same day (owner-approved, verified
  `has_discussions: true` via the API), which retires the settings half of T2.3 and resolves the
  empty-room question in favor of enabling now. **What actually remains in this plan:** T2.1
  skill-format doc, T2.2 good-first-issue seeds, T2.3 Discussions structuring + README links + welcome
  draft, T3.1 release runbook, T3.3 roadmap-intake wiring.
- **[2026-07-31][T2.1] DONE — and it was not docs-only. DEVIATION (owner-authorized): the audit found
  bugs, and the owner directed "fix the bugs and improve any gaps we discovered … not delay and risk
  it being lost and forgotten."**

  **The compatibility claim HOLDS**, now verified rather than assumed: a vanilla `SKILL.md` carrying
  only `name` + `description` imports cleanly, and `triggers` is genuinely optional (absent ⇒ lists and
  loads, never auto-surfaces; empty is never read as "matches everything").

  **Four defects found by PROBING the parser, not reading it** — each silently lost every field:
  (1) a **UTF-8 BOM** before `---` (Windows editors add one) → `{}`; (2) a **leading blank line** → `{}`;
  (3) `triggers` as a **YAML block list** — the ecosystem's natural spelling — silently ignored;
  (4) **nested mapping keys hoisted to the top level**, so `sub: v` masqueraded as a real field. Fixed
  in `_parse_frontmatter` (+ a new `_parse_frontmatter_text` string entry point) and in
  `strip_frontmatter`, where the same delimiter miss **leaked the whole raw YAML block into the model
  prompt**.

  **The finding that mattered most came from validating as a user, not from the unit tests.** With all
  17 tests green I installed a real BOM'd skill into a live gateway (:10761, isolated dev home) and the
  dashboard still showed an EMPTY description. Cause: **five more copies of this parser existed**
  (`dashboard/handlers/skills.py` ×2, `skills/marketplace.py`, `skills/curator.py`,
  `workflows/native.py`) — `curator.py`'s docstring even claimed to "mirror loader._parse_frontmatter"
  and no longer did. Fixing one parser fixed nothing the user could see. All now **delegate to the one
  parser** (clean break, no compat shim).

  **One duplicate was BETTER than the original and was promoted, not dropped:**
  `marketplace._parse_description` folded YAML **block scalars** (`|`, `>`, `|-`, …), which the loader
  did not — so the Store preview showed a folded multi-line description while the installed skill showed
  a bare `"|"`. That capability moved into `_parse_frontmatter_text`; a test pins Store-vs-loader
  agreement. **This invalidated one of my own doc claims mid-task** (the "not supported" table said
  block scalars keep the indicator) — the doc and test were corrected to the measured behavior.

  **Two of my own errors, recorded:** I first documented block-scalar behavior and a
  `docs/guides/skills.md` cross-link **without checking either** — the probe showed block scalars
  yielded a literal `'|'`, and that guide does not exist. Both fixed. Second, a mutation sweep whose
  `sed` patterns silently matched nothing reported "0 failures" and I nearly read that as tests-are-weak;
  re-run properly, **all six mutations are caught**. One mutation (removing CRLF normalization) legitimately
  changed nothing because `read_text` already translates newlines — so a **string-level** test was added,
  since the raw-text callers (`mcp_core.py`) get no such translation.

  **Deliberately NOT changed:** the skills route keys its `name` off the DIRECTORY, not frontmatter.
  Verified as intentional (it is the install/uninstall identity, and every bundled skill behaves the
  same) — left alone rather than "fixed".

  **ARCC was NOT queried — the MCP server is unavailable in this session.** Standard practice applied:
  tolerant-read only, no new dependency, no parsing of untrusted input into executable form, and the
  prompt-leak path closed.

  **Gates:** `make lint` clean (black/isort/flake8 + mypy 563 files) · `make test` **9779 passed, 0
  failed** · all **250 pre-existing skill tests pass with ZERO assertion edits** (E4 holds).
  New: `tests/test_skill_format_compat.py` (20 cases) + `docs/reference/skill-format.md`.
  **Validated as a user** on :10761 across two gateway boots with a genuinely foreign skill (BOM +
  YAML-list triggers + no PersonalClaw fields): description empty → correct, `always` under a BOM
  false → true, YAML-list triggers matched live, **0 tracebacks**.
- **[2026-07-31][T2.2] DONE — 9 good-first-issues live (bar was ≥8), and two of the plan's seeds were
  RETIRED as already-fixed rather than filed.**

  **`scripts/setup_labels.sh` had never been run** (V1's log flagged the GitHub-side step as deferred
  to post-merge and it was never picked up) — so the repo carried only GitHub's 13 defaults and none of
  the `area:*`/`wave:*`/`good-first-issue` taxonomy the script defines. Run now on **both** repos, 16
  labels each.

  **Verified every seed still reproduces before filing — two did not, and were CLOSED with evidence
  instead:** #85 (croniter DST test) was fixed by PR #98's invariant rewrite (`-k spring_forward` → 2
  passed), and #94 (video stored as `kind=image`) is fixed — `video` is now in `ALLOWED_KINDS`,
  `BINARY_KINDS` and `_MIME_TO_EXT`, and #127 closed the frontend half with a test that reads
  `ALLOWED_KINDS` out of the Python source so the two cannot drift again. Filing either as a
  good-first-issue would have sent a newcomer to fix nothing.

  **Two more plan seeds were stale and are not actionable:** the "one issue per `xfail`-annotated test"
  list is already covered by open issues #6/#7 (the xfail reasons cite them inline), and
  `--slack-only` no longer exists in code (Provider-Boundary retired it).

  **Labeled 6 verified-real existing issues** (#6, #7, #8, #9, #10, #95 — each re-checked: #6 still
  xfails, #95's `SafetyProfile` is exported but has zero consulting call sites, `WidgetFrame.tsx`
  exists) and **filed 4 new ones from real findings**: #129 (a Skills authoring guide — `docs/guides/`
  genuinely has no `skills.md`, found while writing T2.1), #130 (`audit_home()` has zero runtime
  callers — wire into doctor or delete, with the honest "run it first, the answer decides" framing),
  #131 (`make serve-web` documented on :3000, actually :3100 per `vite.config.ts:56` — a newcomer's
  first frontend command lands on a dead port), and apps#13 (README badges — the apps README has none).
- **[2026-07-31][T3.1] DONE — `docs/maintainers/release-runbook.md`, written from `release.yml` and
  WALKED against the shipped v0.1.3.**

  Documents all seven jobs, both protected environments (`release`, `release-client` — verified to
  exist via the API), the six version surfaces, tagging, the in-flight approvals, and outside-in
  post-release verification. Validated live: PyPI carries core **and** client at 0.1.3 (lockstep
  holds), the GitHub Release has 48k of real CHANGELOG-derived notes, `static/dist` is the symlink the
  wheel check inspects, and **`git rev-parse v0.1.3` (`f4c6b28`) genuinely differs from
  `v0.1.3^{commit}` (`bc185c0`)** — the annotated-tag dereference trap the page warns about is real,
  not theoretical.

  **One claim corrected mid-write:** the CHANGELOG heading must be exactly `## [X.Y.Z]`, because the
  `notes` job matches `^## \[<ver>\]…`; a heading without brackets silently yields the bare fallback
  `Release X.Y.Z.` — a published release with no notes, unfixable after the tag. **One step left
  explicitly unverified** rather than implied: the GHCR pull needs `docker login` or a `read:packages`
  token, neither available here — marked as the one item to confirm by hand.
- **[2026-07-31][T2.3 + T3.3] DONE (code/docs side); one OWNER action remains, and it is a real API
  limit, not a deferral.**

  **Discussions is ENABLED on both repos** (owner-approved; `has_discussions: true` verified). README
  gains a community block routing by category (Q&A / Show and tell / Ideas, bugs → Issues) plus the
  good-first-issue front door. `roadmap.md` gains **"Proposing roadmap changes"** (T3.3) stating
  maintainer-ownership as *process, not opacity* — with the reason (plans encode cross-plan contracts;
  one holder keeps the dependency graph coherent), the propose-don't-PR path into Ideas, and what IS
  directly contributable (implement a plan task under EXECUTION-PROTOCOL, take a good-first-issue, or
  report a plan whose premise no longer matches code — an E1 escalation, which has genuinely re-scoped
  plans before).

  **OWNER ACTION: discussion CATEGORIES cannot be created via the API.** Verified against the GraphQL
  schema — the `Mutation` type exposes `createDiscussion`/`updateDiscussion`/`closeDiscussion` but
  **no `createDiscussionCategory`**; category management is web-UI-only. Enabling Discussions created
  six defaults (Announcements, General, Ideas, Polls, Q&A, Show and tell), which already cover the
  plan's list except **App Dev**. Links currently point roadmap input at **Ideas**, which works — a
  dedicated "Roadmap Input" category is optional. Both steps + the welcome post are laid out in
  `docs/maintainers/discussions-welcome-draft.md` (drafted in owner voice, deliberately NOT posted;
  all four doc paths it cites verified to resolve).
- **[2026-07-31][README version] DONE — and the drift is now MECHANICALLY PREVENTED, not just fixed.**

  The README's pre-1.0 banner said **v0.1.0 at v0.1.3** — three releases stale, in the exact paragraph
  warning users their data may break without migration. Root cause: it was unenforced. Rather than fix
  the symptom, `tests/test_version_consistency.py` grew two guards (4 → 6 surfaces): the README banner
  version, and **`acp/client.py`'s `CLIENT_VERSION`** — which was itself found hardcoded at `0.1.2`
  during the 0.1.3 release, meaning every ACP agent was told the wrong version in the initialize
  handshake. Both guards mutation-verified: reverting either value fails its own test with a message
  naming the file to fix. The runbook's bump table now lists all six.

  Left alone deliberately: the v0.1.0 mentions in `AGENTS.md` and `CONTRIBUTING.md` are **historical**
  ("this shipped as the v0.1.0 blank dashboard") and correct as written.
