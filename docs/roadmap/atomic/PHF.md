# PLATFORM-HARDENING-FLOORS — atomic plans

**Source plan:** [`PLATFORM-HARDENING-FLOORS`](../plans/PLATFORM-HARDENING-FLOORS.md)  
**Code:** `PHF`  
**Source status:** todo

Enforcement floors, trust seams, and gate ergonomics for the platform. It corrects an unsafe resource-ceiling delivery mechanism (deliver rlimits after exec via a stdlib shim instead of a fork-unsafe preexec_fn), closes the unauthenticated app-backend inbound trust boundary with a proxy HMAC signature, and adds config/inert-surface drift baselines, an offline fake-model E2E harness, ceiling-intersect-profile guardrail wiring, flake root-cause fixes, and docs/aggregate-gate legibility. Most atoms originate here or deliver a specific mechanism correction; cross-plan edges mark work that lands into a plan another owner holds.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PHF-1` | ✅ | Post-exec resource-ceiling shim + spawn tripwires | — | A child reports the ceiling via ulimit -n; the shim imports and runs with no core dependency; config round-trips; ACP sessions still open with multiple MCP servers (no EMFILE regression); adding an unmapped Popen site or an async preexec_fn reds CI naming file:line; validated on the dev gateway (ulimit, bounded fork bomb contained, terminal open with no shim cost, watchdog-revived backend carries the ceiling); the two hazard-site audits either yield a wedge regression test or a recorded finding that they are safe. |
| `PHF-2` | ⬜ | cgroup v2 second enforcement tier | `PHF-1` | On a Linux fixture a fork bomb hits pids.max and dies contained; on macOS one warning states pids/RSS are not enforced; the probe never raises under any environment. |
| `PHF-3` | ✅ | App-backend inbound proxy-signature authentication | — | A direct curl to the backend port is refused; a signed request replayed after the window is refused; a valid proxied request succeeds; the secret file is 0600 and never logged; every first-party backend refuses unsigned requests and the full app-boot path runs green; the doc states what each layer does and does not buy. |
| `PHF-4` | ✅ (#PENDING) | Environment-inheritance allowlist sweep for shell hooks and cron scripts | `EXT:EXECUTION-ISOLATION:D1-allowlist-shape` | A planted secret in the gateway env is absent from a hook child, a cron-script child, and a bash-action child, proven by one regression test per site. |
| `PHF-5` | ✅ | Committed config-schema baseline + drift gate | — | Renaming a config field without regenerating reds CI naming the path; regeneration is byte-identical on re-run; adding a field without regenerating is caught. |
| `PHF-6` | ✅ | Inert-control inventory baseline (writer/reader ratchet) | `PHF-5` | The current inert population is measured and committed; adding a declared-but-unread surface reds CI; each cleanup commit shrinks a counter with a test proving the writer now exists; the forbidden-to-raise doc line is present. |
| `PHF-7` | ✅ | Offline fake-model E2E harness + a11y rail | `EXT:DESIGN-SYSTEM-CONSISTENCY:deferred-axe-per-route-a11y-tail` | A gateway boots on the fake provider and completes a scripted chat turn with no credentials present; make test-e2e runs the browser gate offline and a bare pytest does not run it; every authenticated route is axe-scanned in CI; a deliberately raw call that skips the enforced helper reds the gate; validated with network off and no provider credentials, runtime recorded. |
| `PHF-8` | ✅ (#PENDING) | Ceiling-intersect-profile guardrail wiring | `PHF-1`, `EXT:AUTONOMY-GUARDRAILS:SafetyProfile-S5.2-wiring` | A profile cannot widen the ceiling (test per archetype); an unknown matcher aborts boot with a WHAT/WHY/FIX error; a normpath-on-pattern implementation reds the matcher tests; a real unattended trigger resolves through the headless profile with a live reader; a narrower profile bites and a widening attempt is refused, confirmed from logs/SEL. |
| `PHF-9` | ✅ (#PENDING) | Suite flake root-cause fixes + xdist scheduler decision | `EXT:CI-RELEASE-ENGINEERING:xdist-worksteal-escape-hatch` | The full suite runs 5x consecutively with zero unclosed-database resource warnings and both named tests pass deterministically; a genuine SEL write failure now raises rather than skipping; the scheduler wall-time measurement is recorded and the decision made; both flake memories are updated or deleted. |
| `PHF-10` | ✅ | Docs-lint + plan-hygiene gate | — | A dead link or a stale file:line citation reds CI; the current population is committed (not zero); the plan-hygiene checker reproduces the known stale-header audit findings on a seeded stale header. |
| `PHF-11` | ✅ | Aggregate gates report, they don't short-circuit | — | A tree with three independent failures reports all three in one run; each aggregate prints one result table with every failure visible in a single run. |
| `PHF-12` | ✅ (#PENDING) | Teach the inert-surface census whole-enum iteration — the false-red class its own docstring promised was impossible | `PHF-6` | whole-enum iteration clears every member of the iterated class, across `for`/`async for`, all four comprehension forms, and the `list`/`tuple`/`set`/`frozenset`/`sorted`/`iter`/`reversed`/`enumerate` family, matched bare (`E`) or module-qualified (`mutations.OpKind`) and resolved through the ITERATING file's own imports so one of the seven `Verdict` enums cannot clear another's members; the generator states the detected shapes, the honest trade (more under-reporting bought with the elimination of this false-red class), and value-lookup `E(value)` as the KNOWN remaining false-red shape; `inert-surface-baseline.json` regenerated BY THE TOOL (145 → 140 total, enum 18 → 13) with every departure verified reachable at a named iteration site and nothing entering; fixture-tree tests pin iteration-only NOT flagged, consumed-nowhere flagged, a same-named enum elsewhere NOT cleared, and a vacuity guard that the census still sees >50 enum classes and still reports members; the shrink-only ratchet is untouched and proven to still red on a real new inert member. |
| `PHF-13` | ✅ (#PENDING) | Per-class provenance verdicts for the census's 13 surviving enum surfaces — and the ruling that value-lookup `E(value)` must NOT clear a member | `PHF-12` | every `E(value)` construction site behind the 13 surviving enum surfaces is classified EXTERNALLY REACHABLE / INTERNAL ONLY / DEAD CALL SITE with file:line evidence; the verdicts live in the detector that produces the flags and `PHF-12`'s superseded premise (`judge_contract.py:342` makes `Verdict.REPLAN` reachable) is corrected there; the census rule is deliberately NOT widened, so the shrink-only ratchet is untouched and the regenerated baseline is byte-identical (140 total, enum 13); three tests pin the ruling — value-lookup alone does not clear a member, the audited dead call sites still have no production caller, and the verdict vocabulary is present in the generator — and the pin is proven able to fail by a temporary widening probe (enum 13 → 7, 3 failed) reverted by a targeted edit; every member still flagged carries a one-line reason it is genuinely unreachable, plus what closing it would take, in the plan's execution log |
| `PHF-14` | ✅ | Decompose `config/loader.py` below the ceiling — restore the headroom every config field needs | — | config/loader.py is at most 5400 lines (>= 600 lines of ceiling headroom, i.e. room for several ordinary config fields rather than one) with the extracted sections living in sibling modules under src/personalclaw/config/ and every importer updated — no re-export shim, proven by a RUNTIME import sweep with a stated count, not by mypy (ignore_missing_imports=true cannot see a stranded first-party import); tests/test_structural_baseline.py green including test_the_ceiling_leaves_the_biggest_file_room_for_ordinary_maintenance and test_the_watch_band_is_not_sitting_on_a_cliff, with the ceiling and the watch band UNCHANGED and structural-baseline.json regenerated only for the moved-file rows; tests/test_config_roundtrip.py green and the committed config-schema baseline (PHF-5) shows ZERO schema drift, proving the split moved code without changing one observable config key; and a NEW config field is added end-to-end in the same change as the proof that headroom was really restored — LV-4's learning.identity_report_* field, wired through all four round-trip points plus its frontend control, which also closes LV-4's only unmet clause. |
| `PHF-15` | ✅ | `AppConfig.load()` must be a pure read — and the real-home rail must be able to see a metadata-preserving writer | — | AppConfig.load() performs NO write under any input — proven by a fresh-interpreter subprocess rail (the shape tests/test_import_time_config_writes.py establishes) that seeds a genuinely pre-migration config.json, calls load(), and asserts (mtime_ns, contents) unchanged AND no .bak created, with a vacuity leg that observes the write when the migration is invoked through its new explicit entry point; the migration still runs for real upgrades through that explicit call from a startup path that a test can point at tmp_path, with a regression proving a pre-migration config IS migrated when the gateway boots and IS NOT when a library reader imports; every existing load() caller audited by a RUNTIME import sweep with a stated count (mypy cannot see a stranded first-party import) and none left depending on the write-back side effect; the real-home rail additionally detects a metadata-preserving write — a planted shutil.copy2 of a tracked file is REPORTED, proven by a probe that reds before the fix and passes after, with ALLOWED_RESIDUE still frozenset() and tests/real_home_guard.py's cost documented; and config/loader.py ends at or below its starting line count, since the 6000-line ceiling rail leaves it 100 lines of headroom (see PHF-14). |

## Atom scopes

### `PHF-1` — Post-exec resource-ceiling shim + spawn tripwires

**Status:** todo

Replace the unsafe preexec_fn ceiling-delivery mechanism (it forces a full fork() of the multi-threaded gateway and can wedge a child holding inherited fds like gateway.lock and the listening socket, blocking the event loop with no await point) with a stdlib-only exec shim prepended to argv that setrlimits in the already-exec'd single-threaded child then os.execv's the real target. Add ResourceCeilings plus four profiles: tool (default, full ceiling + oom_score_adj bias to prefer killing agent work), session_host (raises NOFILE to the inherited hard limit for ACP hosts multiplexing many MCP pipes, no OOM bias), build (keeps OOM bias), none (user terminal, no limits/no bias). Add spawn_shim_argv/create_subprocess_limited and wire sandbox.nofile/max_pids/max_rss_mb config 4-point (dataclass + _meta, load, to_dict, write path). Route all agent-influenced async seams (native bash, bash action provider, subagent, app backends, MCP stdio, ACP transport, loop gates/worktree) through the shim; terminal gets none; frontend/service/update spawns operator-exempt. Add two AST tripwires: every subprocess/Popen/run/StdioServerParameters site is in a ceiling-wrapped-or-operator-exempt allowlist, and no async spawn site passes preexec_fn (documented exceptions only). Audit the watchdog-thread backend respawn and the event-loop bash spawn for the pre-existing wedge form. This supersedes and rewrites the EXECUTION-ISOLATION EI-A1/EI-A2/EI-A3 mechanism, whose single-threaded-at-fork premise is false (67 thread-creation sites; backends respawn off a daemon thread).

**Done when:** A child reports the ceiling via ulimit -n; the shim imports and runs with no core dependency; config round-trips; ACP sessions still open with multiple MCP servers (no EMFILE regression); adding an unmapped Popen site or an async preexec_fn reds CI naming file:line; validated on the dev gateway (ulimit, bounded fork bomb contained, terminal open with no shim cost, watchdog-revived backend carries the ceiling); the two hazard-site audits either yield a wedge regression test or a recorded finding that they are safe.

### `PHF-2` — cgroup v2 second enforcement tier

**Status:** todo

Add an opt-in sandbox.cgroup_scopes tier on Linux as a second enforcement layer above the NOFILE floor: systemd-run --user --scope with TasksMax, MemoryMax, and MemorySwapMax=0. Probe once for a unified cgroup hierarchy plus a systemd user session and add the probe line to the doctor. On macOS, non-systemd, or containers, emit exactly one loud warning naming what is not enforced (pids/RSS) while NOFILE still applies; the probe never raises.

**Done when:** On a Linux fixture a fork bomb hits pids.max and dies contained; on macOS one warning states pids/RSS are not enforced; the probe never raises under any environment.

### `PHF-3` — App-backend inbound proxy-signature authentication

**Status:** todo

Close the unauthenticated inbound half of the app-platform trust boundary. App backends bind loopback with no caller check, so any local process that finds the port talks to the backend directly, bypassing the gateway proxy, session auth, and the app permission middleware; loopback binding is a network boundary, not an authorization one. Mint a per-app proxy secret at install/first-boot (apps_dir/<app>/.app_secret, mode 0600) and hand it to the backend by env. Sign every proxied request in the app proxy: X-PersonalClaw-Proxy: <ts>:<hmac> over <ts>:<METHOD>:<path>[?query]:<sha256(body)>. Verify fail-closed in the SDK app-server helper (absent/malformed/stale beyond +-60s/wrong signature -> 401, no route body runs, constant-time compare, denials to SEL). Roll every first-party backend onto the verifying helper (cross-repo, core helper first then apps in the same session). Document honestly that loopback is not authorization and the signature is what makes the permission model hold; add a CHANGELOG entry. Secret mint is also fail-closed: a backend that cannot read its secret does not start rather than starting unprotected.

**Done when:** A direct curl to the backend port is refused; a signed request replayed after the window is refused; a valid proxied request succeeds; the secret file is 0600 and never logged; every first-party backend refuses unsigned requests and the full app-boot path runs green; the doc states what each layer does and does not buy.

### `PHF-4` — Environment-inheritance allowlist sweep for shell hooks and cron scripts

**Status:** done (#PENDING)

Apply the minimal-allowlist child-env shape (PATH, locale, home-equivalent, the three PersonalClaw vars, plus declared needs, with _SENSITIVE_ENV_PREFIXES as the floor) to the agent-influenced spawn sites not already covered: shell hooks and cron scripts. Confirm the bash action provider's existing _scrub_env is the allowlist shape or align it. Reuses the allowlist shape already specified by EXECUTION-ISOLATION's D1; this only extends it to the sites D1 does not name.

**Done when:** A planted secret in the gateway env is absent from a hook child, a cron-script child, and a bash-action child, proven by one regression test per site.

### `PHF-5` — Committed config-schema baseline + drift gate

**Status:** todo

Add scripts/generate_config_baseline.py that walks the existing _meta registry and emits a committed config-baseline.json (flat path list + type + default + sensitive flag), plus a CI job asserting regeneration is a no-op. This catches drift a round-trip test cannot see: a renamed key, a silently dropped _meta, or a field that stopped being written. Generator must be deterministic (byte-identical on re-run).

**Done when:** Renaming a config field without regenerating reds CI naming the path; regeneration is byte-identical on re-run; adding a field without regenerating is caught.

### `PHF-6` — Inert-control inventory baseline (writer/reader ratchet)

**Status:** todo

Add tests/test_inert_surface_baseline.py plus a committed inert-surface-baseline.json recording, for each declared surface (config keys, enum members, registered kinds/runtimes, _EDITABLE_CONFIG entries, SDK exports), whether a writer and a reader exist. This targets the recurring defect where something is declared and nothing on the other side of the seam consumes or produces it while hand-built test state hides it. Per-file counters may only shrink; ship at the measured population (not zero, since a never-run gate given teeth at zero is an outage) with a doc line forbidding raising a number to go green. Drive the top offenders down one file per commit, each commit proving the writer now exists. SafetyProfile is expected to appear and is left for PHF-8 to fix.

**Done when:** The current inert population is measured and committed; adding a declared-but-unread surface reds CI; each cleanup commit shrinks a counter with a test proving the writer now exists; the forbidden-to-raise doc line is present.

### `PHF-7` — Offline fake-model E2E harness + a11y rail

**Status:** todo

Build a deterministic scripted fake model provider fixture (scripted responses, tool-call emission, zero network) usable as a real bound provider, reusing the empty seed fixture path. Add make test-e2e that boots a real gateway on the fake provider with a seeded authenticated session then runs web/e2e/ — skipped unless PERSONALCLAW_E2E=1, forced serial with addopts cleared, its own high timeout, and no coverage instrumentation on the subprocess gateway. Mount the deferred axe-per-route accessibility rail on the harness and add it to CI so every authenticated route is axe-scanned and a new WCAG AA violation reds the build. Add a strict-mode env flag that turns one persistence/fencing convention (on-loop persistence, atomic writes, or is_fenced) into a hard failure for the gate's duration. Port the manual UI-validation click-path into web/e2e/ specs so it is reproducible in CI.

**Done when:** A gateway boots on the fake provider and completes a scripted chat turn with no credentials present; make test-e2e runs the browser gate offline and a bare pytest does not run it; every authenticated route is axe-scanned in CI; a deliberately raw call that skips the enforced helper reds the gate; validated with network off and no provider credentials, runtime recorded.

### `PHF-8` — Ceiling-intersect-profile guardrail wiring

**Status:** ✅ done (#PENDING)

Wire the SafetyProfile family, which shipped with zero non-test callers (a declared decision object nothing consults). Add a Ceiling loaded once at boot from an operator-owned path the agent process cannot weaken; resolve(ceiling, profile) returns the intersection under tightest-wins (the profile may only narrow). Implement four archetypes each with one compose function (ScopedRuleset, OrdinalControl, CapabilityGate, ScopedMap), dispatching on archetype not scope name so adding a scope is data, not engine code; matchers and ordinal scales live in enforcer-owned registries; an unknown matcher aborts governance boot fail-closed. Encode the path-matcher rule as table-driven tests: normalize only the queried item (expand ~ and $VAR, then abspath) and never run the pattern through normpath (normpath treats */** as ordinary segments and collapses an adjacent .. against them, e.g. /a/**/../b -> /a/b, silently dropping the ** and widening an allow or shrinking a deny); cover /a/**/../b, a .. traversal against an allow-prefix, and a relative item against an absolute deny. Wire profile_for_session into the three dispatch seams plus spawn so the guardrail success criterion holds in code, proven by driving a real unattended trigger rather than a constructed object; composes with PHF-1 (profile picks tools/egress, the sandbox ceilings pick blast radius). Owner decision required on the ceiling's trust-root path and how it is protected from the agent on a single-user machine; document what the layer does and does not buy.

**Done when:** A profile cannot widen the ceiling (test per archetype); an unknown matcher aborts boot with a WHAT/WHY/FIX error; a normpath-on-pattern implementation reds the matcher tests; a real unattended trigger resolves through the headless profile with a live reader; a narrower profile bites and a widening attempt is refused, confirmed from logs/SEL.

### `PHF-9` — Suite flake root-cause fixes + xdist scheduler decision

**Status:** done (#PENDING)

Fix two suite flakes at their root, then decide the parallel scheduler with measurements. Knowledge-merge flake: close the KnowledgeStore connection in fixture teardown (no rerun, no xfail) so the suite runs clean of unclosed-sqlite resource warnings. Subagent SEL flake: the SEL audit call sits inside a swallowing except Exception, so contention makes the patched mock never fire; narrow the except so a genuine audit-write failure surfaces instead of vanishing, and give the two tests an isolated home — a silently-swallowed SEL write is a security-audit gap, not a test problem. Only after the root causes are gone, decide the xdist scheduler last and with measurements: switch --dist worksteal to loadgroup only if the suite wall-time regression is acceptable, recording the before/after either way (the serialization group mark is the real deliverable, not the scheduler choice). Reconcile the two flake memories so a fixed flake is not left documented as pre-existing.

**Done when:** The full suite runs 5x consecutively with zero unclosed-database resource warnings and both named tests pass deterministically; a genuine SEL write failure now raises rather than skipping; the scheduler wall-time measurement is recorded and the decision made; both flake memories are updated or deleted.

### `PHF-10` — Docs-lint + plan-hygiene gate

**Status:** todo

Add scripts/docs_lint.py, make docs-lint, and a blocking CI job checking dead relative links, missing anchors, file.py:NNN citations whose file no longer exists, and code-fence language tags; ship at the measured population with a shrink-only allowlist rather than at zero. Extend it to plan hygiene in report-only mode: a plan whose Status: DONE header has unchecked task rows, or whose header contradicts its Execution log, is reported — the log and the code win over the header, so this ratchets rather than blocks.

**Done when:** A dead link or a stale file:line citation reds CI; the current population is committed (not zero); the plan-hygiene checker reproduces the known stale-header audit findings on a seeded stale header.

### `PHF-11` — Aggregate gates report, they don't short-circuit

**Status:** todo

Convert run_prepush.sh from a short-circuiting && chain into a runner: run every independent check, capture all output, print one result table, and exit non-zero if any failed, with the verdict computed from the collected data rather than inline. Do the same for the website's test:ci aggregate, respecting the repo-owned pre-push hook and never weakening an assertion to go green. An && chain of N independent checks reports only the first failure, so a tree with several unrelated problems costs multiple push/wait rounds to discover over independent measurements of the same commit.

**Done when:** A tree with three independent failures reports all three in one run; each aggregate prints one result table with every failure visible in a single run.


### `PHF-12` — Teach the inert-surface census whole-enum iteration — the false-red class its own docstring promised was impossible

**Status:** ✅ done (#PENDING)

Corrects `PHF-6`'s census (`scripts/generate_inert_surface_baseline.py`), whose enum detector documented its own error direction as "never a false red"

**Done when:** whole-enum iteration clears every member of the iterated class, across `for`/`async for`, all four comprehension forms, and the `list`/`tuple`/`set`/`frozenset`/`sorted`/`iter`/`reversed`/`enumerate` family, matched bare (`E`) or module-qualified (`mutations.OpKind`) and resolved through the ITERATING file's own imports so one of the seven `Verdict` enums cannot clear another's members; the generator states the detected shapes, the honest trade (more under-reporting bought with the elimination of this false-red class), and value-lookup `E(value)` as the KNOWN remaining false-red shape; `inert-surface-baseline.json` regenerated BY THE TOOL (145 → 140 total, enum 18 → 13) with every departure verified reachable at a named iteration site and nothing entering; fixture-tree tests pin iteration-only NOT flagged, consumed-nowhere flagged, a same-named enum elsewhere NOT cleared, and a vacuity guard that the census still sees >50 enum classes and still reports members; the shrink-only ratchet is untouched and proven to still red on a real new inert member.

**Design**

The census is the instrument that decides which cleanups are worth doing — five recent atoms
were picked straight off `inert-surface-baseline.json` — so its accuracy is load-bearing, and
its enum detector was **wrong in the one direction it swore it could not be**. The detector
stated its rule and its error direction explicitly:

> An enum member whose name is never accessed as an attribute anywhere in `src/` is declared
> and never referenced […] Iteration-only consumption (`for m in E:`) is not detected; that is
> the accepted under-reporting direction (**never a false red**).

Under-reporting is the harmless direction; a false red is not, because it sends someone to
"wire up" code that already works. `workflows/publish.py` declares `Lineage` =
`SOURCE`/`INFORMED_BY`/`RELATED` and `parse_publish` (publish.py:136) validates
author-supplied edges against the whole enum **by iteration**:

```python
if edge not in {e.value for e in Lineage}:
    return None, f"unknown lineage edge {edge!r}; expected {[e.value for e in Lineage]}"
