# Integration Architecture & Shared Contracts — How the Rev-9 Plans Fit Together

**Created 2026-07-18 (roadmap rev 9).** This is the connective tissue an executor needs *before* touching any single plan: the map of how plans 31-47 interconnect, the shared seams each one builds on (defined **once** here, referenced by every consumer), and the mechanical conventions every plan must obey. It exists so a session — including a smaller model — never invents a signature, an error shape, a storage layout, or an event format that another plan also touches.

**Rule of use:** when a plan's `Contracts & Interfaces` section says "per INTEGRATION-ARCHITECTURE §X," that section is the authority. Do not re-derive it. If reality (the code) contradicts a contract written here, that's escalation trigger **E1** — stop and record it; do not silently pick a different shape.

**One-canonical-source discipline:** every shared type/API is defined in exactly one place (usually its owning plan's contracts section, indexed below). This document *indexes and cross-links* them and defines only the truly cross-cutting conventions. It never forks a definition.

---

## 1. The dependency & data-flow map

### 1.1 Build order (topological — a plan may only be executed after everything it points to)

```
31 LIFECYCLE-DOCTRINE ──────────────┬────────────────────────────────────────────┐
  (gates + migrations + tiers)      │ (every class-B/S change needs these)        │
                                    ▼                                             │
33 CI-RELEASE ──► 34 DISTRIBUTION   42 INBOX-NOTIF-UNIFICATION ◄── 40 CHANNEL (S1 trust)
  │  (pipeline)     │ (artifacts)     │ (kind registry, rules, inbox kinds)        │
  │                 │                 ├──► 46 LEARNING-VIS (proposals as inbox kind)│
  ▼                 ▼                 ├──► 44 MOBILE (push target)                  │
32 PROVIDER-BOUNDARY  36 DISCOVER     ├──► 45 DESKTOP (native-notif target)         │
  (manifest seams)    (org/site)      └──► 21 PROACTIVE (digest = its ambient slice)│
  │                                                                                │
  └──► 38 ECOSYSTEM (manifest self-desc, scaffold, registry)                       │
                                                                                   │
41 MCP-READONLY-INBOUND (fail-closed inbound substrate) ──► 24 EXTERNAL-ACCESS ────┘
35 SECURITY-LEGIBILITY (docs) ──► 47 SECURITY-HARDENING (keychain/signing/fuzz/SEL page)
37 OSS-OPERATIONS (model+hygiene) ──► 38 ECOSYSTEM (front door)
39 PLATFORM-REACH (arm/windows) ──► 45 DESKTOP (non-mac targets)   43 ONBOARDING (independent)
```

### 1.2 The shared seams and who touches them

| Seam (defined once in →) | Producers | Consumers |
|---|---|---|
| **Gate registry** (§4.1, plan 31) | 31 | 34, 42, 47, and every class-B change |
| **Migration framework** (§4.2, plan 31) | 31 | 42, 47, 14, 26, and every schema change |
| **Stability tiers** (§4.3, plan 31) | 31 | 25, 26, client-py, sdk changes |
| **SEL event logging** (§3.3, existing) | all security-relevant plans | SEL page (47) |
| **Inbound access substrate** (plan 41 §Contracts) | 41 | 24 (inherits, never rebuilds) |
| **Channel trust seam** (plan 40 §Contracts) | 40 | 24 §3, every channel app |
| **ChannelDelivery / ChannelTransport** (existing, §3.5) | 40 (Telegram/Discord/email), slack | 42 (channel_dm target), 44 |
| **Notification kind registry** (plan 42 §Contracts) | 42 | every `notify()` emitter (~10 sites), 46 |
| **Notification rules engine** (plan 42 §Contracts) | 42 | 44 (push), 45 (native), 40 (channel_dm), 46, 21 |
| **InboxItem typed kinds** (plan 42 §Contracts) | 42 | 46 (proposal), 40 (agent_request), 8 (flywheel queue) |
| **App manifest new fields** (plan 32 §Contracts) | 32 (cli.*, loggerRoots), 45 (desktop), 38/25 | app loader, scaffold (38) |
| **SDK export additions** (§3.4) | 40 (trust), 42 (kinds), others | app bundles |
| **Desktop capability bridge** (plan 45 §Contracts) | 45 | apps declaring `desktop:` perm |
| **SQLite compat helper** (plan 39 §Contracts) | 39 | 6 FTS5 modules |

### 1.3 The three "landmine" convergence points (where two plans touch one surface — sequence carefully)

1. **The attention/notification path** — plans 42 (rules), 40 (channel_dm delivery), 44 (push), 45 (native), 46 (proposal items), 8 (flywheel queue), 7 (autonudge absorption), 21 (digest). **42 lands first and owns the contracts;** all others consume its registry/rules/item types. Building any of them before 42 = two migrations over one surface (forbidden).
2. **Credential storage** — plan 47 (keychain) and plan 13 (secret vault) must share ONE backend behind `save_credential`. Whichever lands first defines the backend selector; the second extends it.
3. **The inbound HTTP surface** — plan 41 builds the minimal fail-closed substrate; plan 24 generalizes it. 24 never re-designs §1; it widens 41's `inbound/` package.

---

## 2. Conventions every plan obeys (the mechanical rules)

### 2.1 Config fields — the 5-point wiring contract (existing, enforced by `tests/test_config_roundtrip.py`)

A new config field is only correct when wired through ALL of:
1. the dataclass in `config/loader.py` with `field(default=…, metadata=_meta(label, help, **kwargs))` — `_meta(label: str, help: str, **kwargs) -> dict` (verified `loader.py:280`);
2. `load()`'s explicit field-by-field mapping;
3. `to_dict()`;
4. a write path — either the `_EDITABLE_CONFIG` PATCH allowlist (`dashboard/handlers/core.py:430`, entries shaped `{"path.key": {"type": "bool|int|float|str|enum|str_list|…", …bounds}}`) or a dedicated PUT;
5. (if user-facing) a frontend control.

Entity/user state that is NOT global config goes in `~/.personalclaw/entity_settings/<name>.json` (see §2.4), never in `config.json`. **Rule of thumb:** operator knobs → config.json; per-entity user preferences → entity_settings; secrets → credential store (§2.5).

### 2.2 Error envelope (HTTP)

New API routes return errors as `web.json_response({"error": {"code": "<stable_snake_code>", "message": "<human>"}}, status=<4xx/5xx>)`. `code` is a stable, append-only string an agent can branch on (never reworded once shipped — it's a Tier-S surface per plan 31). Success envelopes match the neighboring handler's existing shape (imitate the nearest module — do not standardize retroactively).

### 2.3 SEL event contract (existing — §3.3)

Every security-relevant action logs via `sel().log_tool_invocation(...)` or `sel().log_api_access(...)` or a raw `sel().log(SecurityEvent(...))`. New event_types are lowercase snake (`gate_set`, `sender_paired`, `inbound_rate_limited`, `app_cli_setup`, `capability_grant`). Never invent a second audit log — the SEL is the one.

### 2.4 Storage file conventions

- Location: everything under `config_dir()` (= `~/.personalclaw`, relocatable via `PERSONALCLAW_HOME`). Never hardcode the home path — call `config_dir()`.
- Writes: `atomic_write(path, content, *, fsync=False, mode=None)` (verified `atomic_write.py:29`) for text; `atomic_write_bytes` for binary. Never a bare `open(...,"w")` for state.
- Reads: tolerate missing + corrupt → return the safe default + a warning log (the fail-open-for-availability / fail-closed-for-inbound split is per-plan; §2.6).
- Permissions: secrets/HMAC keys `mode=0o600`.
- Append-only JSONL logs (audit, digest queue): trim at 2× cap (mirror `notifications.jsonl` / SEL trim).
- New durable state is a plan-31 change-class **B** and needs a migration if it changes an existing file's shape.

### 2.5 Credential store (existing)

`save_credential(key: str, value: str)` (verified `config/loader.py:234`) writes `.env` (0600, mirrored to `os.environ`). Read via the credential accessors re-exported through `sdk/credentials`. Keys are UPPER_SNAKE. Apps get credentials only through the SDK; core owns the `.env`. Plan 47 adds a keychain backend *behind this same API* — callers never change.

### 2.6 App-persisted settings (existing — for app bundles)

`ProviderSettings` (static API, verified `providers/settings.py:25`): `.load(app_name) -> dict`, `.save(app_name, dict)`, `.update(app_name, partial) -> dict`, `.validate(config, schema) -> list[str]`, `.config_path(app_name) -> Path` (lives in the app's `data/`, survives updates). Channel/model/tool apps store their non-secret config here; secrets go to the credential store (§2.5).

### 2.7 The fail-open vs fail-closed rule (stated once, referenced everywhere)

- **User-facing availability surfaces fail OPEN:** a corrupt notification-rules or settings file must never silence the system → fall back to permissive defaults + warn (the existing `notification_allowed` philosophy).
- **Inbound / security surfaces fail CLOSED:** a missing/corrupt inbound `enabled` flag, an unverified token, an unknown migration state → refuse/disable + explicit log. Stated in-code at each such site so nobody "fixes" the asymmetry.

### 2.8 SDK export rule

Apps import core ONLY via `personalclaw.sdk.*` (enforced by `tests/test_apps_import_boundary.py`). Any new app-facing primitive (channel trust, notification kinds an app may emit) is exported by adding it to the relevant `sdk/<area>.py` re-export block — never by an app reaching into core internals. Adding an sdk export is a plan-31 Tier-**S** change (semver-stable surface).

---

## 3. The existing primitives (verified signatures — build on these, don't reinvent)

### 3.1 `atomic_write(path, content, *, fsync=False, mode=None) -> None` — `atomic_write.py:29`; `atomic_write_bytes(...)` :47.
### 3.2 `save_credential(key, value) -> None` — `config/loader.py:234`; `config_dir() -> Path` :161; `_meta(label, help, **kwargs) -> dict` :280.
### 3.3 SEL — `sel() -> SecurityEventLog` (`sel.py:440`, singleton). `SecurityEvent` fields (`sel.py:62`): `event_id, timestamp(ISO-UTC), event_type, caller_identity, agent, source, operation, tool_kind, outcome, resources, downstream_service, request_id, error, prev_hash, entry_hash, metadata`. Convenience: `log_tool_invocation(*, session_key, agent="personalclaw", source="", tool_name, tool_kind="", outcome, request_id="", downstream_service="", resources="", error="", metadata=None)`; `log_api_access(*, caller, operation, outcome, source="dashboard", resources="", error="")`; `verify_integrity(max_entries=_VERIFY_WINDOW) -> tuple[int,int]` :280.
### 3.4 `DashboardState.notify(kind: str, title: str, body: str, *, meta: dict|None=None) -> None` — `dashboard/state.py:1027`. Gated by `notification_allowed(kind)` (`providers/entity_routes.py`); persists to the notification log + broadcasts. Plan 42 wraps the gate with the rules engine BEHIND a lifecycle gate (byte-identical when gate off). `unread_count()` derives from unacked entries (plan 42 moves this to inbox).
### 3.5 Channel seams — `ChannelTransportProvider` ABC (`channel_transports/base.py:70`: `name, display_name, connect, disconnect, send(OutboundMessage), receive()->AsyncIterator[ChannelMessage], start_inbound(services), stop_inbound, health, test, capabilities()->ChannelCapabilities, info`); `ChannelDelivery` protocol (`channel_delivery.py`: 18 methods incl. `deliver_text/rich/notification`, `request_approval`, `build_thread_link`, streaming trio). Dataclasses `OutboundMessage`/`ChannelMessage`/`ChannelCapabilities` in base.py.
### 3.6 Inbox — `InboxItem` (`inbox.py:60`, id=`{channel}_{ts}`, `.ts` property rsplits id — **any new kind keeps `*_{ts}` id shape**); `ItemStatus{PENDING,SENT,DISMISSED,HANDLED}`; tolerant `from_dict`. Skills proposals — `skills/proposals.py`: `enqueue(*, slug, description, triggers, procedure_md, session_key, created_at, kind="new", refine_target="", source_excerpt="") -> SkillProposal|None`, `list_pending()`, `get(pid)`, `reject(pid)`, `accept(pid, *, description=None, procedure_md=None) -> str`.
### 3.7 Security — `fence_untrusted(text, *, source="") -> str` (`security.py:672`); `redact(text) -> str` :658; command screening via `denied_command_patterns()`.
### 3.8 App manifest — `apps/manifest.py` dataclasses (CronEntry, UIPage, UISidebar, UIConfig, BackendConfig, Permissions{api,events,mcpTools,memory,cron,storage,agent,network}, `setup.onInstall`, `configSchema`); unknown fields preserved. Plans 32/45/38 add fields — all via the same to_dict/from_dict-parity pattern.
### 3.9 Read paths (plan 41 tools) — `MemoryService.recall_with_provenance(*, query_text, limit=8) -> list[dict]` (`memory_service.py:835`); `knowledge/retrieval.py::search(query, limit=10, *, include_archived=False) -> list[dict]`; `tasks/registry.py::list_all_tasks(...)`, `get_task(task_id, provider_name=None)`, `search_tasks(...)`.
### 3.10 CLI — argparse subparsers off `sub = parser.add_subparsers(dest="command")` (`cli.py:205`); two-level pattern (see `cron`/`spawn`/`security`). New commands (`gates`, `migrate`, `inbound`, `pair`, `app new`, `push`) follow it.

---

## 4. The three foundational contracts (owned by plan 31 — reproduced here as the index; plan 31 is authority)

### 4.1 Gate registry
`Gate(id: str, owner_plan: str, created: str, change_class: Literal["R","B","S"], summary: str, removal_condition: str, target_removal: str, default: bool)`; `register(gate) -> None`; `all_gates() -> list[Gate]`; `gate_enabled(id: str) -> bool` (missing state→default; unregistered id→`KeyError`). State: `~/.personalclaw/gates.json` `{id: {enabled: bool, flipped_at: str}}`. CLI: `personalclaw gates list|set <id> on|off`. **Consumers call `gate_enabled("<id>")` at the call site, never cache it.**

### 4.2 Migration framework
`Migration(id, plan, description, applies_to: list[str], required: bool, run(ctx)->None, verify(ctx)->bool)` in `lifecycle/migrations/m_YYYYMMDD_<slug>.py`. Ledger `~/.personalclaw/migrations.json`. Runner: `personalclaw migrate [--dry-run(default)|--apply|--rollback <batch>|--list]`; snapshots via `portability.create_export_zip()` before `--apply`. Boot: pending `required` → refuse start with remedy; pending optional → doctor + notify.

### 4.3 Stability tiers
Tier **S** (semver, deprecation window 2 minors/90 days): `personalclaw.sdk.*`, app manifest schema, inbound dialect wire contracts, on-disk formats, `personalclaw-client`. Tier **I** (no contract): dashboard `/api/*`, WS events, core internals. Generated inventory `docs/reference/stability-inventory.md` + drift test.

---

## 5. Per-plan contract index

Each deepened plan carries a `Contracts & Interfaces` section defining its own types/signatures/schemas and an `Integration points` list (calls / called-by / events / storage). This index says where each lives:

| Plan | Owns these contracts |
|---|---|
| 31 LIFECYCLE | §4 above (gates, migrations, tiers) |
| 32 PROVIDER-BOUNDARY | manifest `cli.setup`/`cli.doctor`/`loggerRoots`; `SetupContext`, `DoctorLine`; `installed_logger_roots()` |
| 33 CI-RELEASE | workflow file contracts (job names, artifacts, environments) |
| 34 DISTRIBUTION | `detect_install_kind()`, update-check payload, `verify_wheel` contract |
| 40 CHANNEL | `channel_trust.py` full API; per-transport delivery obligations |
| 41 MCP-INBOUND | `inbound/` package API (auth/caps/audit/mcp_http/tools), the 5-tool JSON-RPC contract |
| 42 INBOX-NOTIF | kind registry, rules schema, extended InboxItem, `emit_attention_item()` |
| 43 ONBOARDING | onboarding-state schema, `EmptyState` props, approval-brief data model |
| 44 MOBILE | device-token contract, push payload schema, companion route API map |
| 45 DESKTOP | capability bridge API, `desktop:` manifest perm, gateway desktop route |
| 46 LEARNING-VIS | skill-draft shape, attribution event fields, refine-diff proposal shape |
| 39 PLATFORM-REACH | `sqlite_features()` contract |
| 38 ECOSYSTEM | scaffold type-table, `registry.json` schema |
| 47 SECURITY-HARD | keychain backend selector, signature payload, corpus layout |
| 35, 36, 37 | doc-artifact structures (no code contracts — file/section specs inline) |

Plans 1-30 add their contract sections when they reach execution (their designs predate this doc); [REV9-ALIGNMENT-AND-OWNER-TASKS.md](REV9-ALIGNMENT-AND-OWNER-TASKS.md) flags which are class-B/S and thus need one.
