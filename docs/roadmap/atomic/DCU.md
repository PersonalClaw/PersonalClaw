# DESKTOP-COMPUTER-USE — atomic plans

**Source plan:** [`DESKTOP-COMPUTER-USE`](../plans/DESKTOP-COMPUTER-USE.md)  
**Code:** `DCU`  
**Source status:** todo

Delivers native desktop GUI automation that lets the agent read and drive the operator's own desktop applications through the OS accessibility layer — enumerate on-screen apps, walk a window into an indexed accessibility tree, then act on an element by index (press, type, set value, scroll, named action), with a coordinate path reserved for canvas/custom-drawn UI. macOS ships first (AX API) with honest typed refusals on Windows/Linux until their drivers land. The entire safety floor — an out-of-band keystone enable, element-index default so the real cursor never moves by accident, a target-app allowlist, SEL audit, and the approval ladder — lands before any capability is usable.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DCU-1` | ✅ | Keystone out-of-band enable-state | — | With the enable file absent, every computer-use tool refuses with a WHAT/WHY/FIX message pointing to the out-of-band enable step; no tool or config path can flip the state. |
| `DCU-2` | ✅ | Target policy, input-target screen, SEL-audit gate | `EXT:AUTONOMY-GUARDRAILS:SEL audit + safety profile` | Driving a non-allowlisted app refuses; typing or set-value into a secure/password field refuses; every attempt, allowed or refused, produces a SEL record. |
| `DCU-3` | 🟡 | macOS accessibility driver (indexed AX tree) | `DCU-1`, `EXT:SECURITY-HARDENING:OS-input-layer class-B/S review` | With the enable on, snapshotting a TextEdit window then AXPress-ing a button by index and typing into a field succeeds without the pointer moving; a stale index (past TTL or changed fingerprint) refuses and forces a re-snapshot. |
| `DCU-4` | ⬜ | Thin stdio shim, in-gateway dispatch, tool surface + ceilinged spawn | `DCU-1`, `DCU-2`, `DCU-3`, `EXT:PLATFORM-HARDENING-FLOORS:ceilinged driver subprocess`, `EXT:AUTONOMY-GUARDRAILS:approval ladder in dispatch` | The agent lists apps and clicks an element by index end-to-end; the shim holds no OS handles; the driver spawn carries the resource ceiling; V1 holds and is recorded — a real app driven by element index with the pointer staying put, a secure-field refusal, SEL records present, and an absent enable file blocking everything. |
| `DCU-5` | ✅ | Approval-ladder integration for desktop drive | `DCU-4`, `EXT:AUTONOMY-GUARDRAILS:approval ladder + unattended-profile grant` | An unattended run without the grant refuses and notifies; an interactive run prompts; validated. |
| `DCU-6` | 🟡 | Windows/Linux honest typed refusals | `DCU-4` | On non-macOS, every computer-use tool returns a typed refusal naming the platform; no silent no-op; validated. |
| `DCU-7` | ⬜ | Human-facing live-view + cursor-motion overlay | `DCU-4` | The views render; neither adds any agent capability — asserted by confirming the tool surface is unchanged with the views on; validated. |

## Atom scopes

### `DCU-1` — Keystone out-of-band enable-state

**Status:** done

New computer_use package with enable_state.py (§3.1): an out-of-band enable file that the agent process can neither read nor write (not a config field the agent can PATCH), plus enable_state.is_enabled() as the first check in the dispatch chain. No prompt, tool, or chat instruction can flip it — it is the single hardest gate and the whole capability is OFF until the operator turns it on out-of-band.

**Done when:** With the enable file absent, every computer-use tool refuses with a WHAT/WHY/FIX message pointing to the out-of-band enable step; no tool or config path can flip the state.