```

So a template author writes `lineage: {informed_by: [...]}`, it validates, and
`flatten_lineage` persists it as a `lineage_informed_by` scalar on the artifact event —
`upsert_plan` only adds the `SOURCE` run marker on top of whatever the spec declared. Both
members are reachable and functional, and the census listed both as inert. Exactly the false
red the docstring said could not happen.

The correction is that **whole-enum iteration reaches every member by construction**, so one
iteration site clears the whole class. Two things make that safe rather than sloppy:

*Resolution is import-aware, not name-based.* `src/` declares seven distinct `Verdict` enums.
`workflows/verify.py:76` runs `for verdict in Verdict:` over **its own** `Verdict`; a name-only
index would have cleared `workflows/judge_contract.py`'s `Verdict.REPLAN` on the strength of an
unrelated namesake, silently deleting a real finding. Each iterated name is resolved through
the iterating file's own bindings (local `class`, `from x import E [as A]`, and module aliases
so `mutations.OpKind` / `detectors.Skip` resolve), falling back to every same-named class only
when resolution fails — the fallback lands on the under-reporting side, never on a false red.

*The trade is stated, not hidden.* Clearing a whole class per iteration site buys the
elimination of this false-red class with MORE under-reporting: a member with no producer goes
unreported once anything iterates its class. That is the right trade for an instrument whose
output is a work queue. The rule "when a reported surface turns out to be reachable, teach the
detector the shape — never hand-edit the baseline, never relax the forbidden-to-raise rule" is
now written in the generator, because this atom is the first instance of it.

**DISCOVERY — a second, distinct false-red class remains, deliberately unfixed here.**
Value-lookup construction `E(value)` also makes every member reachable when the value is
externally supplied: `judge_contract.py:342` does `Verdict(str(raw.get("verdict", "")).upper())`
on **model-emitted** text, so `REPLAN` is reachable and still reported. Six of the thirteen
surviving enum surfaces sit on classes constructed that way (`MemoryTier`, `MemoryScope`,
`DependencyType`, `Status`, `Actor`, `Verdict`). Unlike iteration, `E(value)` does not prove
reachability on its own — a deserializer fed only from internally-written state proves nothing
— so separating "author/model-supplied value" from "our own round-trip" is a judgment call
worth its own atom rather than a widening of this one. Named in the detector docstring so the
next reader is not misled, and left in the baseline rather than blessed away.

**Implementation plan**

1. `_module_file` / `_absolute_import_module` — resolve a dotted `personalclaw.*` module to
   its `src/` file, including relative `from .models import X` against the importing file's own
   package. Returns `None` for anything outside `src/` so a fixture tree cannot raise.
2. `_enum_name_bindings` — per-file symbol table: local enum name → declaring file, and local
   alias → module file (for `mutations.OpKind`). This is the piece that keeps the seven
   `Verdict`s apart.
3. `_iterated_expressions` — every expression a module iterates as a whole: `for`/`async for`,
   all four comprehension forms, and `_ITERATING_BUILTINS` applied to it. `E(value)` and
   `value in E` are deliberately absent (see the DISCOVERY above); `isinstance(x, E)` was
   excluded after an early pass wrongly counted it.
4. `_iterated_enum_classes` — `(declaring file, class)` for every enum iterated anywhere in
   production `src/`, resolved through 2, with the clear-all-namesakes fallback.
5. Split the detector into `_inert_enum_members` (path-typed, so a fixture tree can drive it)
   and `_inert_enum_surfaces` (the repo-relative wrapper). A member is inert only when it has
   NEITHER an attribute reader NOR an iterated class.
6. Rewrite the module + detector docstrings: the new rule, the detected shapes, the
   deliberately-undetected shapes, the honest trade, and the teach-the-detector rule.
7. Regenerate `inert-surface-baseline.json` with the tool, then account for every delta line
   by line against the code.
8. Tests: fixture-tree pins for all three shapes cleared, consumed-nowhere still flagged, the
   same-named-enum collision, the `Lineage` regression on the real tree, and a vacuity guard.
   Prove the ratchet still bites by temporarily adding a member to a shipped non-iterated enum.

### `PHF-13` — Per-class provenance verdicts for the census's 13 surviving enum surfaces — and the ruling that value-lookup `E(value)` must NOT clear a member

**Status:** ✅ done (#PENDING)

A CLASSIFICATION atom, not a feature atom: it makes the census's verdict on each surviving enum
surface correct and evidence-backed, and it does not build the missing writers.

**Done when:** every `E(value)` construction site behind the 13 surviving enum surfaces is
classified EXTERNALLY REACHABLE / INTERNAL ONLY / DEAD CALL SITE with file:line evidence; the
verdicts live in the detector that produces the flags and `PHF-12`'s superseded premise is
corrected there; the census rule is deliberately NOT widened, so the shrink-only ratchet is
untouched and the regenerated baseline is byte-identical (140 total, enum 13); three tests pin the
ruling and the pin is proven able to fail by a temporary widening probe reverted by a targeted
edit; every member still flagged carries a one-line reason it is genuinely unreachable.

**Design**

`PHF-12` fixed one false-red class (whole-enum iteration) and named a second one it did not fix:

> Value-lookup `E(value)` also makes every member reachable when the value is external:
> `judge_contract.py:342` does `Verdict(str(raw.get("verdict", "")).upper())` on **model-emitted**
> text, so `Verdict.REPLAN` is reachable and still reported.

**That premise is wrong, and the audit is what shows it.** `judge_contract.py:342` lives inside
`validate_verdict`, and **nothing in `src/` calls `validate_verdict`** — `engine.py:1510`
deliberately RESTATES the aggregation rule rather than importing it, and only tests import the
function. Model-emitted text never reaches that constructor. The same holds for
`judge_actors.py:84` (`Actor(...)`, reached only from `resolve_transition`, which no production
file calls) and for `judge_contract.py:224` (`enum_cls(str(value))` for `Ratchet`, inside the
equally uncalled `hints_from_dict`).

So `E(value)` proves reachability only when BOTH hold: **the construction executes in production**,
and **its value crosses a trust/authoring boundary**. Auditing all six sites:

| Site | Verdict |
|---|---|
| `tasks/models.py:74` `DependencyType(raw_type)` | EXTERNALLY REACHABLE — `POST /api/tasks` body |
| `memory_record.py:216`/`:218` `MemoryTier`/`MemoryScope` | INTERNAL ONLY — our own SQLite rows |
| `workflows/confirmation.py:177` `Status(...)` | INTERNAL ONLY — our own persisted records |
| `workflows/judge_actors.py:84` `Actor(...)` | DEAD CALL SITE |
| `workflows/judge_contract.py:342` `Verdict(...)` | DEAD CALL SITE |
| `judge_contract.py:224` `enum_cls(str(value))` (`Ratchet`) | DEAD CALL SITE, and loop-bound |

A syntactic `E(value)` rule would therefore clear six classes of which **exactly one** is genuinely
reachable — measured, not guessed: the widening probe took enum 13 → 7. A false CLEAR is worse than
the over-report it replaces, because it buries a genuine gap inside every internal deserializer and
it passes the shrink-only ratchet silently (the count goes DOWN). And "provably outside" is not a
cheap deterministic AST rule: the one genuine case needs four interprocedural hops across three
modules (`request.json()` → `create_task(**body)` → `_coerce_dependencies` → `from_dict`).

**So the census rule is NOT widened.** The deliverable is the verdict table itself, written where
the next reader lands (the detector docstring + the plan log), plus rails that keep it honest.

**Implementation plan**

1. Enumerate every `E(value)` site behind the 13 surviving surfaces by AST (`Call` whose func names
   an enum class), then trace each value to its origin: request body, model output, on-disk
   template, or our own round-trip.
2. Check EXECUTION before provenance — a construction with no production caller proves nothing,
   whatever it reads. This is what overturned `PHF-12`'s premise.
3. Rewrite the module + `_inert_enum_members` docstrings: the ruling, the per-site verdict table,
   the 5-of-6 false-clear arithmetic, and the pointer to the rails.
4. Add three tests: a fixture-tree pin that a value-lookup-only member is STILL flagged; a
   real-tree pin that the audited dead call sites still have no production caller (it reds when one
   is wired up, which is the moment to re-verdict); and a docstring pin for the verdict vocabulary
   that also refuses `PHF-12`'s superseded claim.
5. Prove the pin can fail: temporarily teach the detector to clear on any `E(value)`, confirm the
   red (and record the 13 → 7 widening figure), revert by a targeted edit — never `git checkout --`.
6. Regenerate the baseline with the tool (byte-identical — no `src/` behaviour changed), and record
   the per-member reason each surviving flag is a real finding in the plan's execution log.

### `PHF-14` — Decompose `config/loader.py` below the ceiling

**Status:** ✅ implemented 2026-08-28 (#PENDING). The table row above deliberately still reads
`⬜`: `tests/test_roadmap_atomic_status_sync.py::test_every_row_mark_agrees_with_the_dag_status`
requires the row mark and `dag.json`'s status to agree, and this change is scoped not to touch
`dag.json`. Flip both in one edit at merge — `⬜` here is a sync artefact, not a verdict on the
work. Result: `loader.py` **5652 → 4285** (−1367) across five
sibling modules under `src/personalclaw/config/`; ceiling headroom **348 → 713** on the rail's
holder (which the split handed to `workflows/controller.py` at 5287) and **1715** on `loader.py`
itself; watch-band headroom unchanged at **179**. `structural-baseline.json` regenerated
**byte-identical** — no row changed at all. Equivalence proven differentially: `to_dict()`, the
JSON schema and all **393** schema-registry entries are byte-for-byte identical to `origin/main`.
Runtime import sweep **1056 modules, 0 failures**. See the plan's execution log for the two
premise corrections (the atom's line count and its clause-4 proof field) and the gate-breadth
regression the split exposed.

The floor that PHF-5's and PHF-8's own rails predicted and that has now arrived. config/loader.py is 5900 lines against the ABSOLUTE 6000-line SIZE_CEILING_LINES, and tests/test_structural_baseline.py::test_the_ceiling_leaves_the_biggest_file_room_for_ordinary_maintenance asserts ceiling - max_file_lines >= 100. Headroom is exactly 100, so ONE added line reds the gate — and that test's own docstring names this file and this exact scenario ('adding one boolean toggle would red CI and demand a 5,427-line split as its price'). The file was 5427 when that rail was written; it has since grown 473 lines and spent all of it. Because the config round-trip contract (dataclass + _meta, load(), to_dict(), a write path, a frontend control) touches loader.py on EVERY new field, the whole remaining roadmap is blocked from adding user-facing configuration. Extract cohesive per-domain config sections into sibling modules under src/personalclaw/config/ and update their importers, following the src/personalclaw/agents/native/decision_tool_defs.py precedent created for this same rail at the 2800-line watch band. Clean break: no re-export shim. NOT in scope: raising the ceiling, widening the watch band, or regenerating the structural baseline to make the red go away — each of those retires the rail instead of paying it.

**Why this is a floor, not a cleanup.** The config round-trip contract touches `loader.py` on every
new field, so with zero headroom the file is a chokepoint on *all* remaining user-facing work, not
just on the next feature. `LV-4` is already parked on it by name, and three atoms in flight on
2026-08-26 each had to be told not to add a line to it. This is the rail working as designed — it
refused a silent widening and demanded the split instead — so the split is the payment, and raising
the ceiling or regenerating the baseline to clear the red would retire the rail rather than pay it.

**Implementation plan**

1. Measure first: per-section line counts inside `loader.py`, so the extraction is chosen from the
   distribution rather than from the alphabet. Record the counts in the plan log.
2. Extract the largest cohesive per-domain sections into sibling modules under
   `src/personalclaw/config/`, following `src/personalclaw/agents/native/decision_tool_defs.py` —
   created for this same rail at the 2800-line watch band, and the right precedent to copy.
3. Update every importer directly. **No re-export shim**: a shim would keep `loader.py` long and
   leave two names for one thing, which the clean-break tenet refuses.
4. Prove nothing was stranded by a RUNTIME import sweep with a stated count. `mypy` cannot see a
   stranded first-party import because `ignore_missing_imports` is true.
5. Prove the split changed no observable configuration: `tests/test_config_roundtrip.py` green and
   `PHF-5`'s committed config-schema baseline showing ZERO drift.
6. Regenerate `structural-baseline.json` **only** for the moved-file rows, with `SIZE_CEILING_LINES`
   and the watch band untouched, and check the watch-band cliff rail still has its `>= 100` headroom
   after the new modules appear — a 2700-line extract would simply move the problem.
7. Then add `LV-4`'s `learning.identity_report_*` field end to end in the same change. Landing the
   split without spending some of the headroom leaves the claim untested; landing the field is the
   proof.

### `PHF-15` — `AppConfig.load()` must be a pure read

Two defects found together on 2026-08-27 while root-causing a CI-only real-home-rail failure, where the second is what hid the first. (1) AppConfig.load() IS NOT A PURE READ: when needs_migration is true it does shutil.copy2(path, path.with_suffix('.json.bak')) then cfg.save() (config/loader.py, inside load()'s try, best-effort with a warning on failure). So ANY caller that merely reads config mutates the user's config.json and drops a .bak beside it — including a module imported for an unrelated reason. That delivery mechanism was fixed separately (mcp_core's module-level _API constant became a call-time _api_base(), the 4th instance of the import-time-constant shape tests/conftest.py::_isolate_real_home_writers documents as beyond a fixture's reach), but the impurity itself remains and any future reader re-arms it. (2) THE REAL-HOME RAIL UNDER-REPORTS AGAINST copy2: the walk detects changes by mtime-since-session-start, and shutil.copy2 preserves the SOURCE mtime, so the .bak the migration writes looks older than the session and is invisible. That is why CI reported '1 entries changed' when two things changed, and it is a general blind spot for any metadata-preserving writer, not a quirk of this one. Both are invisible on a developer machine: the write only fires when config.json exists AND is pre-migration, and a developer's config is already migrated, so the rail is green locally however broken the code is. NOT in scope: deleting the migration itself (it is load-bearing for real upgrades) or redesigning the rail's single-walk performance choice.

**How it was found, because the path matters more than the bug.** CI red on `PP-16`'s branch with
`real-home rail FAILED: 1 entries under /home/runner/.personalclaw changed` → `modified config.json`.
Every one of the ~27k tests had passed. The writer was captured by wrapping `atomic_write` /
`Path.write_text` and running the suite against a relocated `HOME`, which produced a traceback rooted
in **pytest collection** — before any test, fixture or conftest hook exists:
`import personalclaw.mcp_artifacts` → `mcp_core._API = _resolve_api_base()` → `AppConfig.load()` →
`cfg.save()`. Exactly one such write occurred in the whole suite.

**Both halves are invisible locally**, which is why this needs a rail rather than a fix alone. The
write only fires when `config.json` **exists AND needs migration**; a developer's config is already
migrated, so `needs_migration` is `False` and the rail is green however broken the code is. And
`copy2` preserving mtime means the `.bak` looks older than session start, so the rail saw one changed
entry where two things changed.

**Implementation plan**

1. Give the migration an **explicit entry point** and call it from the startup path only. `load()`
   returns the migrated object in memory; it does not persist.
2. Audit every `load()` caller with a **runtime** import sweep and state the count. Any caller that
   silently depended on persistence is a finding to record, not to paper over.
3. Rails: a fresh-interpreter subprocess test that `load()` writes nothing, and a boot test that the
   migration **does** run — the second is the vacuity leg for the first, and without it "no write"
   is trivially satisfiable by deleting the migration.
4. Teach the real-home rail to see a metadata-preserving write. Prove it with a planted `copy2` that
   reds before and passes after. `ALLOWED_RESIDUE` stays `frozenset()`.
5. Keep `config/loader.py` at or below its starting length — it has 100 lines of ceiling headroom
   (`PHF-14`), and moving code out is the direction of travel anyway.
