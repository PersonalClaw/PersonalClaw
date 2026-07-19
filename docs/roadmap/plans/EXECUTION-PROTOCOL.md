# Execution Protocol — How Any Session Implements Any Plan

**Status:** ACTIVE — this is not a feature plan; it is the standing protocol every implementation session follows when executing a roadmap plan. Written 2026-07-18 (roadmap rev 9) so that implementation can be delegated — including to smaller/cheaper models — without eroding the codebase's standards.

Every plan's task tables assume this protocol. A session that has not read this document is not ready to execute a task.

---

## 1. Before touching code

1. Read, in order: the plan document (fully — especially its **soul guardrail** and **Context** sections), this protocol, and every architecture doc the plan cites (they live in `docs/architecture/`).
2. Locate your session's **task table** in the plan. Tasks are executed **in listed order**. You own exactly one task at a time.
3. Confirm the plan's **change class** (R / B / S — see `docs/architecture/change-lifecycle.md` once plan 31 lands; until then: does this change persisted state or a stable surface? If yes, it needs its gate/migration named in the plan). If the plan doesn't state the class and the change touches state, **stop — escalation trigger E3**.
4. Set up: dev gateway state must NEVER be your real home. Use `make serve` (isolated `./.dev-home`) or `PERSONALCLAW_HOME=<tmp>` + `--seed <fixture>`. Tests must monkeypatch `config_dir`/`tmp_path` (CONTRIBUTING rule — this has bitten before).

## 2. Ground rules (the anti-derail set)