**DONE.** `src/personalclaw/computer_use/enable_state.py` owns the keystone, built on the
`guardrails/ceiling.py` trust model rather than a new one: the enable document lives at
`$PERSONALCLAW_HOME/governance/computer_use.enable.json` (the directory the ceiling already owns,
so the two share one trust root and one denylist entry), overridable to an absolute path by
`PERSONALCLAW_COMPUTER_USE_ENABLE_FILE` so an operator can put it on a root-owned `0444` file
outside the agent's home. It must contain exactly `{"version": 1, "enabled": true, "apps": ["TextEdit"]}`. A
touch-a-marker file was rejected on measurement: as a marker, an empty file and a half-flushed
write both arm the machine — the `empty-file` and `half-flushed-write` cases in the fail-closed
corpus are that decision made testable. Fourteen malformed shapes (non-JSON, wrong root type,
unknown version, unknown key, `"true"`, `1`, `false`, missing flag) all resolve to OFF, and
`enabled` is compared with `is True` rather than for truthiness. An unenforced key is REFUSED
rather than ignored: `{"enabled": true, "only_apps": ["Mail"]}` means *on, narrowed*, so honouring
the flag while dropping the scope would grant strictly more than was asked.

`require_enabled(tool)` raises `ComputerUseDisabled` carrying a typed `AgentError`
(`ERR_COMPUTER_USE_DISABLED`, registered in `errors.ERROR_CODES`) whose FIX names the resolved
path, the exact bytes, the restart and the env override — never a silent no-op, because a no-op
reads to a model as "the click landed". The state is read once and cached, so neither a tamper nor
a legitimate enable changes the reach of the running process; arming costs a restart the operator
performs themselves. `gateway.py`'s `run()` calls `ensure_computer_use_boot()` immediately after
`ensure_governance_boot()`, SEL-auditing the resolved source + digest + outcome once per run —
that is the live call site and the tamper evidence, and unlike governance it never aborts, because
OFF is the normal state.

⚠️ **The tool population is EMPTY today, and this atom is scoped honestly around that.** `DCU-4`
ships the tool surface, the stdio shim and the in-gateway dispatch chain; `DCU-3` ships the macOS
driver. So "every computer-use tool refuses" is proven over a *fixture* tool that routes through
the real guard, plus two rails that arm the clause for the population that does not exist yet:
rail A asserts by AST that every module-level `computer_*` function under `computer_use/` calls the
guard as its **first** statement (proven to detect — the scanner is run against an unguarded twin
and against a guard-placed-after-the-work variant), and rail B pins every public function/class in
the package, closing rail A's naming gap so a dispatch surface that abandons the convention still
trips something. Both carry an explicit vacuity marker
(`test_the_computer_use_tool_population_is_currently_empty`) that states the size is 0 out loud and
reds the moment `DCU-4` adds the first tool.

No config field was added — a field the agent can PATCH is exactly what §3 floor 1 forbids — and
that is proven three ways: absent from `_EDITABLE_CONFIG`, absent from `AppConfig` entirely, and a
census showing `enable_state.py` is the **only** module in the shipped package that even mentions
the filename or the env var (so no handler, CLI or provider can write it). 42 tests in
`tests/test_computer_use_enable_state.py`; 8 mutations were run and every one reded the intended
rail. One found a real defect: `require_enabled` originally read `state.enabled` directly, so
forcing `is_enabled()` to return True left every refusal test GREEN — two readers of one flag. It
now reads the decision through `is_enabled()`, the one check the plan names, and the same mutation
reds 20 tests.

### `DCU-2` — Target policy, input-target screen, SEL-audit gate

**Status:** todo

policy.py (§3.3-3.4): a self-plus-operator target-app allowlist via check_app, and a secure/password-field and sensitive-text screen via check_input_target run before any type/set-value; plus gate.py where require_computer_use records every attempt to the security event log (records, does not decide).

**Done when:** Driving a non-allowlisted app refuses; typing or set-value into a secure/password field refuses; every attempt, allowed or refused, produces a SEL record.

### `DCU-3` — macOS accessibility driver (indexed AX tree)

**Status:** todo

macOS accessibility driver over ctypes FFI (macos_driver.py, macos_ffi.py, types.py) per §2/§3.2: element walk into an indexed AX tree with TTL + fingerprint; AXPress activation by element index (no pointer involved); type/set-value/scroll/named-action; a located coordinate path that posts to the target process (CGEventPostToPid) for canvas/custom-drawn UI; and the explicit real-cursor global warp behind its own named method and a distinct SEL tool_kind that auto never resolves onto. Element-targeted, non-pointer input is the default and the only thing on by default.

**Done when:** With the enable on, snapshotting a TextEdit window then AXPress-ing a button by index and typing into a field succeeds without the pointer moving; a stale index (past TTL or changed fingerprint) refuses and forces a re-snapshot.

**PARTIAL (2026-08-26) — implementation landed; one clause is blocked on an OS permission a human
must grant.** `types.py`, `macos_ffi.py` and `macos_driver.py` ship over **ctypes FFI as §3.2
specifies, adding no dependency at all** — every symbol is in a system framework, so `pyobjc` was
neither needed nor added. `driver_host.resolve_driver("Darwin")` now returns a module instead of
`None`, which is the call site this atom exists to land.

*Proven on the authoring machine:* `op_list_apps` against the real OS (75 bundled apps, and it
needs no permission by design so an operator can discover the name to allowlist before granting
anything); every FFI symbol binding; the real pointer read; the real ceilinged spawn reaching the
real driver end-to-end through `computer_dispatch`; and the accessibility refusal as the **real OS
answer** (`AXIsProcessTrusted()` False, `kAXErrorAPIDisabled`/-25211). Staleness is proven from
both sides — the dispatch's TTL and fingerprint were already railed by `DCU-4`, and the driver adds
the act-moment re-walk the dispatch cannot make for itself (its own re-walk precedes the
secure-field screen and the SEL row, leaving a window in which the operator can drag the window).
"The pointer does not move" is asserted as the *set of OS calls* each op makes against a recording
double, with a rail proving that recording can fail.

*NOT observed, and unblockable only by hand:* the done_when's "snapshotting a TextEdit window then
AXPress-ing a button … **succeeds**". macOS gates the AX API behind TCC, whose database is
SIP-protected, so no code can grant it. **What a human must click:** System Settings → Privacy &
Security → Accessibility → `+` → add the python binary running the gateway (not the terminal
hosting it) → restart the gateway. With that done, two tests already written flip from asserting
the refusal branch to asserting a real indexed tree, with no code change:
`test_the_accessibility_permission_refusal_is_the_real_os_answer` and
`test_the_real_spawn_reaches_the_real_driver_and_its_code_survives`. The row stays `todo` until
that walk-through is recorded.

### `DCU-4` — Thin stdio shim, in-gateway dispatch, tool surface + ceilinged spawn

**Status:** todo

The stdio MCP shim (cli.py) that resolves session identity and forwards while holding no OS handles; in-gateway dispatch running the full chain — enable / policy.check_app / index freshness+fingerprint / check_input_target / SEL gate / platform driver / re-snapshot+redact (service.py, §2); the tool surface registered in tools.py (computer_list_apps, computer_snapshot, computer_click, computer_type, computer_set_value, computer_scroll, computer_perform_action) with MCP registration; and the driver running as a ceilinged subprocess so a wedged/looping driver is kernel-bounded (§3.5). Includes end-to-end validation V1.

**Done when:** The agent lists apps and clicks an element by index end-to-end; the shim holds no OS handles; the driver spawn carries the resource ceiling; V1 holds and is recorded — a real app driven by element index with the pointer staying put, a secure-field refusal, SEL records present, and an absent enable file blocking everything.

### `DCU-5` — Approval-ladder integration for desktop drive

**Status:** todo

Wire the approval ladder into policy (§3.4) so an unattended profile may drive the desktop only with a creation-time grant, while interactive sessions receive the approval prompt.

**Done when:** An unattended run without the grant refuses and notifies; an interactive run prompts; validated.