- **The task line is the scope.** Do not fix, refactor, or "improve" anything the task doesn't name — if you found a real adjacent problem, record it (§5) instead of fixing it inline.
- **Premise mismatch = stop, not improvise.** If the task cites a file/function/line that doesn't exist as described (moved, renamed, already done), do not guess an equivalent. Record a deviation note (§5) and escalate (E1).
- **No new dependencies** unless the task line names the exact package. This includes "tiny" ones.
- **No dead code, no TODO/FIXME comments, no commented-out blocks, no "phase 2" stubs.** The codebase has zero TODOs; keep it that way — unfinished work lives in the plan file, not in code comments.
- **Vendor names never enter core.** Anything provider-specific goes in an app bundle; apps import core **only** via `personalclaw.sdk.*`. The deliberate exceptions are enumerated in `docs/architecture/provider-boundary.md` — you may not add to them.
- **One path per concern.** Never leave two implementations of the same behavior without a registered lifecycle gate (plan 31). "Old path kept just in case" is a defect.
- **Config fields are contracts.** A new config field must be wired through all of: dataclass + `_meta`, `load()`, `to_dict()`, a write path, and (if user-facing) a frontend control — `tests/test_config_roundtrip.py` will catch most misses; don't fight it, complete the wiring.
- **Entity/user state goes outside `config.json`** (`entity_settings/*.json`, dedicated stores) unless the plan says otherwise.
- **Security surfaces are copy-sensitive.** Do not reword warnings, consent text, fencing preambles, or refusal messages except as the task specifies — their wording is part of the control.
- **Match the house style.** black/isort/flake8/mypy enforce mechanics (`make lint`); for everything else, imitate the nearest existing module: naming, docstring register, error-handling shape, logging levels. When in doubt, find the closest analog file and follow it.
- **Frontend:** state lives in the URL (hash router doctrine — there's a test); components use the shared shell primitives (TopBar/ListScaffold/SidePanel/HeaderActions) and design tokens (`web/src/design/tokens.css`); no hardcoded colors/spacing; WCAG AA (focus-visible, reduced-motion) is not optional.

## 3. Definition of done — every task, no exceptions

A task is done when ALL of the following pass, run from the repo root:

```
make lint                      # black --check, isort --check, flake8, mypy
python -m pytest <targeted>    # the test files your change touches, plus new tests
make test                      # full suite before the session's final commit
cd web && npm run typecheck && npm test && npm run build   # when web/ was touched
```

…and:

- **New behavior has tests.** Bug-shaped tasks get a regression test that fails before the change and passes after. Destructive tests are isolated (§1.4).
- **Docs moved with the change.** If you altered config fields, routes, CLI flags, or user-visible behavior: update `docs/reference/` (and the plan's own doc targets) in the same commit.
- **CHANGELOG.md** gains an entry for class B/S changes.
- **The task's "done-when" column is literally true** — re-read it and verify each clause.

## 4. Session close — validate as a user

Every session's task table ends with a **validation walkthrough** (labeled V-task). Execute it exactly: drive the system from the frontend (or CLI where specified), inspecting every listed surface — UI, console, network tab, backend logs, persisted state under the dev home. Any gap or rough edge found is in scope to fix before the session closes (CONTRIBUTING's validation bar). A session whose V-task fails is not complete — do not mark it complete.

## 5. Deviations & discoveries ledger

Append (never rewrite) to the plan document under `## Execution log`:

```
- [YYYY-MM-DD][T<id>] DEVIATION|DISCOVERY|DONE|BLOCKED: <one line>
```

- **DONE** per completed task (with commit ref once committed).
- **DEVIATION** when reality forced a change from the task line (what + why).
- **DISCOVERY** for adjacent problems you deliberately did NOT fix (§2.1).
- **BLOCKED** with the escalation trigger id.

This ledger is how a solo maintainer audits delegated work — keep entries honest and terse.

## 6. Escalation triggers — stop and surface, do not push through

- **E1 — premise mismatch:** the code doesn't match the task's citations.
- **E2 — failing test you can't root-cause in ~30 minutes** (or any pre-existing red not annotated in code).
- **E3 — lifecycle ambiguity:** the change turns out to touch persisted state / a Tier-S surface and the plan didn't declare it.
- **E4 — security-control ambiguity:** the task requires touching auth, fencing, scanner, egress, sandbox, or SEL beyond its literal wording.
- **E5 — dependency pressure:** the implementation seems to need a package the task didn't name.
- **E6 — scope pressure:** completing the task honestly seems to require work another task/plan owns.

On any trigger: write the BLOCKED ledger line, leave the working tree clean (stash or revert partials — no half-landed state), and stop that task. Move to the next unblocked task only if it doesn't depend on the blocked one.

## 7. Commit protocol

- One concern per commit; the task id in the message: `feat(inbox): T2.3 typed kind registry` / `fix`, `refactor`, `docs`, `test` prefixes as appropriate.
- Never commit: secrets, `~/.personalclaw` content, `.dev-home`, generated `web/dist` (CI builds it), lockfile changes unrelated to the task.
- **Branch naming (enforced):** `feature-<slug>`, `bugfix-<slug>`, `improvement-<slug>` — one concern per branch, branched from `main`. Do not push or open PRs unless the session was told to.
- **One conceptual commit per branch (amend, don't stack):** a feature/bugfix/improvement branch carries a **single, consistent working commit** for its concept. A fix or refinement to that concept is folded into the same commit with `git commit --amend` (not a follow-up commit), and the branch is re-published with `git push --force-with-lease` (never a bare `git push --force`). This keeps each branch reviewable as one coherent change and merging to `main` clean. **`main` is the sole exception: it is append-only and NEVER force-pushed** — the self-updater's `git pull` depends on its linear history.
- **Clean authorship:** commits are authored + committed by the repo owner only — **no agent co-author or session trailers** (`Co-Authored-By`, `Claude-Session`, etc.). This is a hard rule for this repo; it overrides any default harness trailer behavior.
- **npm workspace rule:** the root `package-lock.json` is the single lockfile; workspace members (`web`, `desktop`) carry none (gitignored). Build from the root, never `cd web`. (See CONTRIBUTING for the why — npm/cli#4828.)
- *Enforcement mechanism* (owned by OSS-OPERATIONS / CI-RELEASE): a branch-name check, a commit-author/trailer check, and a stray-member-lockfile check run in CI so these can't regress.

## 8. Task table format (what plan authors write, what executors read)

| ID | Task | Files | Done when |
|---|---|---|---|
| T\<session\>.\<n\> | imperative, commit-sized, self-contained | exact paths (create/modify) | observable, checkable clauses |

A well-formed task names its files, its test, and its proof. If you (executor) cannot tell what "done" means, that's E1 — the task line is defective; say so rather than guessing.