**DONE (2026-08-27) — every clause met. NO new rung name was minted.** `guardrails/autonomy.py`
keeps its four rungs; what this adds is ONE declaration, `rungs.COMPUTER_USE_DRIVE`
(`computer_use.drive`), at the existing `one_tap` — floor and ceiling both, `leaves_machine=True`,
no `providers`, following the `_AFFORDANCE_SPECS` precedent for a governed behaviour nothing
dispatches through the action-provider registry. The reasoning is `action.browse`'s one notch
harder: not `autonomous` (a click in somebody's mail client is an irreversible external write),
not `auto_with_undo` (that rung promises a reversal handle and no driver op here can un-press a
button), not `draft_only` (the operator already armed this out-of-band, per application — with-
holding every drive after that is a capability wearing a control's clothes), ceiling == floor so
no accumulated track record can take the ask away from the highest-consequence capability in the
product. `one_tap` → `ROUTE_ASK` is also what makes the "and notifies" half work at all:
`announce_withheld` files an `agent_request` for `ask`/`draft` and **nothing** for a route that
executes, which is why a rail pins the declaration rather than trusting it.

The seam is `policy.check_autonomy`, step **4b** of the chain — the insertion point `DCU-4`'s log
reserved, between `check_input_target` and the approved SEL row, inside the audited `try` so a
refusal is recorded against the app it was aimed at. It makes **two** reads, both existing
contracts: the ladder route, and `profile_for_session(...).approval`. *The ladder cannot make the
second read, and that is a property of the ladder rather than an omission:*
`rung_ceiling_for_profile` narrows an unattended run to `auto_with_undo`, which is ABOVE
`one_tap`, so the composed rung is `one_tap` for a dashboard turn and `one_tap` for a cron fire
alike — **measured**. One rung, two consequences (asked-and-answered vs. asked-with-nobody-home),
and the seam is where that difference is known.

**The grant reuses `DCU-1`'s enable surface** rather than a config field: a fourth key,
`"unattended"`, listing the computer-use tools a run with nobody watching may invoke. Absent is
the fail-closed default. It is out-of-band for the reason the keystone is — a grant the agent's
process can PATCH is a grant the agent can give itself, and "may I drive the desktop while you
sleep" is the last question that should be answerable in-band. The governance ceiling could not
express it either (it may only NARROW a profile, so it cannot grant). `config/loader.py` is
untouched: **5647 lines before, 5647 after.**

Rather than a second copy of `apps`' validation, both lists now go through one
`_parse_name_list` — so all the malformed shapes `DCU-1` refuses (non-list, non-string, empty,
padded, duplicate) apply to both, and the `apps` detail strings stay byte-identical. `unattended`
adds one rule `apps` cannot have: entries are checked against the CLOSED tool surface, so a typo
refuses the whole document instead of failing closed invisibly.

**`ERR_COMPUTER_USE_UNATTENDED_NOT_GRANTED`** appended to `ERROR_CODES`, and deliberately kept
**OUT** of `service._CHILD_CODES` — the inverse of `DCU-3`/`DCU-6`, whose codes had to be added
because only the child can determine them. This one is decided in the parent at step 4b before
any child exists, so a child able to name it could dress a driver crash up as a policy verdict
the parent never reached. A real-spawn test proves the exclusion bites: a child naming it comes
back as `ERR_COMPUTER_USE_DRIVER_FAILED`.

**DISCOVERY — a measured fail-open at the HTTP seam, closed here.** `caller_identity=""` (any
authenticated client that simply did not send `X-Session-Key` — a script, an ACP CLI) resolved to
the **INTERACTIVE** profile, so the ladder read "a human is watching" for a caller with nobody
present. Fixed at the one seam that knows the header was absent (`handlers/computer_use.py`
`_caller_identity`), by minting a sessionless identity through the same `unattended_dispatch_key`
helper the trigger and hook seams use (PHF-8) — not by a special case inside `check_autonomy`,
which should read one contract.

**DISCOVERY — the ladder registry is lazy, and getting it wrong is invisible.** Without
`ensure_core_action_types()` at the seam, `resolve_rung` fails closed to `draft_only` for a
declared-but-unregistered key, and a computer-use dispatch never travels the provider-registration
path that registers them. The refusal would still refuse — so nothing looks broken — but it would
file a *proposal* ("here is what it would have done") instead of the *agent request* ("decide")
the clause asks for, and say the wrong sentence. `test_the_hold_row_is_a_request_from_a_cold_registry`
clears the registry and asserts the premise first, so the rail cannot go vacuous.

*Real on this host:* the whole seam. Every refusal and every permitted leg goes in at the real
`service.computer_dispatch` with the real keystone document, the real parser, the real screens,
a real `SecurityEventLog` read back off disk, a real `InboxStore` row read back off disk, and one
leg through the real ceilinged spawn with a real child. *Simulated:* only the platform driver
(step 6 is an in-process double on the in-process legs, because the AX API needs the TCC grant
`DCU-3` records as ungrantable by code). The `apps`-allowlist and keystone halves are untouched
and their suites still pass, which is the floor this sits on.

### `DCU-6` — Windows/Linux honest typed refusals

**Status:** todo

Windows (UIA) and Linux (AT-SPI) driver stubs (windows_driver.py, linux_driver.py, §3.6) that return a typed 'not yet on this platform' refusal for every tool — an honest refusal, never a silent no-op or a simulated success. Real Windows/Linux drivers are a deliberate later split, sequenced behind the platform-reach ordering when the operator has those platforms to validate on.

**Done when:** On non-macOS, every computer-use tool returns a typed refusal naming the platform; no silent no-op; validated.

**DONE (2026-08-26) — every clause met, with each non-macOS leg labelled simulated.**
`windows_driver.py` and `linux_driver.py` ship, so the `DRIVER_MODULES` entries that have named
them since `DCU-4` — while neither module existed, which made `resolve_driver` answer `None` —
now resolve. Both refuse with a newly registered code, `ERR_COMPUTER_USE_PLATFORM_UNSUPPORTED`,
distinct from `..._DRIVER_UNAVAILABLE` because the two are acted on differently: that one means
*this build has no driver for you and never claimed to* (still the honest answer for a platform
outside the map, so its branch is live, not dead), while this one means *your platform is named
and intended and the implementation is what is missing*. The FIX says so — it names macOS as the
one implemented driver, tells the operator plainly that no local setting turns this on, and keeps
the internal module path out of a user-facing sentence (the old fallback leaked
`personalclaw.computer_use.windows_driver` at an operator and then said "nothing to configure").
The wording lives ONCE in `unsupported_platform.py`, parameterised only by the platform name and
the accessibility API a real driver there will speak (UIA / AT-SPI); a rail asserts by AST that
neither platform module builds its own envelope, because two copies of a WHAT/WHY/FIX are the
family the structural-duplication ratchet counts.

*Real on this macOS host, no simulation:* resolution (all three platforms resolve; an unmapped
one still answers `None`), and that importing either pending driver touches no OS library — that
one is load-bearing rather than tidy, because `resolve_driver` runs **inside the gateway's
process**, so a module-level `import comtypes` would turn "no desktop capability" into "no
gateway". *Simulated, and only `platform.system()`:* the seven-operation refusal (in-process, real
`driver_host.run_op`) and the end-to-end clause — all seven tools through the real
`computer_dispatch`, the real keystone, the real screens, the real SEL row, the real ceilinged
spawn, a real child process running the real `driver_host.main`, and the real `_run_driver`
translation, with `platform.system` faked *inside the child* because a parent-side monkeypatch
cannot cross `exec`. Every leg has a Darwin twin through the same code path asserting the refusal
does **not** fire there.

### `DCU-7` — Human-facing live-view + cursor-motion overlay

**Status:** todo

Optional live-view (PiP) mirroring screenshots the model already read, and an optional cursor-motion overlay that draws a fake cursor (invisible to screen capture) so a watching human sees where a click will land, plus a dashboard view (§3.7). Both are observation-only and grant the agent nothing.

**Done when:** The views render; neither adds any agent capability — asserted by confirming the tool surface is unchanged with the views on; validated.

