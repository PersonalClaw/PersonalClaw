// PersonalClaw API client (web). Matches the real backend contract:
// root-relative /api paths, X-Session-Key header on every call, same-origin
// (cookie pc_token_<port> rides along via the dev proxy). See the composer
// API contract in docs.

import { errText } from './errText'

const SK = { 'X-Session-Key': 'dashboard:ui' }

/** An Error that carries the HTTP status, so callers can distinguish a genuine 404
 *  (resource gone) from a transient network/5xx blip. `.message` is unchanged (the
 *  backend's error text), so existing `catch(e => e.message)` callers are unaffected;
 *  only callers that branch on status read `.status`. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new ApiError(await errText(r), r.status)
  return r.json() as Promise<T>
}

const get = <T>(p: string) => fetch(p, { headers: { ...SK } }).then(j<T>)
const post = <T>(p: string, body?: unknown) =>
  fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json', ...SK }, body: body == null ? undefined : JSON.stringify(body) }).then(j<T>)
const put = <T>(p: string, body?: unknown) =>
  fetch(p, { method: 'PUT', headers: { 'Content-Type': 'application/json', ...SK }, body: body == null ? undefined : JSON.stringify(body) }).then(j<T>)
const patch = <T>(p: string, body?: unknown) =>
  fetch(p, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...SK }, body: body == null ? undefined : JSON.stringify(body) }).then(j<T>)
const del = (p: string) => fetch(p, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw new ApiError(await errText(r), r.status) })

/** App install/update: POST that returns the parsed body on ANY HTTP status.
 *  The scanner verdict + needs_consent are carried in the 400/409 body, so a
 *  thrown error would discard exactly what the install modal needs to show. Only
 *  a true network/parse failure rejects (as an ok:false result). */
async function _installReq(p: string, body: unknown): Promise<AppInstallResult> {
  try {
    const r = await fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify(body) })
    const data = await r.json().catch(() => null)
    if (data && typeof data === 'object') return data as AppInstallResult
    return { ok: false, name: '', error: `HTTP ${r.status}`, needs_consent: false, scan: null }
  } catch (e) {
    return { ok: false, name: '', error: String((e as Error)?.message || e), needs_consent: false, scan: null }
  }
}

/** The knowledge store serializes `tags` as a JSON string; normalize to an array
 *  so the UI can map over it. Defensive against already-array or absent values. */
// ── types ──
/** A saved custom theme = a named color identity, persisted server-side under
 *  config_dir()/themes and shareable across browsers/surfaces. `dark`/`light`
 *  map CSS color-token varNames (design/tokenRegistry ColorTokens) → hex. */
export interface ThemeSummary { slug: string; name: string; emoji: string; created_at: string }
export interface ThemeRecord extends ThemeSummary {
  dark: Record<string, string>
  light: Record<string, string>
}
export interface ThemeWrite {
  name: string; emoji?: string
  dark: Record<string, string>
  light: Record<string, string>
}
// Live channel runtime: connection state + health (distinct from the Providers
// enable/config surface — this is whether the transport is actually connected now).
export interface ProviderHealth {
  name: string
  breaker_state: 'closed' | 'open' | 'half_open'
  consecutive_failures: number
  calls: number
  passed: number
  failed: number
  pass_rate: number | null
  p50_ms: number
  p90_ms: number
  p99_ms: number
  failure_modes: Record<string, number>
  degraded: boolean
}

// The earned-autonomy ladder (AUTONOMY-GUARDRAILS §5-§6). One row per DECLARED action
// type: the rung it resolves at, where that rung came from (`authority`), the recomputed
// track record, and whether the next rung has been earned. Nothing here is editable in
// place — a rung changes through `autonomyGrant` (a click) or `autonomyDemote`.
export interface AutonomyType {
  key: string
  floor: string
  ceiling: string
  leaves_machine: boolean
  providers: string[]
  resolved_rung: string
  granted_rung: string
  /** Granted higher than it currently resolves, because the incident kill switch is on. */
  held_by_incident: boolean
  /** WHY this type runs at this rung, in one server-composed sentence. */
  authority: string
  granted_at: string
  evidence_window: string
  demotions: Array<{ at: string; cause: string; cooldown_until: string }>
  eligible: boolean
  next_rung: string
  /** The derived track record, or what is missing from it. Always populated. */
  record: string
  clean_approvals: number
  rejections: number
  observed_days: number
  cooldown_until: string
}
/** One executed `auto_with_undo` action. `reversed_at` empty = the undo is still offered. */
export interface AutonomyReversal {
  id: string
  action_type: string
  rung: string
  label: string
  created_at: string
  reversed_at: string
}
export interface AutonomyLadder {
  rungs: string[]
  /** Server-owned wording per rung, so a chip and a proposal cannot disagree. */
  rung_meta: Array<{ key: string; label: string; hint: string }>
  incident_active: boolean
  types: AutonomyType[]
  reversals: AutonomyReversal[]
}

// Doctor — the tiered read-only health report (PLATFORM-RESILIENCE §1).
export interface DoctorProbe {
  id: string
  capability: string
  tier: number
  title: string
  ok: boolean
  detail: string
  evidence: Record<string, unknown>
  fix_id?: string
}
export interface DoctorCapability {
  ok: boolean
  tier: number
  probes: DoctorProbe[]
}
export interface DoctorReport {
  ok: boolean
  core_ok: boolean
  worst: string
  restart_suggested: boolean
  capabilities: Record<string, DoctorCapability>
  skipped_capabilities: string[]
  generated_at: number
}

// No-model degraded mode (PLATFORM-RESILIENCE §5).
export interface DegradedSurface {
  surface: string
  available: boolean
  floor: string
  backlog: number
  use_cases: string[]
}
/** One scheduled backup job's last run + whether it's due (DURABILITY-AND-SYNC §3). */
export interface DurabilityJob {
  last_run: number   // epoch seconds; 0 = never run
  due_in_secs: number
  due: boolean
}
export interface DurabilityStatus {
  enabled: boolean
  export: DurabilityJob
  snapshot: DurabilityJob
  drill: DurabilityJob
}
export interface DurabilitySnapshot {
  name: string
  taken_at: string
  size: number
  /** False = the CURRENT retention tiers would prune this one on the next pass. */
  retained: boolean
}
export interface DurabilitySnapshots {
  directory: string
  snapshots: DurabilitySnapshot[]
  would_prune: string[]
  tiers: { daily: number; weekly: number; monthly: number }
}
export interface DurabilityJobResult {
  job: string
  ok: boolean
  /** The REASON this job did no work, or `''` if it ran. Non-empty is not a failure —
   *  usually a concurrent run held the single-flight lock, or there was nothing to do
   *  (e.g. no snapshot to drill yet). It is a string, not a flag, so the reason can be
   *  shown instead of a bare "skipped". */
  skipped: string
  detail: string
  duration_secs: number
  extra?: Record<string, unknown>
}
export interface DegradedReport {
  surfaces: DegradedSurface[]
  degraded: string[]
}

// Confirm-gated fixes + surfacing simulator (PLATFORM-RESILIENCE §2/§3.1).
export interface DoctorFix {
  id: string
  title: string
  impact: string
  preview: string
}
export interface SurfacingCandidate {
  key: string
  kw_score: number
  sem_score: number
  threshold_kw: number
  threshold_sem: number
  negated: boolean
  included: boolean
  reason: string
}

// Health-scored remediation engine (PLATFORM-RESILIENCE §4).
export interface RemediationJobRow {
  id: string
  status: string
  cost: number
  detail?: string
  error?: string
}
export interface RemediationRun {
  ts: number
  score_before: number
  score_after: number
  jobs: RemediationJobRow[]
  stopped_reason: string
}
export interface RemediationSnapshot {
  score: number
  target_score: number
  deficits: { key: string; count: number; penalty: number; reachable: boolean }[]
  plan: RemediationJobRow[]
  recent_runs: RemediationRun[]
}

export interface ChannelHealth { state: string; detail?: string }
export interface ChannelRuntime {
  name: string; display_name: string; connected: boolean
  capabilities?: Record<string, unknown>
  health: ChannelHealth
}
// A background subagent (from /api/spawn) — spawned by a cron/loop/Slack/agent.
export interface SpawnedAgent { id: string; task: string; done: boolean; parent?: string; agent?: string; started?: number; result?: string; error?: string }
// A knowledge item scored for chat-context injection (from search-for-context),
// carrying its token cost so the picker can budget. P12 adds the per-item citation
// locator (source_type/section/line_range/deep_link) so a card can deep-link + cite
// where in the source the match sits; all optional (null for a structureless type).
export interface KnowledgeContextCard {
  id: string; title: string; provider?: string; match_type?: string; tokens: number; summary?: string
  source_type?: string | null; section?: string | null; line_range?: [number, number] | null; deep_link?: string | null
}
export interface KnowledgeContextResult { query: string; results: KnowledgeContextCard[]; total_tokens: number; max_tokens: number }

export interface LexiconTerm { id: string; canonical: string; aliases: string[]; entity_type: string; weight: number; source: 'graph' | 'manual' | 'learned' | string; enabled: boolean }
export interface LexiconCorrection { id: string; heard: string; meant: string; count: number; auto_apply: boolean; last_seen: string }
// An MCP server available to an agent (from /api/mcp/active).
export interface McpActiveServer { name: string; enabled: boolean }
// A lifecycle hook in effect (redacted view from /api/agent-hooks).
export interface AgentHook { command: string; matcher?: string; source?: string }
export interface AgentProvider {
  name: string; provider_id: string; type: string; ready: boolean; state: string; detail: string
}
export interface DiscoveredAgent {
  id: string; name: string; runtime: string; description: string; provider_agent: string; reasoning_effort: string; models: string[]
  // Backend-declared reasoning-effort options ({value,label}), verbatim. Empty =
  // runtime has no effort axis → composer hides the reasoning control.
  supported_efforts?: { value: string; label: string }[]
}
export interface ModelItem { name: string; model_name: string; description: string; provider: string }

// App Platform (A7)
// An app's declared permission scope, exactly as `Permissions.to_dict()` emits it
// (apps/manifest.py) — every key that dict can carry must be declared here, or the
// consent UI cannot disclose it (that was the APE-12 defect: `appMessaging` reached
// the browser and was dropped on the floor). All of these are enforced server-side
// EXCEPT `network`, which is declaration-only — the gateway has no per-app egress
// chokepoint (provider code is imported in-process). The consent UI must therefore
// render `network` as advisory and outside the enforced list; see `PermissionList` in
// pages/apps and docs/security/limitations.md §2.
export interface AppPermissionsWire {
  api?: string[]; events?: string[]; mcpTools?: string[]
  storage?: boolean; network?: boolean; memory?: string; cron?: boolean; agent?: boolean
  // APE-9/APE-12: apps this app may send a brokered message to (exact name, or a
  // trailing-`*` prefix pattern). Enforced — `POST /api/apps/message` is the only
  // app-to-app path and refuses an undeclared target 403 + SEL. Absent = may message
  // no app at all (deny by default), which the consent UI states rather than implies.
  appMessaging?: string[]
  // DC-2: native desktop capabilities this app may reach THROUGH the gateway (apps
  // never touch Electron IPC). Enforced — `/api/desktop/*` refuses an undeclared
  // capability 403 + SEL `desktop.capability_denied`. Exact names only, no wildcard.
  // Absent = no native reach at all, which the consent UI states rather than implies.
  desktop?: string[]
}
// DC-2. One native capability's state as the desktop shell reported it. `granted`
// mirrors macOS's own vocabulary so nothing is translated on the way through.
// `requestable` is false when THIS PROCESS cannot raise the OS prompt (macOS exposes
// no API to ask for Screen Recording, and none to read notification authorization) —
// the UI must then point at System Settings instead of offering a dead button.
export interface DesktopCapabilityWire {
  available: boolean
  granted: 'granted' | 'denied' | 'restricted' | 'not-determined' | 'unavailable'
  requestable: boolean
  reason: string
}
export interface DesktopStateWire {
  connected: boolean
  shell: { version: string; platform: string } | null
  // Empty whenever `connected` is false. Absence is the honest answer: a browser tab
  // must not read as "these exist, just not granted yet".
  capabilities: Record<string, DesktopCapabilityWire>
  registered_at: string
  last_seen: string
}
export interface AppUiPage { route: string; label: string; icon: string }
export interface AppSummary {
  name: string; displayName: string; version: string; description: string
  enabled: boolean; origin: string; source?: string; icon: string
  heroUrl?: string  // resolved data: URI for the optional hero/banner image; absent/"" if none
  hasBackend: boolean; hasUI: boolean
  uiPages: AppUiPage[]
  isProvider: boolean; providerType: string; hasConfig: boolean
  permissions: AppPermissionsWire
  tags: string[]
  installedAt?: string; updatedAt?: string
  backendRunning: boolean; backendPort: number | null
  // App category is the SINGLE `native` flag: true = a native app (always-on,
  // locked, can't be uninstalled — filesystem/tool providers + seeded natives);
  // false = a first-party or third-party app the user installs/uninstalls. Whether
  // a native app has a settings surface is `hasConfig` (not a separate flag).
  native?: boolean
  // APE-7: this app's source offers a newer version than the installed copy. Computed
  // on the /api/apps read path (no polling); `latestVersion` is that newer version.
  updateAvailable?: boolean
  latestVersion?: string
}
export interface AppDetail {
  name: string
  installed: Record<string, unknown>
  manifest: Record<string, unknown> | null
  config: Record<string, unknown>
  configSchema: Record<string, unknown>
  backendRunning: boolean; backendPort: number | null
}
// P29: a manifest cron's install-consent summary — name + cadence + WHAT it runs
// (an agent + its prompt; a manifest cron has no action/command). Cadence is either
// `every` seconds or a `cron_expr`.
export interface AppCronSummary {
  name: string; every?: number; cron_expr?: string; agent?: string; message?: string
}
export interface AppCatalogEntry {
  name: string; displayName: string; description: string; version: string
  icon: string; heroUrl?: string; author: string
  source: string; sourceKind: 'bundled' | 'native' | 'first-party' | 'local' | 'git'
  isProvider: boolean; providerType: string; tags: string[]
  // P20: when this entry came from a source's registry index, the install pointer
  // (repo[#subdirectory]) to hand install — routes through the scanner unchanged. "" for
  // a dir-scanned/bundled entry (its `source` is the pointer).
  pointer?: string
  // P29 install-consent: what the app will be GRANTED (permissions) + the recurring
  // jobs it will RUN (crons), surfaced pre-install so the Store card can show them
  // before the user commits. Empty dict/[] for an app that declares neither, or for a
  // registry-index pointer (its manifest isn't fetched until install).
  permissions?: AppPermissionsWire
  crons?: AppCronSummary[]
}
export interface AppScanFinding { surface: string; severity: string; rule: string; path: string; evidence: string }
export interface AppScanReport { verdict: string; findings: AppScanFinding[]; tier?: string }
export interface AppInstallResult {
  ok: boolean; name: string; error: string; needs_consent: boolean
  scan: AppScanReport | null
  // P21 platform gate: set when the app installs on the user's LOCAL machine
  // (installMode=client) or doesn't support this server's OS — the server can't
  // install it, so it hands back a copy-paste one-liner to run in a terminal.
  needs_client_install?: boolean
  client_install?: { shell?: string; postInstall?: string } | null
  // The install pulled a new python dependency (or registered pieces that only
  // load at boot) — the gateway must restart before the app fully takes effect.
  restart_required?: boolean
  // APE-8 "Fix with AI": on a failed install with captured subprocess output,
  // `fix_prompt` is a ready-to-send chat seed that embeds `log_excerpt` wrapped in
  // the backend's untrusted-content fence. The FE hands it straight to launchChat;
  // it is empty on success or when there was no log to show.
  log_excerpt?: string
  fix_prompt?: string
}
export interface SkillInstallResult {
  ok?: boolean; path?: string; error?: string
  httpStatus: number            // 201 ok · 409 overridable warning · 403 dangerous
  verdict?: string              // clean | low | warning | dangerous
  tier?: string
  overridable?: boolean         // true → re-install with force=true is allowed
  scan?: AppScanReport | null   // findings, reused shape from the app scanner
}
export interface AppDepClassification {
  key: string; kind: string; id: string; disposition: string; remaining: string[]
}
export interface AgentDef { name: string }
export interface ChatSession {
  key: string; title: string; agent: string; model: string; reasoning_effort: string
  acp_provider: string; acp_provider_agent: string; mode: string; workspace_dir: string
  messages: number; running: boolean; stopping: boolean; pending_approval: boolean
  memory_mode?: string; last_message?: string; last_ts?: number
}
export interface ChatSessionSummary {
  key: string; title: string; agent?: string; model?: string; messages: number
  running?: boolean; created?: string; last_activity_ts?: string; last_ts?: string; pinned?: boolean
  folder_id?: string; tags?: string[]; color_index?: number | null
  last_message?: string; prompt_preview?: string
  // Session origin: 'manual' (user-initiated) vs a worker started by a goal loop /
  // code project / campaign. Worker sessions carry the originating entity's id +
  // friendly label so the history list can tag + link them and default-hide them.
  origin?: 'manual' | 'loop' | 'code' | 'campaign' | 'channel'
  source_id?: string; source_label?: string
  // Session lifecycle (SESSION-MANAGEMENT S2). 'archived' leaves the active list but
  // stays fully searchable and restorable — archiving is never deletion.
  lifecycle?: 'active' | 'archived'
  last_activity_at?: number
  never_archive?: boolean
}
/** One recorded disagreement between two stored claims (KNOWLEDGE-SYNTHESIS §3.2).
 *
 *  `basis` matters to a reader: `deterministic` means two claims provably cannot both hold,
 *  `model` means a fast model thought so. Rendering them identically would give an opinion the
 *  weight of a proof. `prefer` is the source-precedence ladder's advice — "" when it cannot
 *  decide, which is the honest answer for two same-tier sources. */
export type KnowledgeConflict = {
  item_id: string
  item_title: string
  left_claim: string
  right_claim: string
  left_item: string
  right_item: string
  kind: 'value' | 'polarity' | 'number'
  basis: 'deterministic' | 'model'
  prefer: 'left' | 'right' | ''
  detail: string
  confidence: number
}

/** One typed edge between knowledge ITEMS (`item_relations`), distinct from the
 *  entity-level `KnowledgeRelation` above. `provenance` separates a deterministic
 *  extraction (confidence 1.0) from a model's inference. */
export type KnowledgeItemRelation = {
  item_id: string
  title: string
  relation: 'supersedes' | 'contradicts' | 'derived_from' | 'depends_on' | 'part_of'
  confidence: number
  provenance: 'extracted' | 'inferred'
}

/** A knowledge shelf. `manual` holds an explicit membership list; `smart` stores a
 *  query re-run on read, so it stays current with no backfill. `item_count` is null
 *  for a smart shelf — counting it would mean a search per shelf on every rail render. */
/** One tag in the taxonomy: id, parent, and a LIVE usage count (computed from the
 *  join, scoped to the active non-archived library — so it agrees with the flat
 *  `knowledgeTags()` autocomplete list and with the corpus overview). */
export interface KnowledgeTag {
  id: number
  name: string
  parent_id: number | null
  parent_name: string | null
  usage_count: number
}
/** Curation ops the bulk endpoint accepts. `delete` is deliberately not one of them:
 *  every op here is reversible, and an irreversible action beside them would be one
 *  mis-click from data loss (the same exclusion the chat bulk endpoint makes). */
export type KnowledgeBulkOp =
  | 'collect' | 'uncollect' | 'read_state' | 'favorite' | 'archive' | 'restore' | 'pin'
export interface KnowledgeBulkResult {
  ok: boolean
  op: KnowledgeBulkOp
  changed: string[]
  /** Already in that state — distinct from a failure, so the UI can say
   *  "8 were already read". */
  unchanged: string[]
  missing: string[]
}
export interface KnowledgeCollection {
  id: string; name: string; kind: 'manual' | 'smart'; query?: string; icon?: string
  position?: number; item_count?: number | null; created_at?: string; updated_at?: string
}
export interface ChatFolder { id: string; name: string; order?: number; collapsed?: boolean; parent_id?: string }
export interface ChatTag { id: string; name: string; color?: string; order?: number; status?: boolean }
// A PROPOSED organization for an untagged chat (SM T2.1). `tags` are NAMES, not ids — a
// proposed tag may not exist yet and is created (via the shared tag helper) only on accept.
// Holding one of these changes nothing about the session; only organizeAccept applies it.
export interface OrganizeProposal {
  session: string; folder_id: string; folder_name: string; tags: string[]
  source: 'title' | 'workspace' | 'channel' | 'llm' | string; reason: string; dedup_key?: string
}
// Magic re-tag batch job (POST/GET /api/sessions/retag-all). status 'idle' only
// appears on the GET before any job has run.
export interface RetagJob { id?: string; status: 'idle' | 'running' | 'done' | 'error' | 'cancelled'; done?: number; total?: number; updated?: number; skipped?: number; errors?: number; current?: string; error?: string }
export interface TagColumn { id: string; name?: string; tag_ids?: string[]; mode?: 'any' | 'all' | 'none'; order?: number; include_untagged?: boolean }
export interface ChatHistoryMsg {
  role: string; content: string; ts?: string; cls?: string
  // tool/permission messages carry meta {tool_call_id, input, purpose, output?, done?};
  // an assistant message that used episodic recall carries memory_citations (§5.4).
  meta?: { tool_call_id?: string; input?: string; purpose?: string; output?: string; done?: boolean; tool?: string; memory_citations?: { n: number; id: string | null; preview?: string }[] }
}

// ── workspace / build entity types ──
export interface NotificationItem {
  kind: string; title: string; body: string; ts: string
  job_id?: string; loop_id?: string; loop_kind?: string; acked: boolean
  /** AUTONOMY-GUARDRAILS §6.1 — set on the passive notice an `auto_with_undo` action leaves.
   *  `reversal_id` is the RECORD id the undo endpoint takes; `reversal` is the provider's own
   *  opaque handle, carried for the audit trail only and never sent back by the UI. */
  reversal_id?: string; reversal?: string; action_type?: string; rung?: string
}
// Schedule job — the schedule-kind projection of a Trigger (from /api/triggers).
// Three orthogonal axes: schedule KIND (every/cron/at), the action (provider +
// config), and delivery/context (channel, silent, timezone, skip_dates, strict).
export type ScheduleKind = 'every' | 'cron' | 'at'
export type ScheduleExecMode = 'agent' | 'script' | 'command'
export interface ScheduleJob {
  id: string; name: string; message: string; enabled: boolean
  schedule: string                          // human-rendered cadence string
  cron_expr?: string | null                 // when kind=cron
  every_secs?: number | null                // when kind=every
  created_ts?: number | null
  last_status?: string | null              // "ok" | "error" (the action-dispatch result)
  last_run_status?: string | null          // newest run record status: success|failure|timeout|launched (T7, persistent)
  agent?: string | null; model?: string | null
  channel?: string | null; approval_mode?: string | null
  silent?: boolean; strict_schedule?: boolean; timezone?: string | null
  skip_dates?: string[]
  script?: string | null; command?: string | null  // zero-token exec modes
  action?: { provider?: string; config?: Record<string, unknown> }  // canonical {provider, config}
  last_run_ts?: number | null; next_run_ts?: number | null
  has_result?: boolean; last_result?: string | null; last_error?: string | null
  is_running?: boolean; running_since?: number | null; has_session?: boolean
}
// One run record from /history (no trace) or /history/{run_id} (with trace).
export interface ScheduleRun {
  // 🔴 `id` is a FireRecord's OWN key and `did_ids`/`suppressed_ids` are lists of it (S165). A
  // projected row carries NO `run_id`/`job_id` — measured, `run_id` comes back `''` — so a
  // consumer matching the split on `run_id` silently matches nothing.
  id?: string
  run_id?: string; job_id?: string; job_name?: string
  trigger?: string                          // "manual" | "scheduled"
  // ISO-8601 on `/api/triggers/history`, epoch seconds on the schedule endpoints — the
  // union is the honest declaration, and every reader goes through `epochSeconds`.
  started_at?: number | string; finished_at?: number | string; duration_ms?: number
  status?: string                           // "success" | "error"
  summary?: string; error?: string; trace?: string
  // 🔴 The TYPED fire outcome (S163). `/api/triggers/history` returns FireRecord rows, whose
  // vocabulary is `ran | skipped_gate | blocked_injection | deferred | …` on `outcome` — NOT
  // `status`, which a FireRecord does not carry at all. Absent from this type, every projected
  // row arrived with `status: undefined` and the Schedule widget's local mapper fell through to
  // its default branch, rendering a quiet-hours SUPPRESSION as "ran".
  outcome?: string
  reason?: string                           // the mandatory one-line why, for any non-clean row
  weight?: string                           // "ledger" | "full" — a ledger row has no openable run
  incomplete?: boolean                      // this row SUMMARISES N fires ("at least N")
}
// Task entity. The wired-today fields match the backend Task dataclass
// (open/in_progress/done/cancelled/blocked, flat `project` string, `labels`).
// The richer fields (exit_criteria, action_plan, typed dependencies, phased
// notes, agent_instructions_template, task_list hierarchy) anticipate the
// TasksMultiServer construct — the UI renders them but the backend may not
// persist them yet (surfaced with a "soon" tag in the form).
export type TaskStatus = 'open' | 'in_progress' | 'blocked' | 'done' | 'cancelled'
export type TaskPriority = 'critical' | 'high' | 'medium' | 'low' | 'trivial'
export type DependencyType = 'BLOCKS' | 'REQUIRED_FOR'
export interface TaskDependency { task_id?: string; depends_on_task_id?: string; dependency_type?: DependencyType }
export interface ExitCriterion { description: string; status?: 'incomplete' | 'complete'; comment?: string; met?: boolean }
export interface ActionPlanItem { content?: string; description?: string; sequence?: number; completed?: boolean }
export interface TaskNote { content: string; timestamp?: string; created_at?: string; phase?: 'research' | 'execution' | 'general' }
export interface ProjectItem { id: string; name: string; is_builtin?: boolean; status?: 'active' | 'archived'; workspace_dir?: string; context_dir?: string; name_locked?: boolean; agent_instructions_template?: string; brief?: string; task_list_count?: number; created_at?: string; updated_at?: string }
export interface ProjectLinkedItem { id: string; name: string; status: string; error_message?: string | null }
/** How far a run-written knowledge item travels (WORK-CONTAINERS §1.6). CLOSED — the two
 *  values the backend can send; a view that renders these must handle both explicitly. */
export type SharingPolicy = 'private' | 'shared'
/** A knowledge item surfaced in a project's view. `source_project` is "" for the project's
 *  own items and the OWNING project's name for a `shared` item from another container. */
export interface ProjectKnowledgeItem {
  id: string
  title: string
  kind: string
  summary: string
  updated_at: string
  project_id: string
  run_id: string
  sharing_policy: SharingPolicy
  source_project: string
}
// Work board (WORK-CONTAINERS §1/§5.2/§6.1). `WorkRow` mirrors `containers.BoardRow.to_dict()`;
// `WorkSection` is one heterogeneous source's own status (per-section isolation — a failed
// source degrades ONE section, never the board); `board` is the state-grouped view with
// needs-input pinned first.
export type WorkState = 'needs_input' | 'working' | 'queued' | 'suspended' | 'review' | 'done'
export interface WorkClaim { holder: string; expires_at: number; taken_at: number; renewals: number }
export interface WorkRow {
  run_id: string; title: string; state: WorkState; origin: string; project_id: string
  claim: WorkClaim | null; collapsed: boolean; attention: boolean; resumable: boolean
}
export interface WorkGroup { state: WorkState; count: number; attention: number; rows: WorkRow[] }
export interface WorkSection { name: string; items: WorkRow[]; status: 'ok' | 'loading' | 'error'; error: string; loadedAt: number }
export interface WorkBoard {
  board: WorkGroup[]; sections: WorkSection[]
  completeness: 'complete' | 'inferred' | 'partial' | 'error'
  attention: number; loadedAt: number
}
export interface TaskListItem { id: string; name: string; project_id: string; agent_instructions_template?: string; created_at?: string; updated_at?: string }
export interface BlockReason { is_blocked?: boolean; blocking_task_ids?: string[]; blocking_task_titles?: string[]; message?: string }
export interface TaskItem {
  id: string; title: string; status: string; description?: string
  provider?: string; project?: string; assignee?: string; priority?: string
  // WHO created it (TEAM-SHARED-ENTITIES §1) — distinct from assignee, who does it.
  author?: string
  labels?: string[]; depends_on?: string[]; due?: string; url?: string
  created_at?: string; updated_at?: string
  // rich / forward-looking (may be absent from the backend today)
  task_list?: string
  dependencies?: TaskDependency[]
  exit_criteria?: ExitCriterion[]
  action_plan?: ActionPlanItem[]
  notes?: TaskNote[]
  research_notes?: TaskNote[]
  execution_notes?: TaskNote[]
  agent_instructions_template?: string
  block_reason?: BlockReason
  blocked_reason_kind?: string
  task_list_id?: string
  order?: number
  comment_count?: number
  // present only on a PUT response: the full set of tasks whose status cascaded
  // (the edited task + auto-block/unblock'd dependents) so the client patches all.
  reconciled?: TaskItem[]
}
// Server DAG snapshot (GET /api/tasks/graph) — adjacency + analysis (seam S3).
export interface TaskGraphEdge { from: string; to: string; type: DependencyType }
export interface DependencyAnalysis {
  completion_pct: number; leaf_task_ids: string[]; root_task_ids: string[]
  critical_path: string[]; cycles: string[][]
  bottleneck_tasks?: { id: string; dependents: number }[]
}
export interface TaskGraphData { tasks: TaskItem[]; edges: TaskGraphEdge[]; analysis: DependencyAnalysis }
export interface TaskComment { id: string; task_id: string; author: string; body: string; created_at: string }

// A decompose proposal — one task the loop intake suggests (index-based deps).
export interface ApiProposedTask { title: string; description?: string; priority?: string; depends_on?: number[] }

// WORKFLOWS-V2 Phase 1: the old SOP types (WorkflowStep/Scope/Graph/Item/Match)
// lived here. Slice 7b lands the v2 run/def types below, now that the API mounts.
//
// The stub stays: it is the shape the loop plan-review pickers type against, and the
// persisted `workflow_ids` field still flows through them. Kept separate from
// `WorkflowDef` rather than merged, because the picker wants a flat label list while a
// def is a node TREE — collapsing them would force the picker to understand the spec.
export interface WorkflowDefStub {
  id: string; name: string; description?: string; enabled?: boolean
  scope?: string; tags?: string[]; steps?: Array<{ id?: string; title: string; instruction?: string }>
}

// ── WORKFLOWS-V2 (Slice 7b) ──
// A node in a spec tree. Kind-specific settings live in `config` (the backend's
// tolerant-reader contract), so this type stays valid as node kinds gain fields.
export interface WorkflowNode {
  kind: string; id?: string
  children?: WorkflowNode[]
  body?: WorkflowNode
  cases?: Record<string, WorkflowNode>
  default?: WorkflowNode
  config?: Record<string, unknown>
  needs?: string[]
}
export interface WorkflowDefSummary {
  name: string; description: string; source: string; version: number; tags: string[]; provider: string
}
/** A template WITH its surfacing state — the row `GET /api/workflows/surfacing` returns.
 *  Separate from WorkflowDefSummary because the thin list is on the picker's hot path and
 *  deliberately does not pay for a per-def run-history lookup; this is what the templates
 *  list renders. */
export interface WorkflowSurfacingRow {
  name: string; provider: string
  surface_mode: 'off' | 'passive' | 'suggest'
  summary: string; when_to_use: string
  cadence_days: number
  escalation: 'manual' | 'auto'
  packs: string[]
  guided: boolean
  freshness: 'never_run' | 'fresh' | 'due_soon' | 'overdue' | 'stale'
  overdue: boolean
  last_completed_at: number
  hands_off_to: Array<{ target_def: string; condition: string; context_fields: string[]; requires_user_request: boolean }>
}
/** One reachability-doctor finding: a def no channel can produce. Typed CODE, not prose — the
 *  backend learned that lesson when a message containing the word "secret" was matched as if it
 *  were one. */
export interface WorkflowSurfacingFinding { name: string; code: string; detail: string }
// One declared input. Named (rather than inlined on WorkflowDef) because the run dialog builds
// its fields from these, and a picker that could not reference the type would re-describe it.
export interface WorkflowInputParam {
  type?: string; required?: boolean; default?: unknown; help?: string
}
export interface WorkflowDef {
  name: string; description?: string; version?: number; source?: string; provenance?: string
  root: WorkflowNode
  inputs?: Record<string, WorkflowInputParam>
  tags?: string[]
  metadata?: {
    risk?: string
    requirements?: Record<string, string[]>
    // How the template is driven (WF2-R15) — surfaced in the picker so a user choosing a
    // template can see a concrete example rather than inferring one from the node tree.
    steering_examples?: Array<{ event?: string; description?: string }>
    /** Declared template-to-template transitions (`DefMetadata.hands_off_to`, S60). The def
     *  payload has always carried these — `DefMetadata.from_dict` parses them on the bundled-def
     *  load path — but this type declared only 3 of the backend's 20 metadata keys, so the field
     *  was invisible to TypeScript and no surface could read it. `handoffs_from_def` drops entries
     *  with no `target_def`, so the FE applies the same filter rather than rendering an edge that
     *  points nowhere. */
    hands_off_to?: WorkflowHandoff[]
  }
}
/** One declared transition out of a template. `condition` is prose (when to take the edge);
 *  `context_fields` name what carries over; `requires_user_request` marks an edge the system must
 *  never take on its own. */
export interface WorkflowHandoff {
  target_def: string
  condition?: string
  context_fields?: string[]
  requires_user_request?: boolean
}
export type WorkflowRunStatus =
  'draft' | 'running' | 'paused' | 'needs_input' | 'complete' | 'failed' | 'cancelled' | 'escalated'
// A node INSTANCE. `instance_path` is the engine's addressing key (a foreach body
// produces many instances of one node id), so it — not node_id — is the list key.
export interface WorkflowNodeState {
  instance_path: string; node_id: string; state: string; attempt?: number
  degraded_reason?: string
  failure?: { class?: string; cause_plain?: string; remediation?: string; terminal_reason?: string } | null
  // Per-item foreach context (WF2-R5): what a "[3/12] auth.py" row needs. Present only on an
  // iterated node — a fan-out of twelve otherwise renders as twelve rows distinguishable only
  // by an index suffix, which is useless for telling which item is stuck.
  item_index?: number; item_total?: number; item_label?: string
}
export interface WorkflowRunSummary {
  id: string; workflow_name: string; status: WorkflowRunStatus; spec_version: number
  created_at: string; started_at?: string | null; completed_at?: string | null
  elapsed_seconds?: number; total_tokens?: number; error_message?: string
  attention?: Record<string, unknown> | null
  project_id?: string; mode?: string
}
export interface WorkflowRunDetailData {
  run_id: string; workflow: string; status: WorkflowRunStatus; spec_version: number
  error?: string; attention?: Record<string, unknown> | null
  tokens?: number; elapsed_secs?: number
  // The containing project (empty when unscoped) — the run view scopes its per-project
  // judge-guidance control on this, since that guidance writes through the project and is
  // what reaches this run's worker and judge sessions (LOOPS-EVOLUTION R14).
  project_id?: string
  nodes: WorkflowNodeState[]
}
// One pending human-input gate. `ask` is the typed payload ONE renderer covers
// (approval|choice|text|form), so the inbox card and the run view share a component.
export interface WorkflowContinuation {
  resume_token: string; node_id: string; instance_path: string
  ask: { kind?: string; prompt?: string; choices?: string[]; fields?: Array<{ name: string; type?: string; label?: string; required?: boolean; choices?: string[] }> }
  handoff: { scope?: string; status?: string; outstanding?: string[]; checks_run?: string[]; next_steps?: string[]; risks?: string[] }
  expires_at: number; expired: boolean
}
export interface WorkflowCascadePreview {
  rerun: string[]; stale: string[]; skipped: string[]; committed_effects: string[]; needs_confirmation: boolean
}
// The §5 reconstructability set for one terminal node (WF2-A2) — what the WV-10 inspector
// drawer renders. `resolved_prompt` is the fully-resolved post-binding prompt inline, or a
// `{ ref }` when it was too large to inline; `output` is the node's value, or an
// `{ artifact_ref }` when the value was offloaded. Every text field arrives redacted — the
// backend strips credentials before this leaves the process.
export interface NodeInspect {
  run_id: string; node_id: string; instance_path: string; state: string
  resolved_prompt: string | { ref: string }
  resolved_inputs: Record<string, unknown>
  output: unknown | { artifact_ref: string }
  attempts: Array<Record<string, unknown>>
  ledger_events: Array<Record<string, unknown>>
  cached: boolean
}
// The code-run workspace review (WORK-CONTAINERS §4.1) — the cockpit's diff panel and the two
// reintegration verbs. `changed` EXCLUDES the engine's own machinery (setup markers, files the
// preserve pass copied in), because a review panel listing them is one the user learns to skim
// with the file that mattered in the same list.
//
// Both verbs are OFFERS, never actions: `safe` says whether picking one would conflict, and the
// gateway deliberately has no endpoint that performs them — reviewing before it lands is why the
// run was isolated. `preserved_workspace_path` is non-empty only when the workspace is alive AND
// dirty; a path to a clean directory is a false lead.
export interface WorkflowDiffEntry { path: string; status: string; staged: boolean }
export interface WorkflowReintegrationVerb {
  verb: 'apply_locally' | 'checkout_branch'; label: string; detail: string; safe: boolean
}
export interface WorkflowWorkspaceReview {
  run_id: string
  workspace: {
    run_id: string; path: string; branch: string; alive: boolean; dirty: boolean
    changed: WorkflowDiffEntry[]
    preserved_workspace_path: string
  }
  reintegration: {
    run_id: string; branch: string; changed_files: number; conflicts: string[]
    verbs: WorkflowReintegrationVerb[]
    note: string
  }
  declared: {
    mode?: string; isolated?: boolean; name?: string; degraded_reason?: string
    setup?: { ran: string[]; skipped: string[]; failed: string[]; blocked_run: boolean }
    issues?: Array<{ code: string; message: string; fatal: boolean }>
  }
}
// One artifact this run published (WORK-CONTAINERS §2.5 outbox). `kind` is what the cockpit resolves
// through the contentTypes registry — the route declares the TYPE and never the renderer, so a newly
// registered kind previews here without touching the outbox.
export interface WorkflowOutboxEntry {
  slug: string
  artifact: string
  kind: string
  action: string
  change_note: string
  node_id: string
  updated_at: string
  // False when a referenced local file could not be copied into the version dir — surfaced, because
  // an artifact that only LOOKS self-contained breaks silently when the workspace goes away.
  self_contained: boolean
}
// The §6.4 nine-question introspection projection for one run (WORK-CONTAINERS R6). Every field is
// a projection over the run's own journal — there is no metrics store behind this, so a number here
// is always traceable to a ledger event.
export interface WorkflowRunStats {
  run_id: string
  tokens: number
  cached_tokens: number
  cost_usd: number
  steps_completed: number
  steps_failed: number
  steps_cached: number
  duration_secs: number
  // Latency to FIRST output, kept separate from total duration: one is what a watching user feels,
  // the other is what a scheduler budgets, and a single "duration" would conflate them.
  first_byte_ms: number
  models: string[]
  unverified_steps: number
  verification_debt: number
  cache_hit_rate: number
}
export interface WorkflowGateStats {
  node_id: string
  passes: number
  rejects: number
  retries_consumed: number
  total: number
  pass_rate: number
  // Non-empty ONLY when there is a real sample behind it: "0 rejections in 0 runs" and "0 in 40"
  // are different claims and only the second is evidence. The badge renders this string verbatim.
  fake_check_warning: string
}
export interface WorkflowTemplateCard {
  template: string
  runs: number
  cost_p50: number
  cost_p95: number
  duration_p50: number
  duration_p95: number
  failure_rate: number
  warnings: string[]
}
export interface WorkflowProofSection {
  summary: string
  verified_steps: number
  total_steps: number
  coverage: number
  evidence_files: string[]
  warnings: string[]
  // False would mean a Proof section with neither evidence nor a caveat — the worst possible
  // surface, because it looks like proof. The backend guarantees one or the other.
  honest: boolean
}
export interface WorkflowTimelineRow {
  kind: string
  ts: string
  node_id: string
  instance_path: string
  attempt?: number | null
  state: string
  duration_secs?: number | null
  tokens?: number | null
  cost_usd?: number | null
  model: string
  approved?: boolean | null
  detail: string
}
export interface WorkflowNextIfSilent {
  action: 'nothing' | 'waits' | 'proceeds'
  detail: string
  queued: string[]
}
export interface WorkflowIntrospection {
  run_id: string
  workflow: string
  stats: WorkflowRunStats
  gates: Record<string, WorkflowGateStats>
  template_card: WorkflowTemplateCard
  proof: WorkflowProofSection
  timeline: WorkflowTimelineRow[]
  // What the run TOUCHED — published artifacts and files handed in. Answers "what changed" for
  // THINGS where the timeline answers it for STEPS, which is why they arrive together.
  touched: WorkflowTouchedItem[]
  answers: {
    running: { status: string; workflow: string; nodes: unknown[] }
    changed: WorkflowTimelineRow[]
    blocked: unknown[]
    approval: Array<{ resume_token: string; node_id: string; ask: unknown }>
    failed: unknown[]
    cost: WorkflowRunStats
    risky: { degraded: unknown[]; gates: WorkflowGateStats[]; verification_debt: number }
    next: WorkflowNextIfSilent
    proof: WorkflowProofSection
  }
  // Empty is the healthy answer. A non-empty entry NAMES a checklist question the payload cannot
  // answer — a backend gap the FE cannot close by rendering harder, so it is shown, not hidden.
  checklist_gaps: string[]
}
// One dashboard pin (WORK-CONTAINERS §6.5d). A REFERENCE, never a copy: no name and no content,
// because a denormalized title goes stale on the next rename and a card that is confidently wrong
// is worse than one that is absent.
export interface PinnedArtifact {
  slug: string
  pinned_at: string
  // The run that produced it, when a run did — so a pin can deep-link back to its cockpit.
  run_id: string
}
// One thing a run touched: an artifact it published, or a file handed into it. Both sources are
// run-scoped on the backend, which is what makes the attribution trustworthy.
export interface WorkflowTouchedItem {
  kind: 'artifact' | 'file'
  ref: string
  label: string
  action: string
  detail: string
  node_id: string
  ts: string
}
export interface WorkflowDroppedFile {
  filename: string
  size: number
  sha256: string
  mime?: string
  lifecycle?: string
  accepted_at?: string
  approved?: boolean
}
export interface WorkflowDropStatus {
  enabled: boolean
  // Why the drop is off, when it is off. Rendered as-is: "this workflow does not declare a file
  // drop" is a configuration fact, and a generic "unavailable" would read as a bug.
  reason: string
  auto_accept_mimes: string[]
  max_files: number
  files: WorkflowDroppedFile[]
  accepted?: WorkflowDroppedFile[]
}
export interface WorkflowManifest {
  spec_semver: string
  node_kinds: Array<{ kind: string; container: boolean; lane: string }>
  gate_kinds: string[]; join_modes: string[]; loop_modes: string[]; item_error_policies: string[]
  pipes: string[]; mutation_ops: string[]; instance_states: string[]; run_statuses: string[]
}
// Prompt template (parametrized). Variables are TYPED — type ∈
// text|textarea|number|boolean|select — and the content carries {{name}}
// placeholders + {{> snippet}} includes the render endpoint resolves.
export type PromptVarType = 'text' | 'textarea' | 'number' | 'boolean' | 'select'
export type PromptKind = 'system' | 'user'
export type PromptSource = 'user' | 'bundled' | 'marketplace'
export interface PromptVariable { name: string; type: PromptVarType; description?: string; required?: boolean; default?: unknown; options?: string[] }
// Runnable "campaign template" (#17): the loop-launch config a runnable prompt
// carries. Non-empty launch_spec = the prompt is a template you fill + launch into a
// Project/Loop run (its rendered content becomes the task). Mirrors LoopComposer's
// create knobs; all optional (kind defaults to 'goal').
export interface LaunchSpec {
  kind?: LoopKind; agent?: string; model?: string; provider?: string; provider_agent?: string
  reasoning_effort?: string; execution?: 'solo' | 'multi_agent'; roster?: RosterMember[]
  strategy_id?: string; intake_rigor?: string; attended?: boolean; autopilot?: boolean
  max_cycles?: number; skill_ids?: string[]; workflow_ids?: string[]; project_id?: string
  success_criteria?: string; kind_config?: Record<string, unknown>
}
export interface PromptItem {
  name: string; kind?: PromptKind; title?: string; description?: string; content?: string
  variables?: PromptVariable[]; tags?: string[]; source?: string; updated_at?: number
  // Runnable template (#17): present + non-empty → fill-and-launch surfaces.
  launch_spec?: LaunchSpec
  // detail-only: the full variable set the fill-in UI renders (own ∪ snippets'),
  // and the snippet names this prompt includes.
  merged_variables?: PromptVariable[]; includes?: string[]
}
// A reusable fragment included by prompts/snippets via {{> name}}.
export interface PromptSnippet {
  name: string; title?: string; description?: string; content?: string
  variables?: PromptVariable[]; tags?: string[]; source?: string; updated_at?: number
  // detail-only: the prompts + other snippets that include this one ({{> name}}).
  used_by?: { prompts: string[]; snippets: string[] }
}
export interface PromptBinding { use_case: string; ref: string; effective_ref: string }
export interface PromptBindings { use_cases: string[]; default_ref: string; bindings: PromptBinding[]; available: PromptItem[] }
// Live authoring: render arbitrary (unsaved) template content through the real engine.
export interface PromptPreview { ok: boolean; rendered?: string; error?: string; detected_variables: PromptVariable[]; includes: string[] }
// The template-language reference the editor renders as a click-to-insert cheatsheet.
export interface PromptSyntaxFn { name: string; category: string; signature: string; description: string; insert: string }
export interface PromptSyntaxConstruct { category: string; label: string; snippet: string; description: string }
export interface PromptSyntax { functions: PromptSyntaxFn[]; constructs: PromptSyntaxConstruct[] }
export interface SkillItem { key: string; name: string; description: string; always: boolean; path?: string; source: string; type: string; loaded_by_agents: string[]; integrity?: 'intact' | 'tampered' | 'unverified'; agent?: string }
export interface EphemeralDraft { slug: string; title: string; body: string; created_at: string }
export interface SkillProposal { id: string; slug: string; description: string; triggers: string; kind: string; refine_target?: string; session_key: string; created_at: string; status: string; procedure_preview: string }
export interface SkillProposalDetail extends SkillProposal { procedure_md: string; source_excerpt: string }
export interface SkillIntegrity { name: string; integrity: 'intact' | 'tampered' | 'unverified'; ok: boolean; unlocked: boolean; mutated: string[]; missing: string[]; added: string[]; summary: string }
export interface SkillFile { path: string; size: number }
export interface SkillMarketplace { name: string; type: string }
export interface SkillSearchResult { id: string; name: string; description: string; source: string; url?: string; installs?: number }
export interface SkillMarketplaceDetail { id: string; name: string; audit_status?: string; files: Array<{ path: string; binary?: boolean }>; frontmatter?: Record<string, unknown>; body?: string; marketplace?: string }
export interface ToolItem { name: string; description: string; provider: string; parameters?: Record<string, unknown>; requires_approval?: boolean; risk_level?: 'safe' | 'caution' | 'destructive'; disabled?: boolean; locked?: boolean; providerDisabled?: boolean; group?: string }
export interface ToolLoadFailure { provider: string; error: string }
// The generated self-description document served at GET /api/manifest — the same
// shape an agent driving this instance reads (personalclaw/manifest.py).
export interface ManifestToolExample { summary: string; args: Record<string, unknown> }
export interface ManifestTool { name: string; provider: string; description: string; parameters?: Record<string, unknown>; requires_approval: boolean; risk_level: string; response_type: string; error_codes: string[]; examples: ManifestToolExample[] }
export interface ManifestRoute { method: string; path: string; summary: string; agent_callable: boolean }
export interface ManifestProvider { app: string; type: string; provider_type: string; capabilities: string[]; enabled: boolean; error?: string | null }
export interface Manifest { apiVersion: number; tools: ManifestTool[]; routes: ManifestRoute[]; app_surfaces: unknown[]; providers: { types: string[]; registered: ManifestProvider[] } }
// Discover (§6): a curated, hand-authored tour of the system's user-facing areas.
// `try_it` is a deep link into an existing page — a tip points, never enables. Tips
// leave the feed by being dismissed or by auto-hiding once the area is engaged.
export interface DiscoverTryIt { route: string; query: Record<string, string>; label: string }
export interface DiscoverTip { id: string; area: string; title: string; lesson: string; try_it: DiscoverTryIt }
export interface DiscoverArea { area: string; tips: DiscoverTip[] }
export interface DiscoverResponse { enabled: boolean; areas: DiscoverArea[]; visible_count: number; total: number }
export interface McpServer {
  name: string; command?: string; args?: string[]; status: string; tools: Array<string | { name: string; description?: string }>
  error?: string; source?: string; enabled?: boolean; presence?: Record<string, boolean>
}
/** P23d: the in-process MCP connection-pool observability snapshot (GET /api/mcp/pool-stats).
 *  `available:false` when the mcp SDK extra isn't installed (no pool exists). */
export interface McpPoolStats {
  available: boolean
  live_connections?: number; shared_conns?: number; session_conns?: number
  configured_servers?: number; spawns?: number; reaps?: number; served?: number
  evicted?: number; reused?: number
}
/** An MCP server configured in an external backend (e.g. Claude Code) that
 *  isn't yet in PersonalClaw — offered as an import suggestion on the Tools page. */
export interface ImportableMcpServer {
  name: string; backend: string; command?: string; args?: string[]
  env?: Record<string, string>; url?: string; headers?: Record<string, string>
}
export interface ToolInvokeResult { ok: boolean; output?: string; error?: string }
export interface HookItem {
  id: string; name: string; event: string; matcher: string; provider: string; provider_config: Record<string, unknown>
  timeout: number; enabled: boolean; last_run: number; last_status: string; run_count: number; used_by: string[]
}
// The wired data-event patterns (event_triggers.EVENT_PATTERNS). Each belongs to exactly one
// source (event_triggers.PATTERN_SOURCE), which the backend derives — the wire never supplies it.
// Kept in lockstep with the Python tuple; the meta table in triggerMeta.ts maps each to its one
// matcher field. A pattern the backend does not know is rejected server-side with a typed error.
export type EventPattern =
  | 'MemoryUpdate' | 'MemoryKeyPattern' | 'ContentMatch'
  | 'InboxMessage' | 'InboxSender' | 'InboxAddress'
  | 'AppEvent'
// Unified Trigger wire shape from /api/triggers (both kinds). The schedule
// helpers project it onto ScheduleJob; the lifecycle helpers onto HookItem.
export interface TriggerAction { provider: string; config: Record<string, unknown> }
export interface Trigger {
  // `GET /api/triggers` serves FOUR kinds (handlers/triggers.py `api_triggers_list`); `event` was
  // missing from this union while `_serialize_event` was already emitting it, so a data-event row
  // was untypeable on the wire and the list page fetched only three of the four sources.
  kind: 'schedule' | 'lifecycle' | 'event' | 'store'; id: string; raw_id: string; name: string; enabled: boolean
  action: TriggerAction
  // event fields (kind=event) — the data-event trigger's pattern + the ONE matcher its pattern
  // reads (`eventPatternMeta().matcher` names which), plus its fire budget.
  pattern?: string; sender_glob?: string; address_glob?: string; key_glob?: string; content_re?: string
  event_glob?: string; fire_count?: number; max_fires?: number
  // store fields (kind=store) — the unified TriggerStore kinds with no legacy backend
  // (file/web_watch/idle/run_completed/view/webhook). Created via the automation_* chat tools.
  store_kind?: string; created_by?: string; spec?: Record<string, unknown>
  // `state` is the LIFECYCLE (`active | paused | autopaused | parked | quarantined | retired`);
  // `health` is the rollup (`ok | degraded | parked | failing`). Two vocabularies, both needed:
  // an autopaused trigger is `health: failing`, and "failing" does not say it has STOPPED (S164).
  // `last_error` (declared with the schedule fields below — one shared interface) carries the
  // failure the lifecycle acted on; the store panel had no reader for it until S169.
  health?: string; state?: string; broken?: string[]
  // schedule fields (kind=schedule)
  message?: string; schedule?: string; cron_expr?: string | null; every_secs?: number | null
  agent?: string | null; model?: string | null; channel?: string | null; approval_mode?: string | null
  silent?: boolean; strict_schedule?: boolean; timezone?: string | null; skip_dates?: string[]
  script?: string | null; command?: string | null
  last_run_ts?: number | null; next_run_ts?: number | null; last_status?: string | null
  has_result?: boolean; last_result?: string | null; last_error?: string | null
  is_running?: boolean; running_since?: number | null; has_session?: boolean; created_ts?: number | null
  // lifecycle fields (kind=lifecycle)
  event?: string; matcher?: string; timeout?: number; last_run?: number; run_count?: number; used_by?: string[]
}
/** Project the shared ScheduleForm's flat draft body onto the unified Trigger
 *  wire shape: a single canonical `action` + the schedule mechanism fields. The
 *  schedule executor dispatches every provider from this action, so the form's
 *  agent / script / command "exec modes" become invoke-agent / run-script / bash
 *  actions. (TriggerCreatePage already sends `action` directly; this serves the
 *  shared ScheduleForm edit path via ScheduleDetail.) */
function _scheduleBodyToWire(body: Record<string, unknown>): Record<string, unknown> {
  const { message, agent, model, approval_mode, script, command, zt_timeout, action, ...rest } = body
  if (action) return { ...rest, action }  // already action-shaped (create page)
  let act: TriggerAction
  if (script) act = { provider: 'run-script', config: { script, timeout: Number(zt_timeout) || 0 } }
  else if (command) act = { provider: 'bash', config: { command, timeout: Number(zt_timeout) || 0 } }
  else act = { provider: 'invoke-agent', config: { task_template: message ?? '', agent: agent ?? '', model: model ?? '', approval_mode: approval_mode ?? '' } }
  return { ...rest, action: act }
}

/** Project a lifecycle Trigger onto the legacy HookItem shape the shared
 *  Lifecycle* components consume (flatten action → provider/provider_config,
 *  bare id). */
function _triggerToHook(t: Trigger): HookItem {
  return {
    id: t.raw_id, name: t.name, event: t.event ?? '', matcher: t.matcher ?? '',
    provider: t.action.provider, provider_config: t.action.config ?? {},
    timeout: t.timeout ?? 30, enabled: t.enabled, last_run: t.last_run ?? 0,
    last_status: t.last_status ?? '', run_count: t.run_count ?? 0, used_by: t.used_by ?? [],
  }
}
// An action provider (renamed from "hook provider" in the Triggers vision) —
// the catalog of things a trigger can run. settingsSchema drives the config form.
export interface ActionProvider {
  name: string; display_name: string; supports_blocking: boolean
  settingsSchema: { type?: string; properties?: Record<string, unknown>; required?: string[] }
}
// Server-sourced trigger $variable catalog (GET /api/triggers/variables). The UIs
// read this instead of mirroring the per-event var lists — backend is the source
// of truth (hooks.LIFECYCLE_EVENT_CATALOG + schedule.SCHEDULE_VARS).
// `dormant` (S67): the event is declared and configurable but NO code fires it — 7 of the 15 are.
// Server-sourced for the same reason the vars are: a hard-coded list here would tell a user their
// working hook is dead the moment the backend wires one.
export interface LifecycleEventInfo { event: string; label: string; desc: string; vars: string[]; blocking: boolean; dormant?: boolean; dormant_reason?: string }
// One app-contributed trigger source and the events it declares (AUTO-A4). Read from the LIVE
// `trigger_sources` registry, so a disabled app's source is absent rather than offered — authoring a
// trigger against an event that cannot fire is the failure this list exists to prevent.
// `source_event` is the namespaced name (`app:<app>:<event>`) the backend matches `event_glob`
// against; the UI never re-derives that prefix, or it would drift from `trigger_sources.namespace`.
export interface AppSourceEvent { event: string; source_event: string }
export interface AppSourceInfo { app: string; label: string; events: AppSourceEvent[] }
export interface TriggerVariables { schedule: string[]; lifecycle: LifecycleEventInfo[]; app_sources: AppSourceInfo[] }
// One manual store/schedule-trigger fire (POST /api/triggers/{schedule|store}:{id}/run).
// `ok` is whether the action ACTUALLY RAN — not whether the request was understood. A trigger whose
// action cannot be resolved answers 200 with `ok: false` and the reason in `result`, because a
// guardrail/config outcome is not a malformed request (#395: this used to answer `ok: true` with the
// failure as prose, so a silent no-op was indistinguishable from a completed run). `refused` carries
// the kill-switch reason when incident mode suspended the fire.
export interface TriggerRunResult { ok: boolean; name?: string; result?: unknown; refused?: string; running?: boolean }
// The Proposal Inbox row (GET /api/learning/proposals). `renderable` is the backend's own honesty
// flag: a row missing provenance cannot be shown weighably, and `bulk_acceptable` already accounts
// for it — the FE must not re-derive either, or the two will disagree about what is safe to accept.
export interface LearningRow {
  id: string; kind: string; title: string; provenance: string
  source_cadence: string; source_excerpt: string
  evidence_refs: string[]; reinforcements: number; confidence: number
  manifest_valid: boolean; manifest_issues: string[]
  risk_tier: string; status: string
  renderable: boolean; bulk_acceptable: boolean
}
export interface LearningInbox {
  rows: LearningRow[]; total: number
  by_kind: Record<string, number>; by_tier: Record<string, number>
  flagged: number; unrenderable: string[]; bulk_acceptable: number
}
// One day of the capture panel. An EMPTY bucket is the signal: `health()` cannot see a day where
// capture never ran, which is the failure the staging tier exists to expose.
export interface StagingDay {
  day: string; passes: number; by_outcome: Record<string, number>
  produced: number; errors: number; staged: number
  cost_usd: number; proposal_ids: string[]
}
export interface StagingWeek {
  days: number; buckets: StagingDay[]
  silent_days: string[]; error_days: string[]
  produced_total: number; cost_usd: number
}

// The flywheel observability panel (GET /api/learning/health — LEARN-R14b).
//
// EVERY score and rate here is `number | null`, and null means UNMEASURED, not zero. The
// backend refuses to score silence: a component with no data is excluded from the
// composite and says so, because reporting an un-instrumented subsystem as 0% is
// indistinguishable from reporting a broken one and the user's only apparent fix would
// be to generate traffic.
/** LEARN-R16's five-way verdict plus its honest not-yet state. Closed — the FE maps every
 *  member explicitly rather than falling back, because a default branch would render a
 *  verdict nobody defined as whatever the fallback said. */
export type AttributionVerdict =
  | 'EFFECTIVE' | 'PARTIALLY_EFFECTIVE' | 'INEFFECTIVE' | 'MIXED' | 'HARMFUL' | 'PENDING'

export interface HealthComponent {
  name: 'precision' | 'capture' | 'utilization' | 'judge'
  score: number | null
  weight: number
  detail: string
}
export interface MaeBucket {
  bucket: string
  /** Verdicts that landed in this confidence band. */
  n: number
  /** …of which a human actually labelled. `mae` is null until at least one did. */
  labelled: number
  mae: number | null
}
export interface LearningHealth {
  days: number
  composite: {
    score: number | null
    components: HealthComponent[]
    measured: number
    of: number
    ideal_band: [number, number]
  }
  utilization: { samples: number; mean: number | null; ideal_band: [number, number] }
  capture: { days: number; passes: number; errors: number; cost_usd: number; all_ok_streak: number }
  surfacing: { surfaced: number; used: number; precision: number | null }
  cost_by_op: { op: string; passes: number; cost_usd: number }[]
  judge: {
    runs_scanned: number
    verdicts: number
    divergences: number
    false_pass_rate: number | null
    nodding_gates: { template: string; node: string; detail: string }[]
    mae: { buckets: MaeBucket[]; labelled: number; unlabelled: number; no_confidence: number }
  }
  attribution: {
    proposers: {
      source: string
      counts: Record<string, number>
      total: number
      decided: number
      harm_rate: number
      effective_rate: number
    }[]
    history: { source: string; verdict: AttributionVerdict }[]
  }
  /** The last ablation-delta sweep, or `{}` when none has run yet (§2.5). */
  ablation: { at?: string; rows?: { heuristic: string; delta: number; verdict: string; items: number }[] }
}

// One projected fire in the week grid (GET /api/triggers/week — AUTO-A3). `suppressed_by` is "" for
// a fire that will actually run, "quiet" inside a quiet window, "skipped" on one of the trigger's
// skip_dates. The two suppression kinds stay distinct because they are different promises: a quiet
// window defers a time of day and may catch up, while a skip date removes a whole day and never
// does. The server ANNOTATES rather than filters — a grid that hid suppressed fires would show a
// schedule the user does not have, and explaining an unexpected gap is the view's whole purpose.
export interface WeekOccurrence {
  trigger_id: string
  trigger_name: string
  /** Epoch seconds. Placed into a cell in the VIEWER's timezone; `server_tz` is captioned so a
   *  mismatch with the host is legible instead of silent. */
  at: number
  suppressed_by: '' | 'quiet' | 'skipped' | 'off_duty'
  reason: string
}

export interface WeekProjection {
  start: string
  end: string
  server_tz: string
  occurrences: WeekOccurrence[]
  /** Trigger ids whose projection hit the per-trigger occurrence cap. Named rather than a bare
   *  boolean: "some trigger was capped" is not actionable, and a silently partial week reads as an
   *  accurate forecast. */
  truncated: string[]
}

// One manual event-trigger fire (POST /api/triggers/event:{id}/run|test). `ran` and `success` are
// deliberately separate: `ran` is whether the trigger reached its action provider at all (false for
// incident mode, an unregistered provider, or a denylist block — `reason` says which), while
// `success` is that provider's own verdict. Collapsing them would report a misconfigured action as
// "never fired", which points the user at the wrong thing entirely.
export interface EventFireResult {
  ok: boolean
  result: { ran: boolean; reason: string; success?: boolean; exit_code?: number; stdout?: string; stderr?: string; error?: string; duration_ms?: number }
}
// Knowledge = a library of TYPED items (note/bookmark/media/docs) with extracted
// content + AI insights. The typed-format enum, media/file fields, structured
// insights, and provider attribution mirror the target vision (OpenForge-style);
// the current PClaw backend persists a RAG subset (item_type string, title/
// content/summary/tags + entities/graph), so the richer fields are
// rendered ahead of the backend (SoonTag) — see knowledge-entity-vision.md.
export type KnowledgeType =
  | 'note' | 'fleeting' | 'journal' | 'gist' | 'bookmark'
  | 'image' | 'audio' | 'video' | 'pdf' | 'document' | 'sheet' | 'slides'
export interface KnowledgeEntity { id: string; name: string; entity_type?: string; description?: string }
export interface KnowledgeRelation { id: string; source_name?: string; target_name?: string; relation_type?: string; weight?: number }
export interface KnowledgeItem {
  id: string; title?: string; content?: string; summary?: string
  item_type?: string; tags?: string[]
  provider?: string; status?: string
  is_pinned?: boolean; is_archived?: boolean
  // library curation (KNOWLEDGE-LIBRARY S1). read_state is a three-value cycle, not a
  // boolean — "reading" is the state a reading list exists to represent.
  read_state?: 'unread' | 'reading' | 'read'; favorited?: boolean
  created_at?: string; updated_at?: string
  _score?: number; _match_type?: string
  // vision fields (may be absent from the PClaw backend today)
  type?: KnowledgeType; gist_language?: string; url?: string; url_title?: string
  mime_type?: string; file_size?: number; thumbnail_path?: string; file_path?: string; word_count?: number
  file_metadata?: { width?: number; height?: number; format?: string; page_count?: number; sheet_count?: number; slide_count?: number; row_count?: number; line_count?: number } & Record<string, unknown>
  insights?: Record<string, unknown> | null; ai_summary?: string; ai_title?: string
  // node-graph ingestion lifecycle (#30): queued|processing|done|partial|failed
  processing_status?: string; processing_error?: string
  // set by the list endpoint when content is a truncated preview (full body via GET /items/{id})
  content_truncated?: boolean
  // whether the item has an embedding vector (the raw vector itself is never sent — export-only)
  has_embedding?: boolean
  // populated by GET /items/{id}
  entities?: KnowledgeEntity[]; relations?: KnowledgeRelation[]
  // populated by GET /items/{id}/related (overlap count)
  shared_entities?: number
}
/** The ingestion node-graph shape for an item's type — nodes + edges + terminals. */
export interface KnowledgeIngestGraph {
  item_type: string
  nodes: { node_type: string; backend?: string; model_backed?: boolean; terminal?: boolean }[]
  edges: { from: string; to: string; when?: string; loop?: boolean; max_iters?: number }[]
  processing_status?: string
  // Ground-truth per-node phase persisted at ingest end (done/failed/skipped) — the
  // detail UI prefers this over reconstructing phases from processing_error.
  node_phases?: Record<string, string>
}
/** One node's output in an item's extracted-content pool (#30 drill-down). */
export interface ExtractedContent {
  id: string; item_id: string; node_type: string; backend?: string
  text?: string; metadata?: Record<string, unknown>; created_at?: string
}
/** A natural-language intent — the Tier-3 ingestion layer. The user states a goal in
 *  plain language; the LLM decides per-item relevance and derives typed-field outcomes. */
export interface KnowledgeIntent {
  id: string; goal?: string; enabled?: boolean
  enabled_for?: string[]; propose_skill?: boolean
  outcome_count?: number  // recorded outcomes (list badge)
}
/** One typed field of an intent outcome, rendered type-aware in the UI. */
export interface IntentOutcomeField { name: string; type: string; value: unknown }
/** An intent's match against one item, stored BY VALUE (survives item deletion —
 *  item_id goes null but the takeaway + fields persist). */
export interface IntentOutcome {
  id: string; intent_id: string; intent_name?: string
  item_id: string | null; item_title?: string
  takeaway?: string; fields?: IntentOutcomeField[]; created_at?: string
}
export interface KnowledgeStats { items: number; entities: number; relations: number; embeddings: { enabled: boolean; model?: string; embedded_items?: number; stale_items?: number } }
// Inbox is a GENERAL entity: message-source providers (filesystem now;
// slack/email future) feed incoming messages into an AI-triage layer that adds
// classification + confidence + an optional drafted reply. Shape matches the
// backend InboxItem dataclass (inbox.py).
export type InboxClassification = 'needs_reply' | 'fyi' | 'noise'
export type InboxConfidence = 'high' | 'needs_review' | 'escalate'
// 'seen' is the read/unread boundary: surfaced to the user but not yet resolved.
export type InboxItemStatus = 'pending' | 'seen' | 'sent' | 'dismissed' | 'handled'
// What kind of attention an item wants. 'message' is the default so every item written
// before the inbox became a general attention store stays valid.
export type InboxItemKind =
  | 'message' | 'mention' | 'email' | 'agent_request'
  | 'proposal' | 'needs_input' | 'digest' | 'system'
export interface InboxThreadMsg { sender_name?: string; text?: string; ts?: string }
export interface InboxItem {
  id: string; channel: string; channel_name: string; thread_ts?: string | null
  message: string; sender_id: string; sender_name: string
  thread_context?: InboxThreadMsg[]
  classification: InboxClassification; draft?: string; confidence: InboxConfidence
  status: InboxItemStatus; created_at?: number; context_summary?: string; ts?: string
  // which source produced it (native / filesystem / slack / …) + whether the
  // source supports a reply (drives the Send gate). reply_target is native-only.
  source?: string; can_reply?: boolean; reply_target?: string
  // P11: user-favorited (a strong engagement signal + a star in the UI).
  favorited?: boolean
  // Feedback Signal (plan 58): per-judgment producer meta the thumbs attribute to.
  feedback_producers?: Record<'classification' | 'draft' | 'digest', FeedbackProducer | undefined>
  // Attention store (plan 42 S2): what kind of attention this wants, and the ids of the
  // things it is ABOUT — refs is what makes a needs_input row deep-link to its loop.
  item_kind?: InboxItemKind
  refs?: Record<string, string>
}
/** One row of the inbox kind-filter chips: what's present, and how much is unresolved. */
export interface InboxKindCount { kind: InboxItemKind; total: number; open: number; channel: boolean }
export interface InboxProvider { name: string; display_name: string; source_name: string }
export interface InboxHealth { running: boolean; last_poll_at?: number; last_poll_ok?: boolean; last_error?: string; poll_count?: number; stale?: boolean }
export interface InboxSourceHealth { name: string; active: boolean; kind: 'push' | 'poll'; can_reply: boolean }
export interface InboxStatus {
  enabled: boolean; user_id?: string
  native_source_active?: boolean; sources?: InboxSourceHealth[]
  watched_channels?: Array<{ id: string; name: string }>
  pending_count: number; total_count: number; health: InboxHealth
  poll_interval_seconds?: number
}
export interface InboxSettings {
  // alert_keywords / alert_on_name_mention removed in plan 42 S3 — alerting is now a
  // `conditions` block on a notification rule (see NotificationRuleRow).
  auto_cleanup_enabled: boolean
  retention_days: number
}
// One row of the security-event log (SEL) — the tamper-evident audit chain.
export interface SelEvent {
  event_id: string; timestamp: string; event_type: string; caller_identity?: string
  agent?: string; source?: string; operation?: string; tool_kind?: string; outcome?: string
  resources?: string; error?: string; prev_hash?: string
}
export interface SelVerify { valid: boolean; count?: number; broken_at?: string; error?: string }
// An archived chat session file (read-only browse). `key`=session key, `stamp`=
// archive timestamp slug, `mtime`=epoch seconds.
export interface SessionArchive { name: string; key: string; stamp: string; size: number; mtime: number }

/** A saved chat starter: the SETUP of a conversation, never its content
 *  (SESSION-MANAGEMENT S3). Empty agent/model mean "use the default at start time". */
export interface SessionTemplate {
  id: string; name: string; agent: string; model: string
  reasoning_effort: string; first_prompt: string; created_at: number
}
export type SessionTemplateInput = Omit<SessionTemplate, 'id' | 'created_at'>
// Portability (import/export archive). Manifest is the zip's MANIFEST.json;
// preview validates without applying, import returns what was merged/replaced.
export interface PortabilityManifest {
  version: number; format: string; created_at: string; hostname: string; user: string
  contents: Record<string, number>
}
export interface PortabilityPreviewResult { ok: boolean; error?: string; manifest?: PortabilityManifest }
export interface PortabilityImportResult { ok: boolean; error?: string; summary?: { mode: string; items: string[] }; manifest?: PortabilityManifest }
// One project's archive. `refused` names what did not arrive (a partial import is the normal case
// for an archive that travelled) and `secrets_expected` names the credentials the far side must
// re-enter — the archive deliberately carries neither their values nor a way to recover them.
export interface ProjectImportIssue { path: string; code: string; message: string; fatal: boolean }
export interface ProjectImportResult {
  project_name: string; accepted: string[]; refused: ProjectImportIssue[]
  secrets_expected: string[]; ok: boolean; summary?: string; preview?: boolean
  project_id?: string; written?: string[]; error?: string
}
// Update + changelog.
export interface UpdateCheck { available: boolean; changes: string; checked: boolean; auto_update: boolean; version?: string; latest?: string; kind?: 'git' | 'pip' | 'container' | 'desktop'; current?: string; update_available?: boolean; commits_behind?: number | null; apply_method?: string; instructions?: string[]; update_dev_mode?: boolean; release_notes?: string }

// settings entity payloads
export interface NotificationSettings {
  mute_all: boolean; quiet_hours_enabled: boolean; quiet_hours_start: string; quiet_hours_end: string
  min_severity: string
}
// Per-(source, kind) delivery rules (plan 42 S1/S3). `mode` is what happens when a
// notification of this kind passes the global gate above; `conditions` ESCALATE a quieter
// mode to immediate on a keyword or name mention.
export type NotificationMode = 'never' | 'badge' | 'immediate' | 'digest'
export type NotificationTarget = 'dashboard' | 'channel_dm' | 'push' | 'native'
export interface NotificationRuleRow {
  key: string; source: string; kind: string; label: string; severity: number
  mode: NotificationMode
  /** The registry default, so the UI can show "changed from default". */
  default_mode: NotificationMode
  /** True when the user has an explicit stored rule for this kind. */
  configured: boolean
  targets: NotificationTarget[]
  conditions: { keywords: string[]; name_mention: boolean }
}
export interface NotificationRulesDoc {
  rules: NotificationRuleRow[]
  digest: { schedule: string }
  targets: NotificationTarget[]
}
export interface NotificationRulePatch {
  mode?: NotificationMode
  targets?: NotificationTarget[]
  conditions?: { keywords?: string[]; name_mention?: boolean }
}
export interface MemorySettings { history_idle_hours: number; history_max_days: number; migrated?: boolean; l1_manifest?: boolean; active_recall?: boolean; proactive_commitments?: boolean; vault_enabled?: boolean; vault_path?: string; graph_enabled?: boolean; push_context?: boolean; push_min_confidence?: number }

/** Per-arm volunteered-vs-used precision for the push reflex
 *  (MEMORY-GRAPH-AND-VAULT §3). `used` = the record's recall count rose after it
 *  was volunteered, so precision is measured rather than asserted. */
export interface VolunteerArmStat { n: number; used: number; precision: number }
export interface VolunteerStats {
  arms: Record<string, VolunteerArmStat>
  overall: VolunteerArmStat
  enabled: boolean
  min_confidence: number
}
export interface MemoryVaultStatus { enabled: boolean; path: string; files: number; exists: boolean }
export interface MemoryVaultSyncResult { records: number; files: number; written: number; pruned: number; path: string }
export interface DailyDigest { day: string; text: string; created_at: string }
export interface MemoryStats {
  semantic_active: number; semantic_deleted: number; episodic_active: number; episodic_deleted: number
  events_count: number; embedded_count: number; embedding_provider?: string; has_legacy_memory?: boolean; migrated?: boolean
}
// A semantic memory entry. `value_json` is a JSON-encoded value (often double-
// encoded) — parse defensively for display.
export interface SemanticEntry { key: string; value_json?: string; created_at?: string; updated_at?: string; confidence?: number; source?: string; scope?: string; scope_ref?: string; tier?: string; recall_count?: number; contributor?: string; is_mine?: boolean }
export interface EpisodicEntry { id: string; text: string; tags?: string; conversation_id?: string; importance?: number; created_at?: string }
// One row of the memory audit trail.
export interface MemoryEvent {
  id: number; event_type: string; memory_type: string; memory_key?: string
  old_value?: string; new_value?: string; source?: string; created_at?: string
  undone_at?: string | null
}
export interface MemoryContextPreview { semantic_context: string; episodic_context: string }
// Memory health lint: auto-fixed counts + per-flag advisories (near-dup / stale / orphan / contradiction).
export interface MemoryLintFlag { check: string; key: string; detail: string }
export interface MemoryLint { auto_fixed: Record<string, number>; flags: MemoryLintFlag[]; flag_count: number }
// Entity graph (MEMORY-GRAPH-AND-VAULT §1). The type set is closed server-side.
export type MemoryEntityType = 'person' | 'project' | 'tool' | 'org' | 'topic' | 'place'
export interface MemoryEntity {
  id: string
  name: string
  entity_type: MemoryEntityType
  aliases: string[]
  source: string
  inbound_count: number
  last_linked_at?: string | null
}
export interface MemoryLink {
  id: number
  from_kind: string
  from_ref: string
  to_entity: string | null
  to_ref: string | null
  link_type: string
  provenance: string
  confidence: number
  context: string | null
  created_at: string
}
export interface MemoryGraphSummary {
  entities: number
  links: number
  linked_records: number
  proposals: number
  semantic_orphans: number
  episodic_orphans: number
  phantom_entities: number
}
export interface MemoryEntitiesResponse {
  entities: MemoryEntity[]
  summary: MemoryGraphSummary | Record<string, never>
  enabled: boolean
}
export interface MemoryGraphRebuild {
  ok: boolean
  seeded: { from_facts: number; from_knowledge: number }
  records_processed: number
  links_created: number
  before: MemoryGraphSummary
  after: MemoryGraphSummary
}
// Memory observability: live counts, injection-rejection reasons, and the injected-context preview.
export interface MemoryObservability {
  stats: Record<string, number>
  rejections: Record<string, number>
  context_preview: { semantic_chars: number; episodic_chars: number; lessons_chars: number; total_chars: number; semantic_preview?: string; episodic_preview?: string; lessons_preview?: string }
}
// A learned "lesson" rule (from the after-turn review or manual add).
export interface Lesson { rule: string; category: string; ts?: string }
// The auto-linked memory graph: fact nodes (grouped by key namespace) + relations.
// `ref` is a stable un-hashed handle onto the source memory (`sem:<key>`, `lesson:<rule>`,
// …) — the Memory Studio maps a selected list entry to its node by ref, not by re-hashing.
export interface MemoryGraphNode { id: string; label: string; group?: string; title?: string; ref?: string }
export interface MemoryGraphEdge { from: string; to: string }
export interface MemoryGraphData { nodes: MemoryGraphNode[]; edges: MemoryGraphEdge[] }
export interface SecurityStats { denied_commands: number; suspicious_patterns: number; tool_schemas: number; redaction_paths: number }
export interface DeniedCommands { builtin: string[]; user: string[] }
export interface EgressPolicyConfig { allow_hosts: string[]; deny_hosts: string[]; allow_private: boolean }
// User-teachable tool-output projection rule (TokenJuice OP6): output matching
// match_regex is projected with `strategy` (a builtin content type).
export type ProjectionStrategy = 'log' | 'diff' | 'json' | 'test' | 'csv' | 'code'
export interface ProjectionRule {
  name: string
  match_regex: string
  strategy: ProjectionStrategy
  /** Rule ops v2 — optional declarative line operations (0/empty = off). */
  head?: number
  tail?: number
  keep?: string
  skip?: string
  count?: string
}
// Feedback Signal (plan 58) — the closed judgment-target vocabulary + producer meta.
export type FeedbackTargetKind =
  | 'inbox_classification' | 'inbox_draft' | 'inbox_digest'
  | 'loop_finding' | 'routing_suggestion' | 'proposal_content' | 'app_judgment'
export interface FeedbackProducer { producer_kind: string; producer_id: string }
export interface FeedbackRecordBody {
  target_kind: FeedbackTargetKind
  target_id: string
  verdict: 'up' | 'down'
  reason?: string
  snapshot?: Record<string, unknown>
  producer_kind?: string
  producer_id?: string
}
export interface FeedbackProducerRow {
  producer_kind: string
  producer_id: string
  ups: number
  downs: number
  n: number
  accuracy?: number
  suppressed?: boolean
  collecting?: boolean
}
export interface FeedbackProducersResponse {
  producers: FeedbackProducerRow[]
  min_n: number
  window_days: number
}
// Investigate Anywhere (plan 60): the origin chip fields the session detail carries.
export interface InvestigateOrigin { kind: string; title: string; back_link: string }

export interface ToolsSavings {
  saved_chars: number
  saved_tokens_estimated: number
  estimated: boolean
  projection_count: number
  top_compressor: string | null
  by_compressor: Record<string, number>
  rows: unknown[]
}

/** Tool GROUPS (Context Economy §5) — the provider-grain partition of the tool
 *  surface. Activation is per-session runtime state (the agent drives it via
 *  reset_tools); what's configurable is `enabled` + the per-surface defaults. */
export interface ToolGroupInfo {
  name: string
  display: string
  alwaysOn: boolean
  toolCount: number
  tools: string[]
  capability: string
  /** False when the group's declared capability doesn't resolve — its tools are
   *  hidden entirely rather than offered in a state where they'd fail. */
  offerable: boolean
  instructions: string
}

export interface ToolGroupsData {
  enabled: boolean
  groups: ToolGroupInfo[]
  /** surface → group names that start ACTIVE. An empty array means "all groups". */
  surfaceDefaults: Record<string, string[]>
}

/** Process-lifetime runtime counters (`/api/system`.stats, `/api/status`.stats) — the Stats
 *  singleton's snapshot, reset on gateway restart. Every field here has a writer on a real
 *  runtime path AND a reader in UsagePanel; the six message/tool-approval counters and `timeouts`
 *  that used to be declared here had neither and were removed from the backend. */
export interface SystemAgentStats {
  sessions_created: number; sessions_cleaned: number
  subagents_spawned: number; subagents_completed: number; subagents_failed: number
  input_tokens: number; output_tokens: number
  cache_creation_tokens: number; cache_read_tokens: number
  total_turns: number; total_duration_ms: number
}
export interface SystemInfo {
  hostname: string; version?: string; os: string; platform: string; python: string; arch: string; pid: number; cpu_count: number; cwd: string
  mem_total_gb: number; proc_mem_mb: number; mem_free_gb: number; mem_used_gb: number
  load_1m: number; load_5m: number; load_15m: number; cpu_pct: number; proc_cpu_pct?: number; ip?: string
  disk_total_gb?: number; disk_free_gb?: number
  gpu_present?: boolean; gpu_vendor?: string; gpu_model?: string
  net_rx_kbs?: number; net_tx_kbs?: number
  thread_count?: number; child_processes?: number; mcp_total?: number
  mcp_processes?: { sandbox: number; agent_cli: number; mcp_server: number }
  stats?: SystemAgentStats
  // NOTE: backend also returns ollama_* fields — intentionally NOT typed/surfaced
  // here (vendor leakage).
}
export interface AuthStatus { mode: string; bind_host: string; valid: boolean; minutes_remaining?: number; oauth2_issuer?: string }

// A pending tool approval (GET /api/approvals + the `approval` WS event carry the
// SAME shape — see state._pending_approvals). The dashboard Action Center resolves
// these inline via approve/reject.
export interface PendingApproval {
  id: string; source: string; tool: string
  tool_input?: unknown; tool_purpose?: string
  session: string; ts: number
}

// GET /api/status — the live status snapshot (uptime, version, capability counts,
// update-availability, YOLO). Exactly the fields status_snapshot() + api_status
// return; model/tool/app/skill counts are NOT here (the System Health widget
// sources those from their own endpoints).
export interface DashboardStatus {
  uptime: string; uptime_secs?: number; start_time?: number
  sessions?: number; messages?: number; cron_jobs?: number; lessons?: number; subagents?: number
  update_available?: boolean; version?: string; platform?: string
  /** Non-null while a self-update pipeline is in flight (step: pulling/installing/
   *  building/restarting/error/failed) — lets a freshly-loaded page pick up an
   *  update already in progress. */
  update_progress?: { step: string; detail?: string } | null
  yolo?: boolean; yolo_expires_in?: number
  os_type?: string; arch?: string; cpu_count?: number; mem_total_gb?: number
  stats?: SystemAgentStats
}

export interface SettingsProvider {
  name: string; displayName?: string; description?: string; version?: string; author?: string
  enabled: boolean; error?: string; available?: boolean; unavailableReason?: string
  // managed = a lifecycle app provider (installByDefault: install/uninstall is its
  // on/off). false = an always-on native built-in (mandatory, no toggle).
  managed?: boolean
  provider?: { type?: string; entity?: string; capabilities?: string[]; multiInstance?: boolean; hasConfigSchema?: boolean }
  tags?: string[]
}
// Agent runtime readiness — native + each acp:<cli>. `extension` keys it onto
// the matching SettingsProvider card so we render ONE merged agent section.
export interface AgentRuntime {
  name: string; provider_id: string; type: string; extension: string | null
  ready: boolean; state: string; detail: string; login_command: string[] | null
}
// JSON-Schema (Draft-07 + x-meta) describing one provider's user-config fields.
export interface ProviderSchemaProp {
  type?: string; default?: unknown; enum?: string[]; minimum?: number; maximum?: number
  'x-meta'?: { label?: string; help?: string; sensitive?: boolean; placeholder?: string; tags?: string[] }
}
export interface ProviderSchema { type?: string; properties?: Record<string, ProviderSchemaProp>; required?: string[] }
// One configured instance of a multiInstance=true provider (generic store —
// extensions/{name}/instances/{id}.json). Each carries its own config dict.
export interface ProviderInstance { id: string; extension_name: string; display_name: string; config: Record<string, unknown>; enabled: boolean }
export interface ModelProvider { name: string; type: string; model?: string; capabilities: string[]; credential_status: string }
/** An installable model-provider type, from an installed model app's manifest.
 *  ``settingsSchema`` is JSON Schema (+ x-meta) describing the instance config
 *  form (api_key / region / endpoint enum / …). Drives the Add-instance dropdown. */
export interface ModelProviderType {
  type: string
  label: string
  app: string
  capabilities: string[]
  multiInstance: boolean
  settingsSchema: { properties?: Record<string, ModelProviderTypeField>; required?: string[] }
}
export interface ModelProviderTypeField {
  type?: string
  default?: string
  enum?: string[]
  'x-meta'?: { label?: string; help?: string; sensitive?: boolean; tags?: string[] }
}
// Ollama model management (#48). Local = downloaded on the host; search = library candidates.
export interface OllamaLocalModel {
  name: string; size: number; size_human?: string; modified_at?: string
  parameter_size?: string; quantization?: string; family?: string
}
export interface OllamaSearchResult { name: string; description?: string; pulls?: number; tags?: string[] }
export interface OllamaModelInfo {
  model: string; family?: string; parameter_size?: string; quantization?: string
  format?: string; context_length?: number; capabilities?: string[]; license_short?: string; error?: string
}
// A registered Search provider (the Search entity) + its disclosed capabilities,
// the unit you bind to a search use-case in Settings → Search.
export interface SearchCapabilitiesInfo {
  returns_content: boolean; returns_answer: boolean; returns_highlights: boolean
  supports_recency: boolean; supports_domains: boolean; supports_fetch: boolean; depths: string[]
}
export interface SearchProviderInfo { name: string; display_name: string; capabilities: SearchCapabilitiesInfo; available: boolean }

// Per-model capability flags (mirrors local_models/provider.py CapabilityMatrix) — a
// binding UI renders these as chips instead of guessing (LMMV §2.1).
export interface CapabilityMatrix {
  word_timestamps?: boolean; segment_timestamps?: boolean; speaker_labels?: boolean
  acoustic_events?: boolean; hotword_biasing?: boolean; hotword_budget?: number
  languages?: string[]; reasoning_budget_control?: boolean
}
// A model discovered from a configured backend (the unit you bind to a use-case).
// The catalog-contract fields (matrix/license/…, LMMV §2) are optional — only local
// models loaded from a catalog.json carry them; hosted/remote models omit them.
export interface AvailableModel {
  id: string; name: string; capabilities: string[]; provider: string; provider_type: string
  size?: number; downloaded?: boolean; gated?: boolean; description?: string; size_mb?: number; source?: string
  matrix?: CapabilityMatrix | null; license?: string; non_commercial?: boolean
  runtime?: string; runtime_contract?: string; context_tokens?: number; output_tokens?: number
  io_mime?: Record<string, unknown>; status?: string; integrity?: string; config_only?: boolean
}
export interface ProviderModels { name: string; displayName?: string; type: string; models: AvailableModel[]; error?: string; searchable?: boolean; local?: boolean }
export interface ProviderTestResult { ok: boolean; status?: string; message: string }
// A local downloadable model (the uniform LocalModel shape from any local provider).
export interface LocalModel { name: string; id: string; size_mb: number; size: number; description: string; downloaded: boolean; capabilities: string[]; gated: boolean; source: string }
// A background local-model download job — the ONE canonical wire shape
// (matches ModelDownloadJob.to_dict in dashboard/model_downloads.py, LMMV §4.1).
// `progress` is 0.0–1.0 when `total_bytes` is known, else 0.0 (indeterminate);
// `speed_bps`/`eta_s` are coarse poller derivations (0 = not cheaply knowable);
// `reason` is a typed machine label on error/cancel ('cancelled'|'network'|
// 'disk_full'|'gated'|'not_found'), '' otherwise.
export interface DownloadJob {
  id: string; provider: string; model: string
  kind: 'weights' | 'sidecar-install'
  state: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  progress: number; speed_bps: number; eta_s: number
  total_bytes: number; downloaded_bytes: number
  error: string; reason: string
}
export interface ReindexJob {
  id: string; model: string; status: 'running' | 'done' | 'error'
  phase: string; done: number; total: number; knowledge: number; memory: number; error: string
}
export interface DashboardConfig {
  restore_sessions: boolean; restore_window_minutes: number; merge_queued_messages: boolean
  // AI auto-tagging at title-generation time (default on; never touches
  // user-tagged or incognito/temporary sessions)
  auto_tag_sessions: boolean
  widget_density: 'more' | 'less'; user_name: string
  // Attribution handle stamped onto records you create (TEAM-SHARED-ENTITIES §1).
  // A label, not a credential; '' = writes carry no attribution.
  username: string
  // server-stored message display prefs (consistent across browsers)
  send_on_enter: boolean; show_timestamps: boolean; show_thinking_inline: boolean
  simplified_tool_names: boolean; confirm_close_session: boolean
  // Follow-up chips after each reply (default on) + streaming reveal cadence.
  followup_chips: boolean; stream_reveal: 'smooth' | 'immediate'
  // Vestigial server field from the retired customizable-bento dashboard (the
  // grid + per-user layout persistence were dropped in the v2 launcher-forward
  // redesign — everyone gets one curated content-first layout now). No FE
  // consumer reads it; kept only to type the config round-trip until the backend
  // drops the field. Do NOT re-introduce a client layout editor against it.
  dashboard_layout?: { widgets: Array<{ id: string; x: number; y: number; w: number; h: number; hidden?: boolean }>; v: number } | Record<string, never>
}
export interface OnboardingState { needs_model: boolean; has_model_provider: boolean; has_chat_binding: boolean }
export interface ChatModelOption { name: string; model_id: string; provider: string; description?: string }
export interface SavedAgent {
  name: string; provider: string; provider_agent?: string; acp_mode?: string; model?: string; approval_mode?: string
  description?: string; system_prompt?: string; voice?: string; skills?: string[]; tools?: string[]; triggers?: string[]; source?: string; default_dir?: string; memory_store?: string
  // Agent routing (AGENT-ROUTING) — suggest-first specialist routing metadata.
  specialty?: string; route_hints?: string
  reserved?: boolean; editable?: boolean
}


// ── Goal Loop — the unified autonomous goal engine.
export type LoopStatus =
  | 'intake' | 'planning' | 'review' | 'ready' | 'running' | 'paused'
  | 'stagnant' | 'needs_input' | 'complete' | 'failed' | 'stopped'
export type GoalType = 'verifiable' | 'open_ended' | 'monitor'
export type Granularity = 'quick' | 'balanced' | 'exhaustive' | 'forever'
export interface LoopFinding {
  cycle: number; summary?: string; key_insight?: string
  sources_checked?: string[]; sources_empty?: string[]
  files_touched?: string[]
  new_findings_count?: number; evidence?: string; metric?: { name?: string; value?: number }; ts?: number
}
export interface LoopVerdict {
  cycle?: number; done: boolean; done_reason?: string; marginal_value: number; quality_score: number; regressed: boolean
  // P4 observability (optional — present on high-stakes/scored verdicts): whether an
  // adversarial skeptic cross-checked this verdict, and the calibrated returns-band used.
  adversarial?: boolean; band_used?: number
}
export interface LoopNudge { text: string; sent_at: number; sent_at_cycle: number; applied_cycle: number | null }
export interface RosterMember { role: string; persona: string; role_hint?: string; agent_name?: string }
export interface GoalLoop {
  id: string; name: string; goal: string; sub_goals: string[]; deliverables?: string[]; scope?: string[]
  goal_type: GoalType; intake_rigor: string
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  agent: string; model: string; provider?: string; provider_agent?: string; reasoning_effort?: string
  attended: boolean; granularity: Granularity
  max_cycles: number; idle_secs: number
  success_criteria: string | null; verify_command?: string
  rubric?: string[]; best_score?: number; last_score?: number | null; ratchet_mode?: string
  marginal_scores?: number[]
  status: LoopStatus; total_cycles: number; error_message: string | null
  created_at: number; started_at: number | null; completed_at: number | null; elapsed_seconds?: number
  findings?: LoopFinding[]; verdicts?: LoopVerdict[]; pending_question?: string | null; nudges?: LoopNudge[]
  feedback_producer?: FeedbackProducer
  linked_task_ids?: string[]
  // The containing Project this loop scopes under (Projects native entity, S3a).
  // project_id = explicit user scope; tasks_project_id = the auto-provisioned backing
  // project a project-less loop gets at launch (both carried by the unified Loop).
  project_id?: string
  tasks_project_id?: string
  // Planner-authored capabilities + role-phased plan (goal-loop planner/quorum).
  skill_ids?: string[]; workflow_ids?: string[]; execution_plan?: Record<string, unknown>[]
}
export interface LoopClassification {
  title?: string
  goal_type: GoalType; classified?: boolean; intake_rigor: string; rigor_reason?: string
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string; strategy_reason?: string
  clarifying_questions?: string[]; verify_command?: string; success_criteria?: string; sub_goals?: string[]
  deliverables?: string[]
  // Planner-suggested capabilities (IT-3/IT-3b): ids from the installed catalog
  // pre-checked in Plan Review, plus marketplace skills worth installing.
  suggested_skill_ids?: string[]; suggested_workflow_ids?: string[]
  marketplace_suggestions?: SkillSearchResult[]
  // Role-phased plan (IT-6): each phase carries per-phase capabilities.
  execution_plan?: Record<string, unknown>[]
}
export interface LoopValidation {
  can_start: boolean; errors: string[]; warnings: string[]; estimated_cycles?: number; estimated_duration_min?: number
}
// The thorough-rigor intake plan (question tree + resume pointers).
export interface LoopIntakeStep { id: string; title: string; prompt: string; answer: string; status: string; discuss: { role: string; content: string }[] }
export interface LoopIntakePhase { id: string; title: string; description: string; steps: LoopIntakeStep[]; status: string }
export interface LoopIntakePlan { phases: LoopIntakePhase[]; current_phase_id?: string; current_step_id?: string }

// ── Code — the SDLC planning/execution engine (mini-IDE). Sibling of GoalLoop. ──
export type CodeStatus =
  | 'intake' | 'planning' | 'review' | 'ready' | 'running' | 'paused'
  | 'blocked' | 'needs_input' | 'complete' | 'failed' | 'stopped'
  // The unified engine's shared watchdog can stagnate ANY kind (the legacy code engine
  // couldn't) — a code loop reaches 'stalled — needs direction' too, so the code-shaped
  // view-model status must include it (resume/stop/steer all valid).
  | 'stagnant'
export type EntryStage =
  | 'ideation' | 'requirements' | 'design' | 'decomposition' | 'implementation'
  | 'verification' | 'review' | 'bugfix' | 'cr_comments' | 'refactor' | 'investigation'
export type ProjectKind = 'greenfield' | 'brownfield'
// The canonical SDLC ladder — the only stage ids valid in a stage plan (mirrors
// the backend SDLC_STAGES; lateral entries like bugfix are entry stages, not plan
// stages). Used by Plan Review's per-stage type picker.
export const SDLC_STAGES = [
  'ideation', 'requirements', 'design', 'decomposition',
  'implementation', 'verification', 'review',
] as const

// Human label for an SDLC stage / lateral entry id (ideation, cr_comments, …). The
// lateral entries are snake_case ('cr_comments'), so a raw render leaks the
// underscore into the UI — "cr_comments" instead of "CR comments". Special-case the
// acronym, else just de-underscore. Shared so every surface (create, plan review,
// list rows, cockpit) shows the same clean label.
export function sdlcStageLabel(stage: string): string {
  const s = (stage || '').trim()
  if (!s) return ''
  if (s === 'cr_comments') return 'CR comments'
  return s.replace(/_/g, ' ')
}
// One stage in the ordered plan the worker walks; gated by exit_criteria.
export interface CodeStage {
  stage: string; title: string; objective: string; exit_criteria: string[]
  deliverable: string; task_list_name: string; agent_name?: string
  skill_ids?: string[]; workflow_ids?: string[]
  // P6 tick-engine quality gate (optional, per-stage). When metric_pass is set, the
  // supervisor's third-party judge must score the stage's work ≥ metric_pass (0-5)
  // before it advances; a score in [metric_hold, metric_pass) HOLDs for another cycle;
  // below the prior stage's bar rolls back. Planner-seeded for verification/review
  // (defaults 3.5 / 2.0) and editable here so the user tunes the quality bar per stage.
  // min_findings/min_dwell_secs are the evidence/bake floors (rarely tuned by hand).
  metric_pass?: number; metric_hold?: number; min_findings?: number; min_dwell_secs?: number
  // The planner's upfront per-stage task checklist, seeded into the stage's
  // TaskList at launch. action_plan / exit_criteria / depends_on are planner-authored
  // (see the backend _normalize_tasks) and must survive the Plan-Review round-trip —
  // modelled here so an edit can't silently drop them. The Plan Review edits title +
  // description; the richer fields pass through untouched.
  tasks?: { title: string; description?: string; action_plan?: string[]; exit_criteria?: string[]; depends_on?: number[] }[]
}
export interface CodeFinding {
  cycle: number; summary?: string; key_insight?: string; stage?: string
  // Present on parallel task-worker findings — ties the cycle to its task so the
  // cockpit can nest agent-execution detail under the right task card.
  task_id?: string
  // A string, or a dict/array of named checks (e.g. {py_compile: "…"}) — the
  // cockpit normalizes any shape for display (see evidenceToText).
  evidence?: unknown; ts?: number
  // Files the worker touched this cycle — absolute paths, or bare relative paths
  // (no-workspace/sequential mode) resolved against the file root. Surfaced as
  // clickable chips in the cockpit so the user can jump from "what changed" to it.
  files_touched?: string[]
}
export interface CodeProject {
  id: string; name: string; task: string; summary?: string
  entry_stage: EntryStage; project_kind: ProjectKind; intake_rigor: string
  stage_plan: CodeStage[]; stage_status?: Record<string, string>
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  agent: string; model: string; provider?: string; provider_agent?: string; reasoning_effort?: string
  skill_ids?: string[]; workflow_ids?: string[]
  workspace_dir?: string; attended: boolean; autopilot?: boolean
  // The project's own file dir (server-local), where doc deliverables land when no
  // workspace is bound; the cockpit roots its file surfaces here as a fallback.
  files_dir?: string
  max_cycles: number; idle_secs: number
  success_criteria: string | null; verify_command?: string; test_command?: string
  status: CodeStatus; total_cycles: number; error_message: string | null
  created_at: number; started_at: number | null; completed_at: number | null; elapsed_seconds?: number
  project_id?: string; tasks_project_id?: string; task_list_ids?: Record<string, string>; session_key?: string
  findings?: CodeFinding[]; pending_question?: { question: string; why?: string } | null
  // Durable steer history (oldest first); applied_cycle stamps which cycle it took effect.
  nudges?: { text: string; sent_at?: number; sent_at_cycle?: number; applied_cycle?: number | null }[]
  // Task ids the user queued for execution (task-driven model); run once ready.
  queued_task_ids?: string[]
}
export interface CodeClassification {
  title?: string; summary?: string; classified?: boolean
  entry_stage: EntryStage; entry_reason?: string; project_kind: ProjectKind
  intake_rigor: string; rigor_reason?: string
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  clarifying_questions?: string[]; verify_command?: string; test_command?: string
  success_criteria?: string; stage_plan: CodeStage[]
  suggested_skill_ids?: string[]; suggested_workflow_ids?: string[]
  marketplace_suggestions?: SkillSearchResult[]
}

// ── Unified Loop — the ONE primitive (kinds: general/goal/code/design) the goal +
// code engines fold into. Defined additively alongside GoalLoop/CodeProject; the
// cockpits/composers migrate onto it in 2d(iii), then the legacy types retire at the
// 2e cutover. Mirrors the backend loop/loop.py entity + loop_routes.py redacted view:
// shared spine fields at top level, everything kind-specific in `kind_config`.
export type LoopKind = 'general' | 'goal' | 'code' | 'design' | 'research'
// The union of every kind's lifecycle states (goal adds `stagnant`; code adds `blocked`).
export type UnifiedLoopStatus =
  | 'intake' | 'planning' | 'review' | 'ready' | 'running' | 'paused'
  | 'stagnant' | 'blocked' | 'needs_input' | 'complete' | 'failed' | 'stopped'
// One phase in the kind-agnostic plan: goal sub-goals (keyed by title), code SDLC
// stages (keyed by stage), design steps. Only `title` is universal; the rest are
// kind-specific and pass through untouched.
export interface LoopPhase {
  title?: string; stage?: string; objective?: string; exit_criteria?: string[]
  deliverable?: string; tasks?: Record<string, unknown>[]
  [k: string]: unknown
}
export interface Loop {
  id: string; kind: LoopKind; name: string; task: string; summary?: string
  intake_rigor?: string
  plan?: LoopPhase[]; phase_status?: Record<string, string>
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  strategy_config?: Record<string, unknown>
  agent: string; model: string; provider?: string; provider_agent?: string; reasoning_effort?: string
  skill_ids?: string[]; workflow_ids?: string[]
  workspace_dir?: string; attended: boolean; autopilot?: boolean
  // The loop's own server-local file dir — where brief/findings live and doc
  // deliverables (REPORT.md/MONITOR_LOG.md) land when no workspace is bound. The
  // cockpit roots its file tree + terminal here for no-workspace loops.
  files_dir?: string
  max_cycles: number; idle_secs: number
  success_criteria: string | null
  status: UnifiedLoopStatus; total_cycles: number; error_message: string | null
  created_at: number; started_at: number | null; completed_at: number | null; elapsed_seconds?: number
  project_id?: string
  tasks_project_id?: string; task_list_ids?: Record<string, string>; linked_task_ids?: string[]; session_key?: string
  // Attached by the redacted view (detail) — empty for kinds that don't produce them.
  // A finding is goal-shaped OR code-shaped (union, not intersection — they have
  // conflicting `evidence` types: goal string vs code unknown), keyed by loop.kind.
  findings?: (LoopFinding | CodeFinding)[]; verdicts?: LoopVerdict[]; marginal_scores?: number[]
  nudges?: LoopNudge[]; pending_question?: { question: string; why?: string } | string | null
  // Feedback Signal (plan 58): the producer the finding thumbs attribute to
  // (("loop_judge", kind) — per-kind, each kind carries its own brief/rubric).
  feedback_producer?: FeedbackProducer
  // Everything kind-specific. goal: {goal_type, granularity, sub_goals, deliverables,
  // rubric, ratchet_mode, verify_command, execution_plan}. code: {entry_stage,
  // project_kind, verify_command, test_command, queued_task_ids}. design:
  // {token_overrides, targets, exports}. general: {verify_command}.
  kind_config: Record<string, unknown>
}
// The normalized classify result the kind-aware /api/loops/classify returns — the
// composer/Plan-Review consumes it + the create body can fold it back in (the whole
// kind_config round-trips).
export interface UnifiedLoopClassification {
  kind: LoopKind; title?: string; summary?: string; classified?: boolean
  intake_rigor?: string; execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  // The planner's rationale for its picks, surfaced on Plan Review (e.g. RigorChip
  // tooltip). entry_reason is code-only; rigor/strategy_reason are common.
  rigor_reason?: string; strategy_reason?: string; entry_reason?: string
  clarifying_questions?: string[]; suggested_skill_ids?: string[]; suggested_workflow_ids?: string[]
  marketplace_suggestions?: SkillSearchResult[]; success_criteria?: string
  plan?: LoopPhase[]; kind_config: Record<string, unknown>
}

// Guided decomposition (#16, grill's `tree` shape) — the richer intake behind
// `intake_rigor='thorough'`: 2-4 phases of clarifying questions that build on one
// another, memory-checked so the agent doesn't re-ask what it already knows about
// you. Returned by POST /api/loops/{id}/grill-tree; the FE walks the phases + folds
// the answers into the task at launch (persisted in kind_config.grill_phases).
// A phase step is `{title, prompt}` from the grill `tree` normalizer today; the OPTIONAL typed
// fields mirror `workflows/grill_protocol.Question` (kind | choices | recommended | required) so
// the QuestionSlider stepper renders the richer deep-rigor Round with no shim when a planner emits
// it. Absent fields default to a required freeform text question (the tree shape's behavior).
export interface GrillPhaseStep {
  title: string; prompt: string
  kind?: 'text' | 'choice' | 'slider' | 'boundary'
  choices?: string[]; recommended?: string; required?: boolean
  min?: number; max?: number; step?: number
}
export interface GrillPhase { title: string; description: string; steps: GrillPhaseStep[] }
export interface GrillTreeResult { phases: GrillPhase[]; memory_hits: number }

// The stepwise SDLC planning walkthrough — an ordered list of steps the planner
// designs for the target; each produces an artifact the user approves or comments on.
export type PlanStepStatus = 'pending' | 'running' | 'awaiting_review' | 'approved'
export interface PlanStep {
  id: string; kind: string; title: string; objective?: string
  status: PlanStepStatus
  artifact?: Record<string, unknown>
  comments?: { text: string; at: number }[]
}
export interface PlanSession {
  project_id: string; created_at: number; steps: PlanStep[]
  // Set when a design pass ran but produced no usable steps — the walkthrough shows
  // a failed state + explicit Retry instead of silently re-spawning a fresh pass.
  design_error?: string
}

export type ApprovalMode = 'normal' | 'trust' | 'trust_reads' | 'yolo'
// Task mode — orthogonal to approval: gates WHICH tools run + how the agent frames
// the work (Plan moved here from the approval enum). See /api/chat/task-mode.
export type TaskMode = 'agent' | 'ask' | 'plan' | 'build'
export type ReasoningEffort = '' | 'low' | 'medium' | 'high' | 'max'
export type MemoryMode = 'persistent' | 'incognito' | 'temporary'

export interface NudgeLoop {
  id: string; session_name: string; message: string; idle_secs: number
  max_cycles: number; cycle_count: number; active: boolean
  last_fire_ts: number; created_ts: number
}

// ── files + artifacts ──
export interface FsEntry { name: string; path: string; is_dir: boolean; size?: number; mtime?: number }
export interface FsRoot { label: string; path: string; name: string; is_dir: boolean }
export interface FileListResp { roots: FsRoot[]; entries: FsEntry[]; path: string }
export interface GitStatusResp { repoRoot: string; branch: string; statuses: Record<string, string> }
export interface ContentMatch { file: string; line: number; col: number; preview: string }
export interface ContentSearchResp { results: ContentMatch[]; engine: 'rg' | 'python'; truncated: boolean }

// Mirrors the backend's ALLOWED_KINDS (artifacts/models.py). The office/PDF kinds and
// `video` were missing here, so `artifactKindMeta` fell through to its first entry and
// every generated .docx/.xlsx/.pptx/.pdf/.csv/video displayed as a "Widget".
export type ArtifactKind = 'widget' | 'html' | 'react' | 'markdown' | 'svg' | 'json' | 'text' | 'infographic' | 'document' | 'image' | 'csv' | 'docx' | 'xlsx' | 'pptx' | 'pdf' | 'video'
export type ArtifactSource = 'chat' | 'cron' | 'subagent' | 'manual' | 'import'
// ── Dashboard-as-views registry (AMBIENT-SURFACES §1 / A2-1) ──
// A view is ordered tile REFS + size hints — NEVER coordinates (the retired grid's
// lesson). A tile ref is `core:<widget>` (a hard-imported first-party widget) or
// `artifact:<slug>` (a pinned artifact tile). `added_by:agent` rows are PROPOSALS
// that render with an accept/dismiss chip.
export type TileSize = 's' | 'm' | 'l' | 'full'
export interface DashboardTile { ref: string; size: TileSize; order: number; added_by: 'user' | 'agent' }
export interface DashboardView { id: string; name: string; icon?: string | null; nav_pinned: boolean; preset: boolean; tiles: DashboardTile[] }

export type ArtifactEventType = 'created' | 'edited' | 'iterated' | 'referenced' | 'reverted'
export interface ArtifactEvent {
  ts: string; type: ArtifactEventType; by: string; session_id: string
  version: number; from_version: number; metadata: Record<string, unknown>
}
export interface Artifact {
  slug: string; name: string; kind: ArtifactKind; source: ArtifactSource
  description: string; tags: string[]; version: number
  created_at: string; updated_at: string
  content?: string | null; events: ArtifactEvent[]
  source_path: string; live_dirty: boolean; project_id?: string
  // Optional library collection label (ARTIFACTS S1). "" = uncollected.
  collection?: string
  /** Frozen record: the server refuses every content mutation on it (SM-9 — today only
   *  shared chat transcripts). Read here so the UI stops OFFERING an edit rather than
   *  letting the user type into an editor whose save always 400s. */
  readonly: boolean
}

// One usage-ledger aggregate (COST-AND-TOKEN-OBSERVABILITY). `priced` is false when
// the group/window mixes a model with no price row → the cost is a partial, render
// "unpriced"/a partial marker, never a confidently-complete figure.
export interface UsageAgg {
  input_tokens: number; output_tokens: number
  cache_read_tokens: number; cache_creation_tokens: number
  cost_usd: number; turns: number; priced: boolean
}

/** One per-model efficiency row for a (use_case, query_class) bucket
 *  (MODEL-ROUTING-TELEMETRY, MRT-1d/1e). Observation only — the fold supplies
 *  n/success/feedback/cost, the audit tail supplies p50/p95 latency, and
 *  `on_frontier` = this ref is not dominated by another on (success↑, p50_ms↓,
 *  avg_cost_usd↓). `feedback`/latency are 0 when no signal has landed yet. */
export interface TelemetryRow {
  ref: string
  n: number
  success: number
  feedback: number
  avg_cost_usd: number
  p50_ms: number
  p95_ms: number
  on_frontier: boolean
}

/** Build the ?since=&until=&session=&group_by= query for the usage endpoints
 *  (empty/absent params omitted). */
function _usageQuery(opts?: { since?: string; until?: string; session?: string; group_by?: string }): string {
  const p = new URLSearchParams()
  if (opts?.group_by) p.set('group_by', opts.group_by)
  if (opts?.since) p.set('since', opts.since)
  if (opts?.until) p.set('until', opts.until)
  if (opts?.session) p.set('session', opts.session)
  const q = p.toString()
  return q ? `?${q}` : ''
}

// One installed pack's ledger record (AGENT-PACKS §9). `connector_markers` holds the
// `connector_missing:<name>` codes for skipped connectors; `setup_pending` gates the
// re-runnable "Finish setup" chip.
export interface InstalledPackRec {
  name: string
  version: string
  components: string[]
  connectors: Array<{ name: string; mode: string; server_name: string; marker: string; credentials_saved: string[]; error: string }>
  connector_markers: string[]
  setup_skill: string
  setup_pending: boolean
  installed_at: string
}

export const api = {
  // agents & providers
  agentsInstalled: () => get<AgentDef[]>('/api/agents/installed'),
  // saved agent definitions (what goal loops validate their worker agent against)
  savedAgents: () => get<{ agents: Array<{ name: string; description?: string; model?: string }> }>('/api/agents').then((d) => d.agents),
  // full native-agent CRUD (the Agents builder): returns the complete profiles + default
  agents: () => get<{ agents: SavedAgent[]; default_agent: string }>('/api/agents'),
  createAgent: (body: Record<string, unknown>) => post<{ ok: boolean }>('/api/agents', body),
  updateAgent: (name: string, body: Record<string, unknown>) => put<{ ok: boolean }>(`/api/agents/${encodeURIComponent(name)}`, body),
  deleteAgent: (name: string) => del(`/api/agents/${encodeURIComponent(name)}`),
  setDefaultAgent: (name: string) => put<{ ok: boolean; default_agent: string }>('/api/config/default-agent', { agent: name }),
  // Agent routing (AGENT-ROUTING) — suggestion-suppression endpoints. The suggestion
  // itself arrives as a `routing_suggestion` WS push; these manage dismiss/mute state.
  routingDismiss: (agent: string) => post<{ ok: boolean; count: number; muted: boolean }>('/api/agents/routing/dismiss', { agent }),
  routingUnmute: (agent: string) => post<{ ok: boolean }>('/api/agents/routing/unmute', { agent }),
  routingStatus: () => get<{ enabled: boolean; muted: string[]; dismissals: Record<string, { count: number; last_dismissed_at: number }> }>('/api/agents/routing/status'),
  // Cost/token usage (COST-AND-TOKEN-OBSERVABILITY). `session` scopes to one chat
  // (the header chip); `since`/`until` bound a period (the Usage panel's Today/7d/30d).
  // `priced=false` ⇒ the window mixes a model with no price row, so the total is a
  // partial (render "unpriced" / a partial marker — never a confidently-complete $).
  usageTotals: (opts?: { session?: string; since?: string; until?: string }) => get<{ session: string; totals: UsageAgg }>(`/api/usage/totals${_usageQuery(opts)}`),
  usageRollup: (opts?: { group_by?: 'model' | 'source' | 'agent' | 'provider' | 'day'; since?: string; until?: string; session?: string }) => get<{ group_by: string; rows: Array<UsageAgg & Record<string, string>> }>(`/api/usage/rollup${_usageQuery(opts)}`),
  // Per-model routing efficiency for one (use_case, query_class) bucket
  // (MODEL-ROUTING-TELEMETRY, MRT-1d). BOTH params are required (a missing either
  // is a 400); `rows` may be empty for a bucket with no telemetry yet. Read-only —
  // this only visualizes; nothing here changes routing. The Routing & Efficiency
  // settings panel (MRT-1e) renders it.
  modelsTelemetry: (opts: { use_case: string; query_class: string }) =>
    get<{ use_case: string; query_class: string; rows: TelemetryRow[] }>(
      `/api/models/telemetry?use_case=${encodeURIComponent(opts.use_case)}&query_class=${encodeURIComponent(opts.query_class)}`,
    ),
  // full backend config (read the `agent` subtree for Agent defaults) + the
  // single-field PATCH (allowlisted dotted paths — see _EDITABLE_CONFIG).
  personalclawConfig: () => get<Record<string, any>>('/api/config/personalclaw'),
  patchConfig: (path: string, value: unknown) => patch<Record<string, any>>('/api/config/personalclaw', { path, value }),

  // ── Packs (AGENT-PACKS §3.4/§9, AP-3) ──
  // The installed-pack ledger (each pack's components, connector resolutions +
  // `connector_missing:<name>` markers, and whether a re-runnable setup interview is
  // pending) and the "Finish setup" chip backend (returns the setup skill's slash-command;
  // the interview runs in chat under normal tool approval — never server-side).
  packsInstalled: () => get<{ packs: InstalledPackRec[] }>('/api/packs/installed').then((d) => d.packs),
  packFinishSetup: (name: string) => post<{ pack: string; setup_skill: string; command: string; pending: boolean }>(`/api/packs/${encodeURIComponent(name)}/finish-setup`, {}),

  // ── Owner login (REMOTE-USER-AUTH C3/C5) ──
  // The credential itself is never READ back — `authSession` reports only whether one is
  // configured, for whom, and whether 2FA is on. Setting a password goes through its own
  // POST rather than patchConfig, because a password is not a config field: the PATCH
  // allowlist deliberately refuses anything password-shaped.
  authSession: () => get<{
    login_enabled: boolean
    credential_configured: boolean
    username: string
    totp_enabled: boolean
    totp_required: boolean
    session_ttl: string
    lockout_threshold: number
    lockout_window: string
    user: string
  }>('/api/auth/session'),
  setLoginPassword: (username: string, password: string) =>
    post<{ ok: boolean; username: string }>('/api/auth/password', { username, password }),
  authLogout: () => post<{ ok: boolean; revoked: boolean }>('/api/auth/logout'),

  // ── Guardrails: incident kill switch + derived provider health (§1.3, §2.5) ──
  incident: () => get<{ active: boolean; reason: string; started_at: string }>('/api/incident'),
  incidentOn: (reason: string) =>
    post<{ active: boolean; reason: string; started_at: string }>('/api/incident', { reason }),
  incidentResume: () => post<{ active: boolean }>('/api/incident/resume', { confirm: true }),
  modelsHealth: () =>
    get<{ providers: ProviderHealth[]; generated_from: number }>('/api/models/health'),

  // ── The earned-autonomy ladder (§5-§6.1). Read, then three writes — and only `grant`
  //    increases what an automation may do on its own, which is why it is the only one
  //    that can be refused (400 with the reason).
  autonomyLadder: () => get<AutonomyLadder>('/api/autonomy'),
  autonomyGrant: (key: string, rung: string) =>
    post<{ ok: boolean; key: string; rung: string; evidence: string }>('/api/autonomy/grant', { key, rung }),
  autonomyDemote: (key: string) =>
    post<{ ok: boolean; key: string; cooldown_until: string }>('/api/autonomy/demote', { key }),
  /** Reverse one automatic action by RECORD id (never a raw handle) — and demote its type. */
  autonomyUndo: (id: string) =>
    post<{ ok: boolean; code: string; action_type: string; demoted: boolean; detail?: string }>('/api/autonomy/undo', { id }),

  // ── Doctor: tiered read-only health probes (PLATFORM-RESILIENCE §1) ──
  doctor: () => get<DoctorReport>('/api/doctor'),
  doctorCapability: (capability: string) =>
    get<{ capability: string; ok: boolean; probes: DoctorProbe[]; unknown?: boolean }>(
      `/api/doctor/${encodeURIComponent(capability)}`,
    ),
  // ── No-model degraded mode (PLATFORM-RESILIENCE §5) ──
  degraded: () => get<DegradedReport>('/api/resilience/degraded'),
  // ── Scheduled backups (DURABILITY-AND-SYNC §3) ──
  durabilityStatus: () => get<DurabilityStatus>('/api/durability/status'),
  durabilitySnapshots: () => get<DurabilitySnapshots>('/api/durability/snapshots'),
  durabilityRun: (job: 'export' | 'snapshot' | 'drill') =>
    post<DurabilityJobResult>('/api/durability/run', { job }),
  // ── Confirm-gated fixes + surfacing simulator (PLATFORM-RESILIENCE §2/§3.1) ──
  doctorFixes: () => get<{ fixes: DoctorFix[] }>('/api/doctor/fixes'),
  doctorFixApply: (fixId: string) =>
    post<{ ok: boolean; fix_id: string; result?: string; error?: string }>(
      `/api/doctor/fix/${encodeURIComponent(fixId)}`, { confirm: true },
    ),
  doctorSimulateSurfacing: (text: string) =>
    post<{ query: string; candidates: SurfacingCandidate[] }>(
      '/api/doctor/simulate/surfacing', { text },
    ),
  doctorCrash: (filename: string) =>
    get<Record<string, unknown>>(`/api/doctor/crash/${encodeURIComponent(filename)}`),
  // ── Remediation engine (PLATFORM-RESILIENCE §4) ──
  doctorRemediation: () => get<RemediationSnapshot>('/api/doctor/remediation'),
  doctorRemediationRun: () =>
    post<{ score_before: number; score_after: number; jobs: RemediationJobRow[]; stopped_reason: string }>(
      '/api/doctor/remediation/run', { confirm: true },
    ),

  // ── Memory Studio: health, observability, deep recall, promotion, lessons ──
  memoryGraph: () => get<MemoryGraphData>('/api/memory/graph'),
  memoryLint: () => get<MemoryLint>('/api/memory/lint'),
  memoryObservability: () => get<MemoryObservability>('/api/memory/observability'),
  memoryRecall: (q: string) => get<{ result: string; query: string; deep: boolean }>(`/api/memory/recall?q=${encodeURIComponent(q)}`),
  memoryPromote: () => post<{ ok: boolean; promoted: number }>('/api/memory/promote'),
  // Entity graph (MEMORY-GRAPH-AND-VAULT §1) — the typed links under recall.
  memoryEntities: () => get<MemoryEntitiesResponse>('/api/memory/entities'),
  memoryEntityCreate: (body: { name: string; entity_type: MemoryEntityType; aliases?: string[] }) =>
    post<{ ok: boolean; id: string }>('/api/memory/entities', body),
  memoryEntityBacklinks: (id: string) =>
    get<{ links: MemoryLink[] }>(`/api/memory/entities/${encodeURIComponent(id)}/backlinks`),
  memoryEntityProposal: (body: { name: string; action: 'accept' | 'reject'; entity_type?: MemoryEntityType }) =>
    post<{ ok: boolean; id?: string }>('/api/memory/entities/proposals', body),
  memoryGraphRebuild: () => post<MemoryGraphRebuild>('/api/memory/graph/rebuild'),
  // Raw markdown memory files (preferences / projects / history) — GET+PUT {content}.
  memoryDoc: (which: 'preferences' | 'projects' | 'history') => get<{ content: string }>(`/api/memory/${which}`).then((d) => d.content),
  saveMemoryDoc: (which: 'preferences' | 'projects' | 'history', content: string) => put<{ ok: boolean }>(`/api/memory/${which}`, { content }),
  // Legacy-markdown → vector-store migration + JSON import (maintenance flows).
  memoryMigrate: () => post<Record<string, number>>('/api/memory/migrate'),
  memoryImport: (data: unknown) => post<Record<string, number>>('/api/memory/import', data),
  lessons: () => get<{ lessons: Lesson[] }>('/api/lessons').then((d) => d.lessons),
  addLesson: (rule: string, category = 'knowledge') => post<{ ok: boolean }>('/api/lessons', { rule, category }),
  deleteLesson: (rule: string) => fetch('/api/lessons', { method: 'DELETE', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ rule }) }).then(j<{ ok: boolean }>),

  // ── Full-text conversation search (over persisted JSONL content) ──
  // `snippet` carries the matching passage with `<<`/`>>` around the matched terms
  // (present on FTS-index hits; absent when the linear-scan fallback answered).
  sessionsSearch: (q: string) => get<{ sessions: Array<{ key: string; title?: string; messages?: number; snippet?: string }>; source?: string }>(`/api/sessions/search?q=${encodeURIComponent(q)}`).then((d) => d.sessions),

  // ── Background subagents monitor (spawned by crons / loops / Slack) ──
  spawnedAgents: () => get<{ agents: SpawnedAgent[] }>('/api/spawn').then((d) => d.agents),
  cancelSpawnedAgent: (id: string) => del(`/api/spawn/${encodeURIComponent(id)}`),
  clearSpawnedAgents: () => del('/api/spawn'),
  // Kill EVERY child of one parent/run in one click (WF2WOR-8 C1.4).
  cancelFanout: (parentSession: string) =>
    post<{ ok: boolean; cancelled: number }>('/api/spawn/cancel-fanout', { parent_session: parentSession }),

  // ── Knowledge context search (token-budgeted cards for the composer picker) ──
  knowledgeSearchForContext: (q: string, maxTokens = 4000) =>
    get<KnowledgeContextResult>(`/api/knowledge/search-for-context?q=${encodeURIComponent(q)}&max_tokens=${maxTokens}`),

  // ── Agent advanced config (routing notes, per-agent MCP, lifecycle hooks) ──
  /** Routing notes ("when to use this agent") — feeds the orchestrator/auto-router. */
  agentMetadata: (name: string) => get<{ name: string; content: string }>(`/api/agent-metadata/${encodeURIComponent(name)}`).then((d) => d.content),
  saveAgentMetadata: (name: string, content: string) => put<{ ok: boolean }>(`/api/agent-metadata/${encodeURIComponent(name)}`, { content }),
  /** The MCP servers an agent gets (name + enabled). Omit agent for the default set. */
  mcpActive: (agent?: string) => get<McpActiveServer[]>(`/api/mcp/active${agent ? `?agent=${encodeURIComponent(agent)}` : ''}`),
  /** Read-only view of the lifecycle hooks in effect (redacted commands). */
  agentHooks: () => get<{ hooks: Record<string, AgentHook[]> }>('/api/agent-hooks').then((d) => d.hooks),
  /** Reconcile native agent configs on disk (rewrites installed copies). */
  syncAgents: () => post<{ ok: boolean; synced?: number }>('/api/agents/sync'),

  // ── Channels runtime (live connection health + connect/disconnect/test) ──
  channels: () => get<{ channels: ChannelRuntime[] }>('/api/channels').then((d) => d.channels),
  connectChannel: (name: string) => post<{ ok: boolean; health?: ChannelHealth }>(`/api/channels/${encodeURIComponent(name)}/connect`),
  disconnectChannel: (name: string) => post<{ ok: boolean }>(`/api/channels/${encodeURIComponent(name)}/disconnect`),
  testChannel: (name: string) => post<{ ok: boolean; health?: ChannelHealth; detail?: string }>(`/api/channels/${encodeURIComponent(name)}/test`),

  // ── Tasks bulk ops (validate-all-then-apply create/update/delete) ──
  tasksBulk: (op: 'create' | 'update' | 'delete', items: Array<Record<string, unknown>>) =>
    post<{ total: number; succeeded: number; failed: number; results?: unknown[]; errors?: unknown[] }>('/api/tasks/bulk', { op, items }),


  // ── Chat turn-level controls ──
  /** Silently prime the next turn with background context (no visible message, no turn). */
  briefSession: (key: string, content: string, source = 'user-brief') =>
    post<{ ok: boolean }>(`/api/chat/sessions/${encodeURIComponent(key)}/context`, { content, source, ephemeral: false }),
  /** Set a live session's working directory (agent cwd + memory-partition scope). */
  setSessionWorkspaceDir: (key: string, workspace_dir: string) =>
    post<{ ok: boolean; workspace_dir?: string }>(`/api/chat/sessions/${encodeURIComponent(key)}/workspace-dir`, { workspace_dir }),

  // ── Contextual prompt starters (background-computed from memory + recent activity) ──
  suggestions: (force = false) => get<{ suggestions: string[]; generated_at: number; stale: boolean }>(`/api/suggestions${force ? '?force=1' : ''}`),

  // ── Discover (§6): a curated tour of the system, grouped by area. Tips only
  // point (deep link), never enable; dismissals persist server-side per tip. ──
  discover: () => get<DiscoverResponse>('/api/legibility/discover'),
  dismissDiscoverTip: (id: string) => post<{ ok: boolean; dismissed: string[] }>('/api/legibility/discover/dismiss', { id }),

  // ── Desktop integration (OS-gated; server runs the subprocess) ──
  /** Reveal a path in Finder (action 'reveal') or open with the default app ('open'). */
  revealPath: (path: string, action: 'reveal' | 'open' = 'reveal') =>
    post<{ ok: boolean; copy?: string }>('/api/reveal', { path, action }),
  /** Interactive region screen capture (macOS). Returns the saved PNG path, or '' if cancelled. */
  screenshot: () => post<{ path: string; error?: string }>('/api/screenshot'),

  // ── Diagnostics: live backend log stream + runtime log level ──
  /** SSE URL for the live log tail; `lines` replays that many ring-buffer entries on connect. */
  logsUrl: (lines = 200) => `/api/logs?lines=${encodeURIComponent(String(lines))}`,
  logLevel: () => get<{ level: string }>('/api/logs/level').then((d) => d.level),
  setLogLevel: (level: string) => post<{ ok: boolean; level: string; persisted: boolean }>('/api/logs/level', { level }),

  // ── Custom themes (server-persisted, shareable color identities) ──
  themes: () => get<{ themes: ThemeSummary[] }>('/api/themes').then((d) => d.themes),
  theme: (slug: string) => get<ThemeRecord>(`/api/themes/${encodeURIComponent(slug)}`),
  createTheme: (body: ThemeWrite) => post<{ ok: boolean; slug: string; theme: ThemeRecord }>('/api/themes', body),
  updateTheme: (slug: string, body: ThemeWrite) => put<{ ok: boolean; theme: ThemeRecord }>(`/api/themes/${encodeURIComponent(slug)}`, body),
  deleteTheme: (slug: string) => del(`/api/themes/${encodeURIComponent(slug)}`),
  agentProviders: () => get<{ agent_providers: AgentProvider[] }>('/api/agent-providers').then((d) => d.agent_providers),
  agentProviderAgents: (id: string, refresh = false) =>
    get<{ agents: DiscoveredAgent[]; permission_modes: string[] }>(`/api/agent-providers/${encodeURIComponent(id)}/agents${refresh ? '?refresh=1' : ''}`),

  // models
  // The one chat-model list (active selection, or all chat-capable on fallback).
  // Entries carry both model_name (composer pill) and model_id (pickers).
  models: () => get<ModelItem[]>('/api/models/chat'),
  settingsProviders: () => get<{ providers: SettingsProvider[] }>('/api/providers').then((d) => d.providers),
  // per-extension config: schema (for the dynamic form) + current values + save.
  providerSchema: (name: string) => get<{ schema: ProviderSchema }>(`/api/providers/${encodeURIComponent(name)}/schema`).then((d) => d.schema),
  providerConfig: (name: string) => get<{ config: Record<string, unknown> }>(`/api/providers/${encodeURIComponent(name)}/config`).then((d) => d.config),
  saveProviderConfig: (name: string, config: Record<string, unknown>) =>
    patch<{ config: Record<string, unknown> }>(`/api/providers/${encodeURIComponent(name)}/config`, config),
  enableProvider: (name: string) => post<{ enabled: boolean }>(`/api/providers/${encodeURIComponent(name)}/enable`),
  disableProvider: (name: string) => post<{ enabled: boolean }>(`/api/providers/${encodeURIComponent(name)}/disable`),
  // agent runtimes (native + acp:<cli>) with readiness — merged onto agent cards.
  // refresh=true forces a fresh readiness probe (post-sign-in / manual re-check),
  // bypassing the 5-minute readiness cache.
  agentRuntimes: (refresh = false) => get<{ agent_providers: AgentRuntime[] }>(`/api/agent-providers${refresh ? '?refresh=1' : ''}`).then((d) => d.agent_providers),
  // generic multi-instance CRUD (any multiInstance=true provider — MCP/OpenAI tools, …).
  providerInstances: (name: string) => get<{ instances: ProviderInstance[] }>(`/api/providers/${encodeURIComponent(name)}/instances`).then((d) => d.instances),
  createProviderInstance: (name: string, body: { display_name: string; config: Record<string, unknown> }) =>
    post<{ instance: ProviderInstance }>(`/api/providers/${encodeURIComponent(name)}/instances`, body),
  updateProviderInstance: (name: string, id: string, body: { display_name?: string; config?: Record<string, unknown>; enabled?: boolean }) =>
    put<{ instance: ProviderInstance }>(`/api/providers/${encodeURIComponent(name)}/instances/${encodeURIComponent(id)}`, body),
  deleteProviderInstance: (name: string, id: string) => del(`/api/providers/${encodeURIComponent(name)}/instances/${encodeURIComponent(id)}`),
  testProviderInstance: (name: string, id: string) => post<ProviderTestResult>(`/api/providers/${encodeURIComponent(name)}/instances/${encodeURIComponent(id)}/test`),
  // model BACKENDS (config-file instances): list + full CRUD + connectivity test.
  modelProviders: () => get<{ providers: ModelProvider[] }>('/api/model-providers').then((d) => d.providers),
  // Installable model-provider types — EXACTLY the model apps currently installed
  // (drives the Add-instance dropdown). No hardcoded type list; a type not backed
  // by an installed app never appears.
  modelProviderTypes: () => get<{ types: ModelProviderType[] }>('/api/model-provider-types').then((d) => d.types),
  createModelProvider: (body: { name: string; type: string; model?: string; options?: Record<string, string> }) =>
    post<{ ok: boolean; name: string }>('/api/model-providers', body),
  updateModelProvider: (name: string, body: { model?: string; type?: string; options?: Record<string, string> }) =>
    put<{ ok: boolean }>(`/api/model-providers/${encodeURIComponent(name)}`, body),
  deleteModelProvider: (name: string) => del(`/api/model-providers/${encodeURIComponent(name)}`),
  testModelProvider: (name: string) => post<ProviderTestResult>(`/api/model-providers/${encodeURIComponent(name)}/test`),
  // discovered models across all backends + the active-per-use-case bindings.
  modelsAvailable: () => get<{ providers: ProviderModels[] }>('/api/models/available').then((d) => d.providers),
  modelsActive: () => get<{ use_cases: Record<string, string[]> }>('/api/models/active').then((d) => d.use_cases),
  // ── Search entity (Settings → Search): registered providers + use-case bindings ──
  searchProviders: () => get<{ providers: SearchProviderInfo[] }>('/api/search/providers').then((d) => d.providers),
  searchActive: () => get<{ use_cases: Record<string, string[]> }>('/api/search/active').then((d) => d.use_cases),
  setActiveSearchProvider: (useCase: string, providers: string[]) => put<{ ok?: boolean }>(`/api/search/active/${encodeURIComponent(useCase)}`, { providers }),
  // ── Ollama model management (first-class provider card, #48) ──
  // Downloaded models on the provider's Ollama host (size + metadata).
  ollamaModels: (provider: string) =>
    get<{ models: OllamaLocalModel[]; error?: string }>(`/api/model-providers/${encodeURIComponent(provider)}/models`),
  // Search the Ollama library (library:tag candidates to pull).
  ollamaSearch: (provider: string, q: string) =>
    get<{ results: OllamaSearchResult[]; error?: string }>(`/api/model-providers/${encodeURIComponent(provider)}/search?q=${encodeURIComponent(q)}`),
  // Per-model metadata (family/params/quant/context) for an informed choice.
  ollamaShow: (provider: string, model: string) =>
    get<OllamaModelInfo>(`/api/model-providers/${encodeURIComponent(provider)}/show?model=${encodeURIComponent(model)}`),
  // Delete a downloaded model to reclaim disk.
  ollamaDeleteModel: (provider: string, model: string) =>
    post<{ ok: boolean; model: string }>(`/api/model-providers/${encodeURIComponent(provider)}/models/delete`, { model }),
  // Pull (download) an Ollama model via the named provider, streaming NDJSON
  // progress frames ({status, completed?, total?} or {error}) to onFrame until
  // the stream ends. Resolves when complete; rejects on transport error (#45).
  // Pass an AbortSignal to let the user STOP the download: aborting the fetch
  // closes the connection, which the backend detects and cancels the pull (#48).
  pullOllamaModel: async (provider: string, model: string, onFrame: (f: Record<string, unknown>) => void, signal?: AbortSignal) => {
    const r = await fetch(`/api/model-providers/${encodeURIComponent(provider)}/pull`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ model }), signal,
    })
    if (!r.ok || !r.body) throw new Error(await errText(r))
    const reader = r.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let nl: number
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim()
        buf = buf.slice(nl + 1)
        if (line) { try { onFrame(JSON.parse(line)) } catch { /* skip partial */ } }
      }
    }
    if (buf.trim()) { try { onFrame(JSON.parse(buf.trim())) } catch { /* ignore */ } }
  },
  // Local downloadable models — ONE uniform provider-scoped surface for every local
  // provider (faster-whisper/piper/sentence-transformers/diarization/ollama). The
  // catalog comes from /api/models/available (per-provider `models`); these drive the
  // lifecycle. Downloads run as async jobs (minutes-long); progress streams over the
  // per-job SSE at downloadStreamUrl. POST returns 202 with the job.
  startModelDownload: (provider: string, model: string) =>
    post<DownloadJob>('/api/models/downloads', { provider, model }),
  modelDownloads: () => get<{ downloads: DownloadJob[] }>('/api/models/downloads').then((d) => d.downloads ?? []),
  cancelModelDownload: (id: string) => del(`/api/models/downloads/${encodeURIComponent(id)}`),
  downloadStreamUrl: (id: string) => `/api/models/downloads/${encodeURIComponent(id)}/stream`,
  // Partial-download leftovers across every local provider's cache root — the files a
  // cancelled/crashed fetch leaves behind. Powers the "Reclaim N GB" affordance.
  modelDownloadCleanupCandidates: () =>
    get<{ candidates: { path: string; bytes: number }[]; total_bytes: number }>('/api/models/downloads/cleanup-candidates'),
  modelDownloadCleanup: () =>
    post<{ removed: number; freed_bytes: number }>('/api/models/downloads/cleanup', { confirm: true }),
  deleteLocalModel: (provider: string, model: string) =>
    del(`/api/models/local/${encodeURIComponent(provider)}/${encodeURIComponent(model)}`),
  // Search a searchable provider's remote installable catalog (ollama's library).
  searchLocalModels: (provider: string, q: string) =>
    get<{ models: LocalModel[] }>(`/api/models/local/${encodeURIComponent(provider)}/search?q=${encodeURIComponent(q)}`).then((d) => d.models ?? []),
  // dashboard config (server-persisted prefs incl. the operator name)
  dashboardConfig: () => get<DashboardConfig>('/api/dashboard/config'),
  saveDashboardConfig: (body: Partial<DashboardConfig>) => put<{ ok: boolean }>('/api/dashboard/config', body),

  // onboarding readiness + the in-flow fix (bind a chat model)
  onboarding: () => get<OnboardingState>('/api/onboarding'),
  chatModels: () => get<ChatModelOption[]>('/api/models/chat'),
  setActiveModel: (useCase: string, models: string[]) => put<{ ok?: boolean }>(`/api/models/active/${encodeURIComponent(useCase)}`, { models }),
  // Re-index all knowledge + memory embeddings after the embedding model changed.
  // 409 {code:'model_not_ready'} if the new model can't produce vectors.
  startEmbeddingReindex: () => post<ReindexJob>('/api/models/embedding/reindex'),
  embeddingReindexStreamUrl: (id: string) => `/api/models/embedding/reindex/${encodeURIComponent(id)}/stream`,

  // Slash commands offered in the composer "/" menu (backend excludes TUI-only
  // blocked commands + supplies one-line hints).
  slashCommands: () => get<{ name: string; description: string }[]>('/api/slash-commands'),
  // sessions
  chatSessions: (archived = false) =>
    get<ChatSessionSummary[]>(`/api/chat/sessions${archived ? '?archived=1' : ''}`),
  pinChatSession: (session: string, pinned: boolean) => patch(`/api/chat/sessions/${encodeURIComponent(session)}/pin`, { pinned }),
  // ── chat organization: folders, tags, kanban tag-columns (backend already
  //    persists folder_id/tags/color_index per session; legacy web exposes these) ──
  chatFolders: () => get<ChatFolder[]>('/api/chat/folders'),
  createChatFolder: (name: string, parentId?: string) => post<ChatFolder>('/api/chat/folders', { name, parent_id: parentId || '' }),
  updateChatFolder: (id: string, body: Partial<ChatFolder>) => patch<ChatFolder>(`/api/chat/folders/${encodeURIComponent(id)}`, body),
  deleteChatFolder: (id: string) => del(`/api/chat/folders/${encodeURIComponent(id)}`),
  setSessionFolder: (session: string, folderId: string | null) => patch(`/api/chat/sessions/${encodeURIComponent(session)}/folder`, { folder_id: folderId || '' }),
  chatTags: () => get<ChatTag[]>('/api/chat/tags'),
  createChatTag: (name: string, color?: string) => post<ChatTag>('/api/chat/tags', { name, color: color || '' }),
  updateChatTag: (id: string, body: Partial<ChatTag>) => patch<ChatTag>(`/api/chat/tags/${encodeURIComponent(id)}`, body),
  deleteChatTag: (id: string) => del(`/api/chat/tags/${encodeURIComponent(id)}`),
  setSessionTags: (session: string, tags: string[]) => put(`/api/chat/sessions/${encodeURIComponent(session)}/tags`, { tags }),
  // Suggested organization (SM T2.1). The GET only READS — a suggestion never applies
  // itself; organizeAccept is the sole path that writes folder/tags from a proposal.
  organizeSuggestion: (session: string, opts: { llm?: boolean } = {}) =>
    get<{ proposal: OrganizeProposal | null }>(`/api/chat/sessions/${encodeURIComponent(session)}/organize${opts.llm === false ? '?llm=0' : ''}`),
  organizeAccept: (session: string, p: OrganizeProposal) =>
    post<{ ok: boolean; folder_id: string; tags: string[] }>(`/api/chat/sessions/${encodeURIComponent(session)}/organize/accept`, { folder_id: p.folder_id, folder_name: p.folder_name, tags: p.tags, source: p.source }),
  organizeDecline: (session: string, p: OrganizeProposal) =>
    post<{ ok: boolean; declined: boolean }>(`/api/chat/sessions/${encodeURIComponent(session)}/organize/decline`, { folder_id: p.folder_id, folder_name: p.folder_name, tags: p.tags, source: p.source }),
  // Magic re-tag: batch AI re-evaluation of every session's tags (board's
  // sparkle button). Progress arrives over /api/ws as retag_progress/retag_done.
  retagAllSessions: () => post<RetagJob>('/api/sessions/retag-all', {}),
  retagStatus: () => get<RetagJob>('/api/sessions/retag-all'),
  cancelRetag: () => post('/api/sessions/retag-all/cancel', {}),
  tagColumns: () => get<TagColumn[]>('/api/chat/tag-columns'),
  createTagColumn: (body: Partial<TagColumn>) => post<TagColumn>('/api/chat/tag-columns', body),
  updateTagColumn: (id: string, body: Partial<TagColumn>) => patch<TagColumn>(`/api/chat/tag-columns/${encodeURIComponent(id)}`, body),
  deleteTagColumn: (id: string) => del(`/api/chat/tag-columns/${encodeURIComponent(id)}`),
  reorderTagColumns: (ids: string[]) => put('/api/chat/tag-columns/order', { ids }),
  dropSessionToColumn: (session: string, columnId: string) => post(`/api/chat/sessions/${encodeURIComponent(session)}/drop`, { column_id: columnId }),
  chatSessionDetail: (key: string) => get<{ key: string; title: string; messages: ChatHistoryMsg[]; running?: boolean; pending_approval?: boolean; agent?: string; model?: string; mode?: string; acp_provider?: string; acp_provider_agent?: string; reasoning_effort?: string; task_mode?: TaskMode; approval?: ApprovalMode; memory_mode?: string; queue?: { id: string; content: string }[]; side?: { open: boolean; messages: { role: string; content: string }[] } | null }>(`/api/chat/sessions/${encodeURIComponent(key)}`),
  deleteChatSession: (key: string) => del(`/api/chat/sessions/${encodeURIComponent(key)}`),
  // ── session lifecycle + bulk (SESSION-MANAGEMENT S2) ──
  setSessionLifecycle: (session: string, body: { lifecycle?: 'active' | 'archived'; never_archive?: boolean }) =>
    patch<{ ok: boolean; lifecycle: string; never_archive: boolean }>(`/api/chat/sessions/${encodeURIComponent(session)}/lifecycle`, body),
  bulkSessions: (op: 'archive' | 'restore' | 'tag' | 'untag' | 'folder' | 'never_archive', keys: string[], args: { tag_id?: string; folder_id?: string; value?: boolean } = {}) =>
    post<{ ok: boolean; op: string; changed: string[]; unchanged: string[]; missing: string[] }>('/api/chat/sessions/bulk', { op, keys, ...args }),
  autoArchiveSessions: (opts: { dry_run?: boolean; active_session?: string } = {}) =>
    post<{ ok: boolean; enabled: boolean; days: number; keys: string[]; count: number }>('/api/chat/sessions/auto-archive', opts),
  // ── session templates + export (SESSION-MANAGEMENT S3) ──
  sessionTemplates: () =>
    get<{ templates: SessionTemplate[] }>('/api/chat/sessions/templates').then((d) => d.templates),
  createSessionTemplate: (body: SessionTemplateInput) =>
    post<{ ok: boolean; template: SessionTemplate }>('/api/chat/sessions/templates', body),
  updateSessionTemplate: (id: string, body: SessionTemplateInput) =>
    put<{ ok: boolean; template: SessionTemplate }>(`/api/chat/sessions/templates/${encodeURIComponent(id)}`, body),
  deleteSessionTemplate: (id: string) =>
    del(`/api/chat/sessions/templates/${encodeURIComponent(id)}`),
  /** Export URL — a plain link, so the browser downloads via Content-Disposition
   *  rather than this client buffering the transcript in memory. */
  sessionExportUrl: (key: string, format: 'md' | 'json') =>
    `/api/chat/sessions/${encodeURIComponent(key)}/export?format=${format}`,
  /** Share a chat as a redacted, READ-ONLY artifact in this instance's own library
   *  (SM-9). POST because it creates durable state, and nothing publishes it anywhere:
   *  there is no public link and no token — an artifact the owner can open, and nobody
   *  else can reach without this gateway's session auth. */
  shareSession: (key: string) =>
    post<{ ok: boolean; slug: string; name: string; kind: ArtifactKind; readonly: boolean; redacted: boolean }>(
      `/api/chat/sessions/${encodeURIComponent(key)}/share`, {}),
  createChatSession: (opts: { name?: string; agent?: string; model?: string; memory_mode?: MemoryMode; mode?: string; project_id?: string } = {}) =>
    post<ChatSession>('/api/chat/sessions', opts),
  setSessionAgent: (session: string, agent: string) => post(`/api/chat/sessions/${session}/agent`, { agent }),
  setSessionAcpAgent: (session: string, body: { provider: string; provider_agent?: string; model?: string; reasoning_effort?: ReasoningEffort }) =>
    post(`/api/chat/sessions/${session}/acp-agent`, body),
  setSessionModel: (session: string, model: string) => post(`/api/chat/sessions/${session}/model`, { model }),
  setReasoningEffort: (session: string, reasoning_effort: ReasoningEffort) =>
    post(`/api/chat/sessions/${session}/reasoning-effort`, { reasoning_effort }),
  setApprovalMode: (mode: ApprovalMode, session = '') => post('/api/chat/mode', { mode, session }),
  setTaskMode: (mode: TaskMode, session = '') => post('/api/chat/task-mode', { mode, session }),

  // composer tools: prompt optimizer + speech-to-text transcription.
  optimizePrompt: (prompt: string, context = '') =>
    post<{ optimized?: string; changed?: boolean }>('/api/optimizer/optimize', { prompt, context }),
  transcribeAudio: async (blob: Blob): Promise<{ text?: string; error?: string }> => {
    const fd = new FormData()
    fd.append('audio', blob, 'recording.webm')
    const r = await fetch('/api/stt/transcribe', { method: 'POST', headers: { ...SK }, body: fd })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) return { error: data?.error || `HTTP ${r.status}` }
    return data
  },

  // send / control
  sendChat: (message: string, session: string, meta?: object, queue_mode?: string) =>
    post<{ ok: boolean; session?: string; queued?: boolean; steered?: boolean }>('/api/chat?ws=1', { message, session, meta, ...(queue_mode ? { queue_mode } : {}) }),
  // Cancel a still-pending queued message (mid-stream FIFO) by its queue id.
  cancelQueued: (session: string, queueId: string) => del(`/api/chat/sessions/${encodeURIComponent(session)}/queue/${encodeURIComponent(queueId)}`),
  stopChat: (session: string, force = false) => post(`/api/chat/sessions/${session}/stop${force ? '?force=true' : ''}`),
  approve: (session: string, action: string, request_id?: string) =>
    post(`/api/chat/sessions/${session}/approve`, { action, request_id }),

  // side chat (stage 6) — an isolated throwaway chat against a snapshot of the
  // session; streams deltas over the `chat.side_result` WS event.
  sideOpen: (session: string) => post<{ ok: boolean }>(`/api/chat/sessions/${session}/side/open`, {}),
  sideTurn: (session: string, question: string) => post<{ ok: boolean; run_id: string }>(`/api/chat/sessions/${session}/side/turn`, { question }),
  sideClose: (session: string) => post<{ ok: boolean }>(`/api/chat/sessions/${session}/side/close`, {}),
  // /undo N — roll back the last N conversation turns (power-user-surfaces P7). Returns
  // how many turns were removed + an honest notice that side effects were NOT reverted.
  undoChat: (session: string, n = 1) =>
    post<{ ok: boolean; turns_undone: number; notice: string }>(`/api/chat/sessions/${session}/undo`, { n }),

  // auto-nudge: a reactive same-session loop — when a turn completes and no user
  // input arrives within idle_secs, the service injects `message` into the SAME
  // session (survives reload/restart). Disabled (503) unless PERSONALCLAW_AUTONUDGE
  // is set; the UI degrades to a "not enabled" state then.
  autonudgeGet: (session: string) =>
    get<{ enabled: boolean; loop: NudgeLoop | null }>(`/api/autonudge/session/${encodeURIComponent(session)}`),
  autonudgeStart: (body: { session_name: string; message: string; idle_secs?: number; max_cycles?: number }) =>
    post<{ ok: boolean; loop: NudgeLoop }>('/api/autonudge', body),
  autonudgeUpdate: (loopId: string, body: { message?: string; idle_secs?: number; max_cycles?: number; active?: boolean }) =>
    patch<{ ok: boolean; loop: NudgeLoop }>(`/api/autonudge/${encodeURIComponent(loopId)}`, body),
  autonudgeDelete: (loopId: string) => del(`/api/autonudge/${encodeURIComponent(loopId)}`),

  // session title: set explicitly, or have the model generate one from the convo.
  renameSession: (session: string, title: string) =>
    patch<{ ok: boolean; title: string }>(`/api/chat/sessions/${encodeURIComponent(session)}/title`, { title }),
  generateTitle: (session: string) =>
    post<{ ok: boolean; title?: string }>(`/api/chat/sessions/${encodeURIComponent(session)}/generate-title`),

  // message actions (stage 4) — all stream the new reply over the dashboard WS.
  regenerate: (session: string) => post<{ ok: boolean }>(`/api/chat/sessions/${session}/regenerate`),
  // Switch which regenerated answer variant is active on the latest assistant turn.
  // The backend swaps the message content + broadcasts chat_variant_switch (echoed to
  // every tab); returns the now-active index. 409 if the session is mid-turn.
  switchVariant: (session: string, index: number) =>
    post<{ ok: boolean; index: number }>(`/api/chat/sessions/${session}/switch-variant`, { index }),
  editResend: (session: string, content: string, ts?: string, index?: number, client_ts?: string, rewind?: boolean) =>
    post<{ ok: boolean; rewound: number }>(`/api/chat/sessions/${session}/edit-resend`,
      // Prefer the original turn's ts to LOCATE the message; always send the index
      // as a fallback (un-hydrated optimistic turns have no ts) + a fresh client_ts
      // the backend stores on the re-appended message so a repeat edit still matches.
      // rewind=true → fork-and-swap (edit ANY past turn): retain the discarded tail
      // on the edited message + reset the provider so context rebuilds truncated.
      { content, ...(ts ? { ts } : {}), ...(index !== undefined ? { index } : {}), ...(client_ts ? { client_ts } : {}), ...(rewind ? { rewind: true } : {}) }),
  // Interrupt the running turn but KEEP the queue (unlike /stop). Optional queueId
  // promotes that queued message to the front so it runs next (queue_promoted WS echo).
  interruptChat: (session: string, queueId?: string) =>
    post<{ ok: boolean }>(`/api/chat/sessions/${session}/interrupt`, queueId ? { queue_id: queueId } : {}),
  forkSession: (session: string, at_message_index?: number) =>
    post<{ ok: boolean; key: string; title: string; messages: number; prompt?: string }>(`/api/chat/sessions/${session}/fork`, at_message_index != null ? { at_message_index } : {}),
  // Restore a rewind tail (CHAT-CRAFT S1) as a NEW fork — reconstructs pre-edit
  // history + the retained tail into a fresh session (restore = fork, never swap).
  forkRewound: (session: string, index: number, snapshot_index?: number) =>
    post<{ ok: boolean; key: string; title: string; messages: number }>(`/api/chat/sessions/${session}/fork-rewound`, { index, ...(snapshot_index != null ? { snapshot_index } : {}) }),
  voiceSynthesize: (text: string, session = '') => post<{ ok: boolean; chunks: number }>('/api/voice/synthesize', { text, session }),

  // ── Unified Loop client (/api/loops, kind-aware) — the ONE surface for every kind
  // (goal/code/general/design). EVERY FE surface (Goal + Code) is migrated onto these
  // (uLoop*); the legacy loop*/code* methods + the /api/code routes are deleted. The `u`
  // prefix is now purely historical (no legacy loop* names left to collide with).
  // Methods mirror loop_routes.py 1:1.
  uLoops: (params?: { projectId?: string; kind?: LoopKind }) => {
    const q = new URLSearchParams()
    if (params?.projectId) q.set('project_id', params.projectId)
    if (params?.kind) q.set('kind', params.kind)
    const qs = q.toString()
    return get<{ loops: Loop[] }>(`/api/loops${qs ? `?${qs}` : ''}`).then((d) => d.loops)
  },
  uLoop: (id: string) => get<Loop>(`/api/loops/${encodeURIComponent(id)}`),
  uLoopReport: (id: string) => get<{ report: string; log: string }>(`/api/loops/${encodeURIComponent(id)}/report`),
  uLoopStreamUrl: (id: string) => `/api/loops/${encodeURIComponent(id)}/stream`,
  classifyULoop: (kind: LoopKind, task: string) =>
    post<UnifiedLoopClassification>('/api/loops/classify', { kind, task }),
  // Guided decomposition (#16): memory-checked question-tree for a created loop's goal.
  grillTree: (id: string) => post<GrillTreeResult>(`/api/loops/${encodeURIComponent(id)}/grill-tree`, {}),
  validateULoop: (body: Record<string, unknown>) => post<LoopValidation>('/api/loops/validate', body),
  createULoop: (body: Record<string, unknown>) => post<Loop>('/api/loops', body),
  updateULoop: (id: string, body: Record<string, unknown>) => put<Loop>(`/api/loops/${encodeURIComponent(id)}`, body),
  uLoopAction: (id: string, action: 'start' | 'pause' | 'resume' | 'stop') =>
    fetch(`/api/loops/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ action }) }).then(j<Loop>),
  uLoopNudge: (id: string, text: string, taskId?: string) => post(`/api/loops/${encodeURIComponent(id)}/nudge`, taskId ? { text, task_id: taskId } : { text }),
  deleteULoop: (id: string) => fetch(`/api/loops/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw new ApiError(await errText(r), r.status) }),
  uLoopQueue: (id: string, taskIds: string[], action: 'queue' | 'unqueue' = 'queue') =>
    post<{ ok: boolean; queued_task_ids: string[] }>(`/api/loops/${encodeURIComponent(id)}/queue`, { task_ids: taskIds, action }),
  uLoopAutopilot: (id: string, on: boolean) =>
    post<{ ok: boolean; autopilot: boolean }>(`/api/loops/${encodeURIComponent(id)}/autopilot`, { on }),
  uLoopPlanSession: (id: string) => get<{ session: PlanSession | null }>(`/api/loops/${encodeURIComponent(id)}/plan-session`).then((d) => d.session),
  uLoopPlanStart: (id: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/start`, {}),
  uLoopPlanRetry: (id: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/retry`, {}),
  uLoopPlanApprove: (id: string, stepId: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/approve`, { step_id: stepId }),
  uLoopPlanComment: (id: string, stepId: string, text: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/comment`, { step_id: stepId, text }),
  uLoopPlanEdit: (id: string, stepId: string, markdown: string) => post<{ ok: boolean; session: PlanSession }>(`/api/loops/${encodeURIComponent(id)}/plan/edit`, { step_id: stepId, markdown }),

  // Design kind — the comprehensive default token set + its schema (global), and a
  // design loop's RESOLVED token tree + CSS-variable block for the live canvas.
  designDefaultTokens: (scheme: 'light' | 'dark' = 'light') =>
    get<{ tokens: Record<string, unknown>; schema: Record<string, unknown>; resolved: Record<string, unknown>; css: string; overrides: Record<string, unknown>; scheme: string }>(`/api/design/tokens/default?scheme=${scheme}`),
  uLoopDesignTokens: (id: string, scheme: 'light' | 'dark' = 'light') =>
    get<{ resolved: Record<string, unknown>; css: string; overrides: Record<string, unknown>; scheme: string }>(`/api/loops/${encodeURIComponent(id)}/design/tokens?scheme=${scheme}`),

  // notifications — items keyed by `ts` (the backend ack/unack/delete take ts,
  // NOT job_id; the old job_id ack was a no-op for most items).
  notifications: () => get<{ notifications: NotificationItem[]; unread: number }>('/api/notifications'),
  ackNotification: (ts: string) => post('/api/notifications/ack', { ts }),

  // Live status snapshot (uptime/version/counts/update/YOLO) — powers the
  // dashboard Hero + System Health widgets.
  status: () => get<DashboardStatus>('/api/status'),
  // Pending tool approvals (dashboard Action Center). resolveApproval mirrors the
  // in-chat approve/reject, keyed by the approval id.
  approvals: () => get<PendingApproval[]>('/api/approvals'),
  resolveApproval: (id: string, action: 'approve' | 'reject') =>
    post<{ ok: boolean }>(`/api/approvals/${encodeURIComponent(id)}/${action}`, {}),
  // Inbox items awaiting a decision (richer than client-filtering /api/inbox).
  inboxPending: () => get<InboxItem[]>('/api/inbox/pending'),
  // Cross-trigger run index (dashboard Schedule widget) — newest runs across all
  // schedules, distinct from the per-schedule history the trigger detail uses.
  // Returns §1.3's archive split alongside the rows: `did_ids` are fires that DID something,
  // `suppressed_ids` the ones a gate held. Typed here because the backend has computed them since
  // S132 and this wrapper declared only `{runs, total}`, so every consumer silently dropped them
  // (S163) — a surface that cannot tell the two apart buries the one fire that mattered under
  // 1439 skips, which is the exact failure the split exists to prevent.
  triggersHistory: (limit = 20, offset = 0) =>
    get<{
      runs: ScheduleRun[]; total: number; schedule_total?: number; kinds?: string[]
      summaries?: number; did_ids?: string[]; suppressed_ids?: string[]; suppressed?: number
    }>(`/api/triggers/history?limit=${limit}&offset=${offset}`),
  unackNotification: (ts: string) => post('/api/notifications/unack', { ts }),
  ackAllNotifications: () => post('/api/notifications/ack-all'),
  deleteNotification: (ts: string) => fetch('/api/notifications', { method: 'DELETE', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ ts }) }).then((r) => { if (!r.ok) throw new Error('delete failed') }),
  clearNotifications: () => post('/api/notifications/clear'),

  // Triggers — the unified surface (schedule + lifecycle). The schedule helpers
  // below speak the schedule wire shape the shared Schedule* components already
  // use; the api layer namespaces the id (schedule:<id>) and routes to /api/triggers.
  triggers: (type?: 'schedule' | 'lifecycle' | 'event') =>
    get<{ triggers: Trigger[]; server_tz: string }>(`/api/triggers${type ? `?type=${type}` : ''}`),
  // The week-grid projection (AUTO-A3). `start` is a local ISO datetime; the backend computes every
  // occurrence from the recurrence each trigger already carries — read-only, no store changes.
  triggersWeek: (start?: string, days = 7) => {
    const qs = new URLSearchParams()
    if (start) qs.set('start', start)
    qs.set('days', String(days))
    return get<WeekProjection>(`/api/triggers/week?${qs.toString()}`)
  },
  // ── event-kind (data-event) triggers: the S67 parity surface ──
  // The backend handled `event` in list/create/DELETE only; toggle/run/test/PUT fell through to the
  // schedule branch and answered 404, so the UI had no way to reach them and no client methods
  // existed. `ran` is whether the trigger REACHED its provider; `success` is the provider's own
  // verdict — a misconfigured action reports ran:true / success:false, which is a different problem
  // from "it never fired" and must stay distinguishable.
  eventTriggers: () => get<{ triggers: Trigger[] }>('/api/triggers?type=event').then((d) => d.triggers),
  // Create a data-event trigger (EIAT-5). The backend DERIVES `source` from `pattern`
  // (PATTERN_SOURCE) — never taken from the wire — so the body carries only the pattern, its
  // one wired matcher field, the action, and an optional max_fires. A 201 body may carry a
  // `warning` (a catastrophic content_re warns rather than refuses, §7/R4 rule d).
  createEvent: (body: {
    name?: string; pattern: EventPattern
    sender_glob?: string; address_glob?: string; key_glob?: string; content_re?: string
    // AppEvent's matcher (AUTO-A4): a glob on the NAMESPACED event name (`app:<app>:<event>`).
    // Empty matches every app event — the catch-all, which is why AppEvent needs no second pattern.
    event_glob?: string
    max_fires?: number; action: { provider: string; config: Record<string, unknown> }
  }) => post<Trigger & { warning?: string }>('/api/triggers', { trigger_type: 'event', ...body }),
  updateEventTrigger: (id: string, body: Record<string, unknown>) =>
    put<{ ok: boolean; trigger: Trigger }>(`/api/triggers/event:${encodeURIComponent(id)}`, body),
  deleteEventTrigger: (id: string) => del(`/api/triggers/event:${encodeURIComponent(id)}`),
  toggleEventTrigger: (id: string, enabled?: boolean) =>
    post<{ ok: boolean; trigger: Trigger }>(`/api/triggers/event:${encodeURIComponent(id)}/toggle`, enabled === undefined ? {} : { enabled }),
  runEventTrigger: (id: string, body?: { key?: string; value?: string; event_type?: string }) =>
    post<EventFireResult>(`/api/triggers/event:${encodeURIComponent(id)}/run`, body ?? {}),
  testEventTrigger: (id: string, body?: { key?: string; value?: string; event_type?: string }) =>
    post<EventFireResult>(`/api/triggers/event:${encodeURIComponent(id)}/test`, { ...(body ?? {}), test: true }),
  eventTriggerHistory: (id: string) =>
    get<{ runs: never[]; total: number; supported: boolean; reason: string; fire_count: number; last_fired_at: number }>(`/api/triggers/event:${encodeURIComponent(id)}/history`),
  // schedule trigger helpers (id is the bare schedule raw id — the shared
  // Schedule* components mutate by bare id, which the helpers re-namespace).
  schedules: () => get<{ triggers: Trigger[]; server_tz: string }>('/api/triggers?type=schedule')
    .then((d) => ({ jobs: d.triggers.map((t) => ({ ...t, id: t.raw_id })) as unknown as ScheduleJob[], server_tz: d.server_tz })),
  createSchedule: (body: Record<string, unknown>) =>
    post<{ ok: boolean; trigger: Trigger }>('/api/triggers', { trigger_type: 'schedule', ..._scheduleBodyToWire(body) }),
  updateSchedule: (id: string, body: Record<string, unknown>) =>
    put<{ ok: boolean; trigger: Trigger }>(`/api/triggers/schedule:${encodeURIComponent(id)}`, _scheduleBodyToWire(body)),
  deleteSchedule: (id: string) => del(`/api/triggers/schedule:${encodeURIComponent(id)}`),
  runSchedule: (id: string, dryRun = false) =>
    post<TriggerRunResult>(`/api/triggers/schedule:${encodeURIComponent(id)}/run`, dryRun ? { dry_run: true } : undefined),
  enableSchedule: (id: string, enabled: boolean) => post(`/api/triggers/schedule:${encodeURIComponent(id)}/toggle`, { enabled }),
  scheduleToChat: (id: string) => post<{ ok: boolean; session: string }>(`/api/triggers/schedule:${encodeURIComponent(id)}/to-chat`),
  // Per-trigger run history. `triggerId` is the FULL facade id (`schedule:abc`, `store:file:notes`)
  // — these wrappers hardcoded a `schedule:` prefix, so a store trigger's history was unrequestable
  // even after the backend began serving it (S166/S167). `supported: false` is a real answer here:
  // a lifecycle trigger keeps no run store, and the caller renders the reason rather than an empty
  // list, because "no runs" and "this kind records none" are different claims.
  triggerHistory: (triggerId: string, limit = 10, offset = 0) =>
    get<{ runs: ScheduleRun[]; total: number; supported?: boolean; reason?: string }>(
      `/api/triggers/${encodeURIComponent(triggerId)}/history?limit=${limit}&offset=${offset}`),
  triggerRunDetail: (triggerId: string, runId: string) =>
    get<{ run: ScheduleRun }>(
      `/api/triggers/${encodeURIComponent(triggerId)}/history/${encodeURIComponent(runId)}`).then((d) => d.run),
  scheduleHistory: (id: string, limit = 10, offset = 0) => get<{ runs: ScheduleRun[]; total: number }>(`/api/triggers/schedule:${encodeURIComponent(id)}/history?limit=${limit}&offset=${offset}`),
  scheduleRunDetail: (id: string, runId: string) => get<{ run: ScheduleRun }>(`/api/triggers/schedule:${encodeURIComponent(id)}/history/${encodeURIComponent(runId)}`).then((d) => d.run),
  triggerVariables: () => get<TriggerVariables>('/api/triggers/variables'),

  // tasks
  // `mine` narrows to the owner's work (assigned to them, or authored by them and
  // unassigned) — resolved server-side from the configured username. `owner` comes
  // back on every response so rows can be labelled mine vs someone else's.
  tasks: (opts: { project?: string; task_list?: string; status?: string; limit?: number; mine?: boolean } = {}) => {
    const qs = new URLSearchParams()
    if (opts.project) qs.set('project', opts.project)
    if (opts.task_list) qs.set('task_list', opts.task_list)
    if (opts.status) qs.set('status', opts.status)
    if (opts.limit) qs.set('limit', String(opts.limit))
    if (opts.mine) qs.set('mine', '1')
    const s = qs.toString()
    return get<{ tasks: TaskItem[]; total: number; owner?: string }>(`/api/tasks${s ? `?${s}` : ''}`)
  },
  task: (id: string, provider?: string) => get<TaskItem>(`/api/tasks/${encodeURIComponent(id)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  taskGraph: (provider?: string) => get<TaskGraphData>(`/api/tasks/graph${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  createTask: (body: Record<string, unknown>) => post<TaskItem>('/api/tasks', body),
  updateTask: (id: string, body: Record<string, unknown>) => put<TaskItem>(`/api/tasks/${encodeURIComponent(id)}`, body),
  deleteTask: (id: string, provider?: string) => del(`/api/tasks/${encodeURIComponent(id)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  taskComments: (id: string, provider?: string) => get<{ comments: TaskComment[] }>(`/api/tasks/${encodeURIComponent(id)}/comments${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`).then((d) => d.comments),
  addTaskComment: (id: string, body: string, provider?: string) => post<TaskComment>(`/api/tasks/${encodeURIComponent(id)}/comments`, { body, provider }),
  deleteTaskComment: (id: string, commentId: string, provider?: string) => del(`/api/tasks/${encodeURIComponent(id)}/comments/${encodeURIComponent(commentId)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  readyTasks: (opts: { project?: string; task_list_id?: string } = {}) => {
    const qs = new URLSearchParams()
    if (opts.project) qs.set('project', opts.project)
    if (opts.task_list_id) qs.set('task_list_id', opts.task_list_id)
    const s = qs.toString()
    return get<{ tasks: TaskItem[] }>(`/api/tasks/ready${s ? `?${s}` : ''}`).then((d) => d.tasks)
  },
  searchTasks: (body: Record<string, unknown>) => post<{ tasks: TaskItem[]; total: number }>('/api/tasks/search', body),

  // projects + task lists (Project → TaskList → Task hierarchy)
  projects: () => get<{ projects: ProjectItem[] }>('/api/projects').then((d) => d.projects),
  project: (id: string) => get<ProjectItem>(`/api/projects/${encodeURIComponent(id)}`),
  projectLinked: (id: string) => get<{ loops: ProjectLinkedItem[]; code: ProjectLinkedItem[]; artifacts: { slug: string; name: string; kind: string }[]; chats: { key: string; title: string; running: boolean }[]; knowledge: ProjectKnowledgeItem[] }>(`/api/projects/${encodeURIComponent(id)}/linked`),
  // The state-grouped Work board: runs + legacy loops + tasks in one board, per-section
  // isolated (a failed source degrades one section, the board still renders).
  projectWork: (id: string) => get<WorkBoard>(`/api/projects/${encodeURIComponent(id)}/work`),
  claimWork: (id: string, target_id: string, holder: string) =>
    post<{ granted: boolean; claim: WorkClaim | null; reason: string }>(`/api/projects/${encodeURIComponent(id)}/work/claim`, { target_id, holder }),
  releaseWork: (id: string, target_id: string, holder: string) =>
    post<{ released: boolean; claim: WorkClaim | null; reason: string }>(`/api/projects/${encodeURIComponent(id)}/work/release`, { target_id, holder }),
  createProject: (body: { name: string; brief?: string; agent_instructions_template?: string; workspace_dir?: string; name_locked?: boolean }) => post<ProjectItem>('/api/projects', body),
  updateProject: (id: string, body: Record<string, unknown>) => put<ProjectItem>(`/api/projects/${encodeURIComponent(id)}`, body),
  deleteProject: (id: string, force = false) => del(`/api/projects/${encodeURIComponent(id)}${force ? '?force=true' : ''}`),
  // Legibility §7 — render the marker-fenced PClaw context block into the project's
  // bound workspace_dir adapter files (CLAUDE.md / AGENTS.md / .cursorrules), replace-
  // in-place. Gated server-side on legibility.context_adapters + a bound workspace_dir.
  regenerateContextAdapters: (id: string) =>
    post<{ ok: boolean; written: string[]; errors: { file: string; error: string }[]; workspace_dir: string }>(
      `/api/projects/${encodeURIComponent(id)}/context-adapters/regenerate`, {}),
  taskLists: (projectId?: string) => get<{ task_lists: TaskListItem[] }>(`/api/task-lists${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`).then((d) => d.task_lists),
  createTaskList: (body: Record<string, unknown>) => post<TaskListItem>('/api/task-lists', body),
  updateTaskList: (id: string, body: Record<string, unknown>) => put<TaskListItem>(`/api/task-lists/${encodeURIComponent(id)}`, body),
  deleteTaskList: (id: string) => del(`/api/task-lists/${encodeURIComponent(id)}`),
  resetTaskList: (id: string) => post<{ ok: boolean; reset_task_ids: string[] }>(`/api/task-lists/${encodeURIComponent(id)}/reset`, {}),

  // workflows

  // prompts
  prompts: (kind?: PromptKind) => get<PromptItem[]>(`/api/prompts${kind ? `?kind=${kind}` : ''}`),
  prompt: (name: string) => get<PromptItem>(`/api/prompts/${encodeURIComponent(name)}`),
  createPrompt: (body: Record<string, unknown>) => post<{ ok: boolean; name: string; prompt: PromptItem }>('/api/prompts', body),
  savePrompt: (name: string, body: Record<string, unknown>) => put<{ ok: boolean; prompt: PromptItem }>(`/api/prompts/${encodeURIComponent(name)}`, body),
  deletePrompt: (name: string) => del(`/api/prompts/${encodeURIComponent(name)}`),
  renderPrompt: (name: string, variables: Record<string, unknown>) => post<{ name: string; rendered: string }>(`/api/prompts/${encodeURIComponent(name)}/render`, { variables }),
  // Runnable "campaign template" (#17): render with values + create+start a loop.
  launchCampaignTemplate: (name: string, variables: Record<string, unknown>, projectId?: string) =>
    post<{ ok: boolean; loop_id: string; kind: LoopKind; started: boolean }>(
      `/api/prompts/${encodeURIComponent(name)}/launch`, projectId ? { variables, project_id: projectId } : { variables }),
  // Live preview of UNSAVED content through the real render engine (no drift).
  previewPrompt: (body: { content: string; variables?: PromptVariable[]; values?: Record<string, unknown> }) => post<PromptPreview>('/api/prompts/preview', body),
  // The template-language reference (functions + constructs) — fetched once for the cheatsheet/autocomplete.
  promptSyntax: () => get<PromptSyntax>('/api/prompts/syntax'),
  // prompt snippets (reusable {{> name}} fragments)
  snippets: () => get<PromptSnippet[]>('/api/prompt-snippets'),
  snippet: (name: string) => get<PromptSnippet>(`/api/prompt-snippets/${encodeURIComponent(name)}`),
  createSnippet: (body: Record<string, unknown>) => post<{ ok: boolean; name: string; snippet: PromptSnippet }>('/api/prompt-snippets', body),
  saveSnippet: (name: string, body: Record<string, unknown>) => put<{ ok: boolean; snippet: PromptSnippet }>(`/api/prompt-snippets/${encodeURIComponent(name)}`, body),
  // carries the backend message (e.g. the 409 "included by N items" usage guard) so
  // the UI can explain why a delete was refused — not the generic del() "delete failed".
  deleteSnippet: (name: string) => fetch(`/api/prompt-snippets/${encodeURIComponent(name)}`, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw new ApiError(await errText(r), r.status) }),
  renderSnippet: (name: string, variables: Record<string, unknown>) => post<{ name: string; rendered: string }>(`/api/prompt-snippets/${encodeURIComponent(name)}/render`, { variables }),
  // prompt use-case bindings (which system prompt serves chat/background/code/goal_loop)
  promptBindings: () => get<PromptBindings>('/api/prompts/bindings'),
  setPromptBinding: (use_case: string, ref: string) => put<PromptBindings>('/api/prompts/bindings', { use_case, ref }),

  // skills
  skills: () => get<SkillItem[]>('/api/skills'),
  skillFiles: (name: string, path?: string) => get<{ name: string; files?: SkillFile[]; path?: string; content?: string }>(`/api/skills/${encodeURIComponent(name)}/files${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  skillContent: (name: string) => get<{ content?: string }>(`/api/skills/${encodeURIComponent(name)}`).then((d) => d.content ?? ''),
  createSkill: (name: string, content: string) => post<{ ok: boolean }>('/api/skills', { name, content }),
  updateSkill: (name: string, content: string) => put<{ ok: boolean }>(`/api/skills/${encodeURIComponent(name)}`, { content }),
  deleteSkill: (name: string) => del(`/api/skills/${encodeURIComponent(name)}`),
  verifySkill: (name: string) => post<SkillIntegrity>(`/api/skills/${encodeURIComponent(name)}/verify`),
  // Skill proposals inbox (skill-evolution-proposal-only) — propose-only review.
  // ── Learning Flywheel §6.1: the Proposal Inbox + the staging week panel ──
  // `accept`/`reject` carry NO actor: the backend derives it from the request, because a caller that
  // could name itself `user` would make §7's human-installs gate decorative.
  learningProposals: (opts?: { kind?: string; tier?: string; flagged?: boolean }) => {
    const q = new URLSearchParams()
    if (opts?.kind) q.set('kind', opts.kind)
    if (opts?.tier) q.set('tier', opts.tier)
    if (opts?.flagged) q.set('flagged', '1')
    const qs = q.toString()
    return get<LearningInbox>(`/api/learning/proposals${qs ? `?${qs}` : ''}`)
  },
  learningProposal: (id: string) =>
    get<Record<string, unknown>>(`/api/learning/proposals/${encodeURIComponent(id)}`),
  acceptLearningProposal: (id: string) =>
    post<{ ok: boolean }>(`/api/learning/proposals/${encodeURIComponent(id)}/accept`, {}),
  // `del` is non-generic (it resolves void and throws ApiError on !ok) — measured against its own
  // signature rather than assumed symmetric with `get`/`post`.
  rejectLearningProposal: (id: string) =>
    del(`/api/learning/proposals/${encodeURIComponent(id)}`),
  learningStagingWeek: (days = 7) =>
    get<StagingWeek>(`/api/learning/staging/week?days=${days}`),
  learningHealth: (days = 7) =>
    get<LearningHealth>(`/api/learning/health?days=${days}`),
  skillProposals: () => get<{ proposals: SkillProposal[] }>('/api/skills/proposals').then((d) => d.proposals),
  skillProposalDetail: (id: string) => get<SkillProposalDetail>(`/api/skills/proposals/${encodeURIComponent(id)}`),
  acceptSkillProposal: (id: string, edits?: { description?: string; procedure_md?: string }) =>
    post<{ ok: boolean; name: string }>(`/api/skills/proposals/${encodeURIComponent(id)}/accept`, edits ?? {}),
  rejectSkillProposal: (id: string) => del(`/api/skills/proposals/${encodeURIComponent(id)}`),
  // Ephemeral session-skill drafts (skill-ephemeral-promotion).
  ephemeralSkills: (session: string) =>
    get<{ drafts: EphemeralDraft[] }>(`/api/skills/ephemeral/${encodeURIComponent(session)}`).then((d) => d.drafts),
  promoteEphemeralSkill: (session: string, payload: { slug: string; scope: 'agent' | 'global'; agent?: string; title?: string; body?: string }) =>
    post<{ ok: boolean; name: string; scope: string }>(`/api/skills/ephemeral/${encodeURIComponent(session)}/promote`, payload),
  discardEphemeralSkill: (session: string, slug: string) =>
    del(`/api/skills/ephemeral/${encodeURIComponent(session)}/${encodeURIComponent(slug)}`),
  skillMarketplaces: () => get<SkillMarketplace[]>('/api/skills/marketplaces'),
  // marketplace omitted → search across ALL marketplaces; pass one to scope.
  searchSkills: (q: string, marketplace?: string, limit = 30) =>
    get<{ results: SkillSearchResult[] }>(`/api/skills/search?q=${encodeURIComponent(q)}&limit=${limit}${marketplace ? `&marketplace=${encodeURIComponent(marketplace)}` : ''}`).then((d) => d.results),
  skillMarketplaceDetail: (id: string, marketplace = 'skills.sh') =>
    get<SkillMarketplaceDetail>(`/api/skills/marketplace/detail?id=${encodeURIComponent(id)}&marketplace=${encodeURIComponent(marketplace)}`),
  // Returns the parsed body on ANY HTTP status — the supply-chain scan verdict +
  // findings are carried in the 409 (overridable warning) / 403 (dangerous) body, so a
  // thrown error would discard exactly what the install UI needs to show. Mirrors the
  // app-install pattern (_installReq). Only a true network/parse failure yields ok:false.
  installSkill: async (id: string, marketplace = 'skills.sh', force = false): Promise<SkillInstallResult> => {
    try {
      const r = await fetch('/api/skills/install', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...SK },
        body: JSON.stringify({ id, marketplace, force }),
      })
      const data = await r.json().catch(() => null)
      if (data && typeof data === 'object') return { httpStatus: r.status, ...data } as SkillInstallResult
      return { ok: false, error: `HTTP ${r.status}`, httpStatus: r.status }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e), httpStatus: 0 }
    }
  },

  // tools
  tools: () => get<{ tools: ToolItem[] }>('/api/tools').then((d) => d.tools),
  // the generated self-description document (tools + routes + providers)
  manifest: () => get<Manifest>('/api/manifest'),
  // full catalog envelope incl. operator-visible load failures (broken providers/sources)
  toolsIndex: () => get<{ tools: ToolItem[]; load_failures?: ToolLoadFailure[] }>('/api/tools'),
  invokeTool: (tool: string, args: Record<string, unknown>, provider?: string) =>
    post<ToolInvokeResult>('/api/tools/invoke', { tool, arguments: args, provider }),
  mcpServers: () => get<McpServer[]>('/api/mcp'),
  toggleMcpServer: (name: string, enabled: boolean) => post('/api/mcp/toggle', { name, enabled }),
  toggleMcpTool: (server: string, tool: string, enabled: boolean) => post('/api/mcp/toggle-tool', { server, tool, enabled }),
  // Native-provider tool enable/disable (writes tool_prefs.json). 409 if locked.
  toggleTool: (provider: string, name: string, enabled: boolean) => post('/api/tools/toggle', { provider, name, enabled }),
  // Whole NATIVE tool-provider enable/disable (tool_prefs.json disabledProviders). 409 if platform-locked.
  toggleToolProvider: (provider: string, enabled: boolean) => post('/api/tools/provider-toggle', { provider, enabled }),
  mcpPoolStats: () => get<McpPoolStats>('/api/mcp/pool-stats'),
  probeMcp: () => post<{ ok?: boolean }>('/api/mcp/probe'),
  // Reconnect (re-probe) a SINGLE MCP server — recover one timed-out provider
  // without re-probing the whole fleet.
  reconnectMcp: (name: string) => post<McpServer>(`/api/mcp/probe/${encodeURIComponent(name)}`),
  toggleAllMcp: (enabled: boolean) => post('/api/mcp/toggle-all', { enabled }),
  // add/update an MCP server (stdio): writes ~/.personalclaw/mcp.json + enables.
  addMcpServer: (name: string, body: { command: string; args?: string[]; env?: Record<string, string> }) =>
    put<{ ok?: boolean; name: string }>(`/api/mcp/servers/${encodeURIComponent(name)}`, body),
  removeMcpServer: (name: string) => del(`/api/mcp/servers/${encodeURIComponent(name)}`),
  // Servers configured in an external backend (Claude Code) not yet in PClaw.
  importableMcp: () => get<{ servers: ImportableMcpServer[] }>('/api/mcp/importable').then((r) => r.servers),
  // Import a discovered server into ~/.personalclaw/mcp.json (PClaw scope).
  importMcpServer: (name: string) =>
    post('/api/mcp/apply', { changes: [{ name, personalclaw: true, globalMcp: false, ccGlobal: true }] }),

  // system / auth (shell status)
  system: () => get<SystemInfo>('/api/system'),
  authStatus: () => get<AuthStatus>('/api/auth-status'),

  // voice — STT/TTS resolve through the use-case BINDING (same as chat/embedding):
  // the active model is /api/models/active; provider-agnostic behavior
  // (enabled/language/speed) lives in per-use-case settings.
  useCaseSettings: (useCase: string) =>
    get<{ use_case: string; settings: Record<string, unknown> }>(`/api/models/use-cases/${encodeURIComponent(useCase)}/settings`).then((d) => d.settings),
  saveUseCaseSettings: (useCase: string, settings: Record<string, unknown>) =>
    put<{ ok: boolean; settings: Record<string, unknown> }>(`/api/models/use-cases/${encodeURIComponent(useCase)}/settings`, settings),

  // terminal (PTY)
  createTerminal: (cwd?: string) => post<{ session_id: string; shell?: string; cwd?: string }>('/api/terminal/sessions', cwd ? { cwd } : {}),
  terminalSessions: () => get<{ enabled?: boolean; sessions: Array<{ session_id: string; pid?: number; alive?: boolean; cols?: number; rows?: number; connected?: boolean; cwd?: string; shell?: string; label?: string }> }>('/api/terminal/sessions'),
  deleteTerminal: (id: string) => del(`/api/terminal/sessions/${encodeURIComponent(id)}`),

  // lifecycle triggers (projected onto the legacy HookItem shape the shared
  // Lifecycle* components consume). All route through the unified /api/triggers.
  hooks: () => get<{ triggers: Trigger[] }>('/api/triggers?type=lifecycle').then((d) => d.triggers.map(_triggerToHook)),
  actionProviders: () => get<{ providers: ActionProvider[] }>('/api/action-providers').then((d) => d.providers),
  createHook: (body: Record<string, unknown>) =>
    post<{ ok: boolean; trigger: Trigger }>('/api/triggers', {
      trigger_type: 'lifecycle', name: body.name, event: body.event, matcher: body.matcher,
      action: { provider: body.provider, config: body.provider_config ?? {} },
    }).then((r) => ({ ok: r.ok, hook: _triggerToHook(r.trigger) })),
  updateHook: (id: string, body: Record<string, unknown>) =>
    put<{ ok: boolean; trigger: Trigger }>(`/api/triggers/lifecycle:${encodeURIComponent(id)}`,
      'provider' in body || 'provider_config' in body
        ? { ...body, action: { provider: body.provider, config: body.provider_config ?? {} } }
        : body,
    ).then((r) => ({ ok: r.ok, hook: _triggerToHook(r.trigger) })),
  deleteHook: (id: string) => del(`/api/triggers/lifecycle:${encodeURIComponent(id)}`),
  toggleHook: (id: string) => post(`/api/triggers/lifecycle:${encodeURIComponent(id)}/toggle`, {}),
  testHook: (id: string, context?: string) => post<{ ok: boolean; result: { stdout: string; stderr: string; exit_code: number; error: string; duration_ms: number } }>(`/api/triggers/lifecycle:${encodeURIComponent(id)}/test`, { context: context ?? 'test' }),

  // store triggers — the unified TriggerStore kinds with no legacy backend
  // (file/web_watch/idle/…). Created via the automation_* chat tools; surfaced
  // here so the Automations page can list/pause/run/delete them. The raw_id is
  // itself <kind>:<slug>, so the namespaced route is `store:<raw_id>`.
  storeTriggers: () => get<{ triggers: Trigger[] }>('/api/triggers?type=store').then((d) => d.triggers),
  toggleStoreTrigger: (rawId: string, enabled: boolean) =>
    post(`/api/triggers/store:${encodeURIComponent(rawId)}/toggle`, { enabled }),
  deleteStoreTrigger: (rawId: string) => del(`/api/triggers/store:${encodeURIComponent(rawId)}`),
  runStoreTrigger: (rawId: string, dryRun = false) =>
    post<TriggerRunResult>(`/api/triggers/store:${encodeURIComponent(rawId)}/run`, dryRun ? { dry_run: true } : {}),
  // The `view` kind's render caller (WF2AUT-6). A render surface pings this as it mounts/refreshes;
  // any `view` trigger bound to `surface` and past its TTL refreshes (fire-and-forget on the
  // gateway), the rest serve cache. Deliberately NOT a poll — R10: a view trigger costs nothing
  // when nobody looks, so a render calls it rather than a background loop. Callers fire-and-forget
  // (`.catch(() => {})`) — a background refresh must never block or error a render.
  viewRender: (surface: string) =>
    post<{ refreshed: string[]; served_cache: { trigger_id: string; reason: string }[] }>(
      '/api/triggers/view/render', { surface }),

  // knowledge — typed item library + entities/graph + sources (see knowledge-entity-vision.md)
  knowledgeStats: () => get<KnowledgeStats>('/api/knowledge/stats'),
  knowledgeItems: (params?: { q?: string; type?: string; page?: number; limit?: number; includeArchived?: boolean }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.type) qs.set('type', params.type)
    if (params?.includeArchived) qs.set('include_archived', '1')
    qs.set('page', String(params?.page ?? 1)); qs.set('limit', String(params?.limit ?? 50))
    return get<{ items: KnowledgeItem[]; total: number; page: number; limit: number }>(`/api/knowledge/items?${qs}`)
  },
  // ── Lexicon / Vocabulary (core LEX.6) ──
  lexiconTerms: (opts: { source?: string; search?: string } = {}) =>
    get<{ terms: LexiconTerm[]; total: number }>(
      `/api/lexicon/terms?source=${encodeURIComponent(opts.source || '')}&search=${encodeURIComponent(opts.search || '')}`),
  lexiconAddTerm: (canonical: string, aliases?: string[]) =>
    post<{ ok: boolean; id: string }>('/api/lexicon/terms', { canonical, aliases: aliases || [] }),
  lexiconSetTermEnabled: (id: string, enabled: boolean) =>
    patch<{ ok: boolean }>(`/api/lexicon/terms/${encodeURIComponent(id)}`, { enabled }),
  lexiconDeleteTerm: (id: string) => del(`/api/lexicon/terms/${encodeURIComponent(id)}`),
  lexiconRebuild: () => post<{ ok: boolean; synced: number; total: number }>('/api/lexicon/rebuild'),
  lexiconCorrections: () => get<{ corrections: LexiconCorrection[] }>('/api/lexicon/corrections'),
  lexiconAddCorrection: (heard: string, meant: string, always = false) =>
    post<{ ok: boolean }>('/api/lexicon/corrections', { heard, meant, always }),
  lexiconSetCorrectionAuto: (id: string, auto_apply: boolean) =>
    patch<{ ok: boolean }>(`/api/lexicon/corrections/${encodeURIComponent(id)}`, { auto_apply }),
  lexiconReset: () => post<{ ok: boolean }>('/api/lexicon/reset'),

  knowledgeItem: (id: string) => get<KnowledgeItem>(`/api/knowledge/items/${encodeURIComponent(id)}`),
  knowledgeItemRelated: (id: string) => get<KnowledgeItem[]>(`/api/knowledge/items/${encodeURIComponent(id)}/related`),
  // Re-run the ingestion node-graph over a batch — scope 'missing' (un-enriched
  // items, default) or 'all'. Returns the count queued.
  regenerateKnowledgeIntelligence: (scope: 'missing' | 'all' = 'missing') =>
    post<{ queued: number; scope: string }>('/api/knowledge/regenerate-intelligence', { scope }),
  knowledgeEntityItems: (name: string) => get<KnowledgeItem[]>(`/api/knowledge/entities/by-name/${encodeURIComponent(name)}/items`),
  // Entities directly connected to this one in the graph (relation type + direction).
  knowledgeEntityRelated: (name: string) =>
    get<{ related: { name: string; entity_type?: string; relation_type: string; outgoing: boolean }[] }>(`/api/knowledge/entities/by-name/${encodeURIComponent(name)}/related`),
  generateKnowledgeIntelligence: (id: string) => post<KnowledgeItem>(`/api/knowledge/items/${encodeURIComponent(id)}/generate-intelligence`),
  // Bare URLs for <img>/<audio>/<video> src — auth rides the same-origin pc_token cookie.
  knowledgeItemFileUrl: (id: string) => `/api/knowledge/items/${encodeURIComponent(id)}/file`,
  knowledgeItemThumbnailUrl: (id: string) => `/api/knowledge/items/${encodeURIComponent(id)}/thumbnail`,
  // node-graph ingestion (#30): per-item extracted-content pool + live progress SSE.
  knowledgeExtracted: (id: string) => get<{ contents: ExtractedContent[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/extracted`),
  knowledgeIngestStreamUrl: (id: string) => `/api/knowledge/items/${encodeURIComponent(id)}/ingest/stream`,
  // The ingestion node-graph SHAPE for an item's type — for the mini-DAG progress view.
  knowledgeItemGraph: (id: string) => get<KnowledgeIngestGraph>(`/api/knowledge/items/${encodeURIComponent(id)}/graph`),
  // intent-driven ingestion (Tier 3): natural-language intents + by-value outcomes.
  knowledgeIntents: () => get<{ intents: KnowledgeIntent[] }>('/api/knowledge/intents'),
  // New intents omit id (the backend derives the slug from the goal); edits send it.
  upsertKnowledgeIntent: (body: Omit<KnowledgeIntent, 'id'> & { id?: string }) =>
    post<{ intents: KnowledgeIntent[]; id: string }>('/api/knowledge/intents', body),
  deleteKnowledgeIntent: (id: string) => del(`/api/knowledge/intents/${encodeURIComponent(id)}`),
  // Everything an intent has gathered (outcomes link back to source items by id).
  knowledgeIntentOutcomes: (id: string) =>
    get<{ intent: KnowledgeIntent; outcomes: IntentOutcome[] }>(`/api/knowledge/intents/${encodeURIComponent(id)}/outcomes`),
  // Retroactively run an intent against all already-ingested items.
  runKnowledgeIntent: (id: string) =>
    post<{ recorded: number; matched: number; new: number; errors: number; evaluated: number; outcomes: IntentOutcome[] }>(`/api/knowledge/intents/${encodeURIComponent(id)}/run`, {}),
  // Synthesize a reusable skill from what an intent has gathered (opt-in per click).
  generateSkillFromIntent: (id: string) =>
    post<{ skill: string; description: string }>(`/api/knowledge/intents/${encodeURIComponent(id)}/generate-skill`, {}),
  // The intents a given item contributed to (bidirectional link, item side).
  knowledgeItemIntents: (id: string) =>
    get<{ outcomes: IntentOutcome[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/intents`),
  createKnowledgeItem: (body: Record<string, unknown>) => post<KnowledgeItem>('/api/knowledge/items', body),
  updateKnowledgeItem: (id: string, body: Record<string, unknown>) => patch<{ ok: boolean }>(`/api/knowledge/items/${encodeURIComponent(id)}`, body),
  deleteKnowledgeItem: (id: string) => del(`/api/knowledge/items/${encodeURIComponent(id)}`),
  knowledgeProviders: () => get<{ providers: Array<{ name: string; display_name: string; always_on: boolean; kind: string }> }>('/api/knowledge/providers').then((d) => d.providers),
  // Distinct tags (frequency-ordered) for tag-input autocomplete.
  knowledgeTags: () => get<{ tags: string[] }>('/api/knowledge/tags').then((d) => d.tags),
  // ── Knowledge collections (KNOWLEDGE-LIBRARY S1) ──
  knowledgeCollections: () =>
    get<{ collections: KnowledgeCollection[] }>('/api/knowledge/collections').then((d) => d.collections),
  createKnowledgeCollection: (body: { name: string; kind?: 'manual' | 'smart'; query?: string; icon?: string }) =>
    post<{ ok: boolean; collection: KnowledgeCollection }>('/api/knowledge/collections', body),
  updateKnowledgeCollection: (id: string, body: { name?: string; kind?: 'manual' | 'smart'; query?: string; icon?: string; position?: number }) =>
    patch<{ ok: boolean; collection: KnowledgeCollection }>(`/api/knowledge/collections/${encodeURIComponent(id)}`, body),
  deleteKnowledgeCollection: (id: string) =>
    del(`/api/knowledge/collections/${encodeURIComponent(id)}`),
  knowledgeCollectionItems: (id: string, limit = 50) =>
    get<{ collection: KnowledgeCollection; items: KnowledgeItem[]; count: number }>(`/api/knowledge/collections/${encodeURIComponent(id)}/items?limit=${limit}`),
  addToKnowledgeCollection: (id: string, itemIds: string[]) =>
    post<{ ok: boolean; added: string[]; missing: string[] }>(`/api/knowledge/collections/${encodeURIComponent(id)}/items`, { item_ids: itemIds }),
  removeFromKnowledgeCollection: (id: string, itemId: string) =>
    del(`/api/knowledge/collections/${encodeURIComponent(id)}/items/${encodeURIComponent(itemId)}`),
  setKnowledgeReadState: (id: string, state: 'unread' | 'reading' | 'read') =>
    post<{ ok: boolean; read_state: string }>(`/api/knowledge/items/${encodeURIComponent(id)}/read-state`, { state }),
  setKnowledgeFavorited: (id: string, value: boolean) =>
    post<{ ok: boolean; favorited: boolean }>(`/api/knowledge/items/${encodeURIComponent(id)}/favorite`, { value }),
  // One curation op over many items. Per-item results, because a selection can go
  // stale between the click and the request — the UI reports "38 shelved, 2 not found"
  // rather than treating a partial success as a failure.
  knowledgeBulk: (op: KnowledgeBulkOp, itemIds: string[], args?: Record<string, unknown>) =>
    post<KnowledgeBulkResult>('/api/knowledge/bulk', { op, item_ids: itemIds, ...(args ?? {}) }),
  // Extracted text for a generated office document — powers the honest text preview
  // (never a fidelity render) beside the download.
  artifactExtractedText: (slug: string) =>
    get<{ slug: string; text: string; truncated: boolean }>(
      `/api/artifacts/${encodeURIComponent(slug)}/extract`),
  // ── Tag taxonomy (KNOWLEDGE-LIBRARY S2, T2.2) ──
  // Distinct from knowledgeTags() above, which stays a flat frequency-ordered string
  // list for ChipInput autocomplete. Every mutation returns the WHOLE tree so the
  // management surface never has to guess what a rename/merge/delete did to parents.
  knowledgeTagTree: () =>
    get<{ tags: KnowledgeTag[] }>('/api/knowledge/tag-tree').then((d) => d.tags),
  renameKnowledgeTag: (id: number, body: { name?: string; parent_id?: number | null }) =>
    patch<{ ok: boolean; tags: KnowledgeTag[] }>(`/api/knowledge/tags/${id}`, body),
  mergeKnowledgeTag: (id: number, into: number) =>
    post<{ ok: boolean; moved: number; already: number; tags: KnowledgeTag[] }>(
      `/api/knowledge/tags/${id}/merge`, { into }),
  // `del` is void-typed repo-wide, so the caller re-reads the tree rather than this
  // one method inventing a generic DELETE.
  deleteKnowledgeTag: (id: number) => del(`/api/knowledge/tags/${id}`),
  // ── Conflicts + typed relations (KNOWLEDGE-SYNTHESIS §3.2) ──
  // Read-only on purpose. Contradictions are flagged at ingest and BOTH claims are kept;
  // deciding which source to trust is the owner's judgement, so there is no resolve call
  // that would let the system settle one on its own.
  knowledgeConflicts: (limit = 100) =>
    get<{ conflicts: KnowledgeConflict[]; count: number }>(
      `/api/knowledge/conflicts?limit=${limit}`),
  knowledgeItemRelations: (id: string) =>
    get<{ outbound: KnowledgeItemRelation[]; inbound: KnowledgeItemRelation[] }>(
      `/api/knowledge/items/${encodeURIComponent(id)}/relations`),
  knowledgeEmbeddingStatus: () => get<{ enabled: boolean; available?: boolean; model?: string; total_items?: number; embedded_items?: number; stale_items?: number }>('/api/knowledge/embedding/status'),
  generateKnowledgeEmbeddings: (rebuild = false) => post<{ ok?: boolean; embedded?: number }>('/api/knowledge/embedding/generate', { rebuild }),
  // Every uploaded file → ONE logical-document item run through its node-graph.
  ingestKnowledgeFile: async (
    file: File,
    onProgress?: (p: { loaded: number; total: number; pct: number }) => void,
  ): Promise<{ item_id?: string; type?: string; status: string }> => {
    const { needsChunked, chunkedUpload } = await import('./chunkedUpload')
    if (await needsChunked(file)) {
      return chunkedUpload(file, { target: 'knowledge', onProgress })
    }
    const fd = new FormData(); fd.append('file', file)
    const r = await fetch('/api/knowledge/ingest', { method: 'POST', headers: { ...SK }, body: fd })
    if (!r.ok) throw new Error(await errText(r))
    return r.json()
  },

  // inbox — general triage entity over pluggable message-source providers
  inbox: (kind?: string) =>
    get<InboxItem[]>(kind ? `/api/inbox?kind=${encodeURIComponent(kind)}` : '/api/inbox'),
  // Kinds PRESENT in the store (not the whole enum) — a chip for an empty kind is a dead
  // control, so the backend drives the chip row from real data.
  inboxKinds: () => get<{ kinds: InboxKindCount[] }>('/api/inbox/kinds').then((d) => d.kinds),
  // Advance PENDING → SEEN. Omit both fields to mark everything; a resolved item is never
  // dragged backwards. Idempotent.
  markInboxSeen: (body: { ids?: string[]; kind?: string } = {}) =>
    post<{ ok: boolean; seen: number }>('/api/inbox/seen', body),
  inboxStatus: () => get<InboxStatus>('/api/inbox/status'),
  inboxProviders: () => get<{ providers: InboxProvider[] }>('/api/inbox/providers').then((d) => d.providers),
  updateInboxItem: (id: string, body: Record<string, unknown>) => put<InboxItem>(`/api/inbox/${encodeURIComponent(id)}`, body),
  draftInboxReply: (id: string) => post<InboxItem>(`/api/inbox/${encodeURIComponent(id)}/draft`),
  // Generate a catch-up digest of a channel's recent messages — lands as a new
  // inbox item (source="digest"), which arrives live over the WS.
  digestInboxChannel: (channelId: string, hours = 4) =>
    get<InboxItem>(`/api/inbox/digest?channel_id=${encodeURIComponent(channelId)}&hours=${hours}`),
  sendInboxReply: (id: string, text: string) => post<{ ok: boolean; delivered_to_session?: boolean }>('/api/inbox/send', { id, text }),
  // P11 engagement signals — recorded only when inbox.engagement_ranking_enabled is on
  // (backend gates it); open is best-effort fire-and-forget, favorite persists the star.
  openInboxItem: (id: string) => post<{ ok: boolean }>(`/api/inbox/${encodeURIComponent(id)}/open`),
  favoriteInboxItem: (id: string, favorited: boolean) =>
    post<{ ok: boolean; favorited: boolean }>(`/api/inbox/${encodeURIComponent(id)}/favorite`, { favorited }),
  dismissAllInbox: () => post<{ ok: boolean; dismissed: number }>('/api/inbox/dismiss-all'),
  restartInbox: () => post<{ ok: boolean; error?: string }>('/api/inbox/restart'),
  inboxSettings: () => get<{ settings: InboxSettings }>('/api/inbox/settings').then((d) => d.settings),
  saveInboxSettings: (s: Partial<InboxSettings>) => put<{ settings: InboxSettings }>('/api/inbox/settings', s),

  // audit log (SEL) — tamper-evident security-event chain.
  selEvents: (opts: { limit?: number; offset?: number } = {}) =>
    get<{ events: SelEvent[] }>(`/api/sel/events?limit=${opts.limit ?? 100}&offset=${opts.offset ?? 0}`).then((d) => d.events),
  selVerify: () => get<SelVerify>('/api/sel/verify'),
  selRotate: () => post<{ ok?: boolean }>('/api/sel/rotate'),
  // session archive (read-only browse)
  sessionArchives: () => get<{ archives: SessionArchive[] }>('/api/session/archive').then((d) => d.archives),
  // The read endpoint serves raw NDJSON text (application/x-ndjson), NOT a JSON
  // document — parse as text or every multi-line archive throws in r.json().
  sessionArchiveRead: (name: string) =>
    fetch(`/api/session/archive/${encodeURIComponent(name)}`, { headers: { ...SK } })
      .then(async (r) => { if (!r.ok) throw new ApiError(await errText(r), r.status); return r.text() }),
  // import / export (portable archive)
  portabilityExportUrl: () => '/api/portability/export',
  // One PROJECT as a manifest ZIP — narrower than the whole-home archive above, so a user can hand
  // a colleague a single project without shipping their memory database. Credentials never travel;
  // the response headers name the ones the far side must re-enter.
  projectExportUrl: (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/export`,
  projectImport: (file: File, opts: { preview?: boolean } = {}) => {
    const fd = new FormData(); fd.append('file', file)
    const qs = opts.preview ? '?preview=1' : ''
    return fetch(`/api/projects/import${qs}`, { method: 'POST', headers: { ...SK }, body: fd }).then(j<ProjectImportResult>)
  },
  // Both endpoints take a multipart upload of an export zip ('file' field).
  portabilityPreview: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return fetch('/api/portability/preview', { method: 'POST', headers: { ...SK }, body: fd }).then(j<PortabilityPreviewResult>)
  },
  portabilityImport: (file: File, mode: 'merge' | 'replace' = 'merge') => {
    const fd = new FormData(); fd.append('file', file)
    return fetch(`/api/portability/import?mode=${mode}`, { method: 'POST', headers: { ...SK }, body: fd }).then(j<PortabilityImportResult>)
  },
  // updates + changelog
  updateCheck: () => get<UpdateCheck>('/api/update/check'),
  changelog: () => get<{ content: string }>('/api/changelog').then((d) => d.content),
  applyUpdate: () => post<{ ok?: boolean; error?: string }>('/api/update'),
  // Cancel a running update / dismiss a stuck progress overlay (backend clears
  // its update_progress state so a reload doesn't resurrect it).
  cancelUpdate: () => post<{ ok?: boolean }>('/api/update/cancel'),
  setAutoUpdate: (enabled: boolean) => post<{ ok?: boolean }>('/api/update/auto', { enabled }),
  setUpdateDevMode: (enabled: boolean) => post<{ ok?: boolean }>('/api/update/dev-mode', { enabled }),
  // restart-only (no git pull) — apply committed backend changes.
  // probe first for the active-work count powering the confirm gate.
  restartProbe: () => post<{ ok: boolean; running_agents: number; sessions: number }>('/api/system/restart?probe=1'),
  restartGateway: () => post<{ ok?: boolean; status?: string; error?: string }>('/api/system/restart'),

  // settings entities
  notificationSettings: () => get<{ settings: NotificationSettings }>('/api/notifications/settings').then((d) => d.settings),
  saveNotificationSettings: (s: Partial<NotificationSettings>) => put<{ settings: NotificationSettings }>('/api/notifications/settings', s),
  // The full effective matrix: one row per REGISTERED kind, so a kind nobody has
  // customized still appears with its default rather than being invisible until edited.
  notificationRules: () => get<NotificationRulesDoc>('/api/notifications/rules'),
  // Merges: only the keys named in the body change. Rejects an unknown kind/mode/target
  // rather than persisting something the read path would silently ignore.
  saveNotificationRules: (body: { rules?: Record<string, NotificationRulePatch>; digest?: { schedule?: string } }) =>
    put<NotificationRulesDoc & { ok: boolean }>('/api/notifications/rules', body),
  memorySettings: () => get<MemorySettings>('/api/memory/settings'),
  saveMemorySettings: (s: Partial<MemorySettings>) => put<MemorySettings>('/api/memory/settings', s),
  /** The push reflex's report card (MEMORY-GRAPH-AND-VAULT §3). */
  memoryVolunteerStats: (windowDays?: number) =>
    get<VolunteerStats>(`/api/memory/volunteer-stats${windowDays ? `?window_days=${windowDays}` : ''}`),
  memoryStats: () => get<MemoryStats>('/api/memory/stats'),
  // memory vault (Obsidian markdown mirror) — status + on-demand sync.
  memoryVaultStatus: () => get<MemoryVaultStatus>('/api/memory/vault'),
  syncMemoryVault: () => post<MemoryVaultSyncResult>('/api/memory/vault/sync', {}),
  // daily-digest nodes (mem-tree) — per-day rollups; rebuild=1 forces a build.
  dailyDigests: (rebuild = false) =>
    get<{ digests: DailyDigest[] }>(`/api/memory/daily-digests${rebuild ? '?rebuild=1' : ''}`).then((d) => d.digests),
  // memory explorer — semantic browse/CRUD, episodic search/list/delete, audit, inspector, consolidate.
  memorySemantic: () => get<{ entries: SemanticEntry[] }>('/api/memory/semantic').then((d) => d.entries),
  writeSemantic: (key: string, value: unknown) => put<{ ok?: boolean }>('/api/memory/semantic', { key, value }),
  deleteSemantic: (key: string) => del(`/api/memory/semantic/${encodeURIComponent(key)}`),
  memoryEpisodic: (opts: { offset?: number; limit?: number; tags?: string } = {}) =>
    get<{ entries: EpisodicEntry[] }>(`/api/memory/episodic?limit=${opts.limit ?? 50}&offset=${opts.offset ?? 0}${opts.tags ? `&tags=${encodeURIComponent(opts.tags)}` : ''}`).then((d) => d.entries),
  searchEpisodic: (q: string, tags?: string) =>
    get<{ entries: EpisodicEntry[] }>(`/api/memory/episodic/search?q=${encodeURIComponent(q)}${tags ? `&tags=${encodeURIComponent(tags)}` : ''}`).then((d) => d.entries),
  deleteEpisodic: (id: string) => del(`/api/memory/episodic/${encodeURIComponent(id)}`),
  memoryEvents: (opts: { offset?: number; limit?: number } = {}) =>
    get<{ events: MemoryEvent[] }>(`/api/memory/events?limit=${opts.limit ?? 50}&offset=${opts.offset ?? 0}`).then((d) => d.events),
  undoMemoryEvent: (eventId: number) =>
    post<{ ok: boolean; message: string }>(`/api/memory/events/${eventId}/undo`, {}),
  memoryContextPreview: (q: string) => get<MemoryContextPreview>(`/api/memory/context-preview?q=${encodeURIComponent(q)}`),
  // consolidate fires a rollup for a session key (the handler expects `key`).
  consolidateMemory: (key: string) => post<{ ok?: boolean; key?: string; error?: string }>('/api/memory/consolidate', { key }),
  securityStats: () => get<SecurityStats>('/api/security/stats'),
  deniedCommands: () => get<DeniedCommands>('/api/security/denied-commands'),
  setUserDeniedCommands: (patterns: string[]) => patch<Record<string, any>>('/api/config/personalclaw', { path: 'security.denied_commands', value: patterns }),
  securityEgress: () => get<EgressPolicyConfig>('/api/security/egress'),
  // DC-2. The desktop shell's pushed capability manifest. In a browser tab this is
  // `{connected: false, capabilities: {}}` — an EMPTY map, not the capability names
  // with a placeholder state, so no surface can render a grant control for something
  // the gateway cannot deliver.
  desktopState: () => get<DesktopStateWire>('/api/desktop/state'),
  setSecurityEgress: (cfg: EgressPolicyConfig) => patch<Record<string, any>>('/api/config/personalclaw', { path: 'security.egress', value: cfg }),
  // Tool-output projection rules (TokenJuice OP6). Read from the whole-config GET
  // (tools.projection_rules); written via the config PATCH allowlist.
  projectionRules: () => get<Record<string, any>>('/api/config/personalclaw').then(
    (c) => ((c?.tools?.projection_rules ?? []) as ProjectionRule[])),
  setProjectionRules: (rules: ProjectionRule[]) => patch<Record<string, any>>('/api/config/personalclaw', { path: 'tools.projection_rules', value: rules }),
  // TokenJuice savings (counterfactual) summary — estimated tokens saved by output
  // projection this month, top compressor, per-compressor breakdown (§1.3).
  toolsSavings: () => get<ToolsSavings>('/api/tools/savings'),
  // Tool groups (Context Economy §5): the derived partition + per-surface
  // activation defaults. Read-only — the flag and defaults are config writes.
  toolGroups: () => get<ToolGroupsData>('/api/tools/groups'),
  setToolGroupsEnabled: (enabled: boolean) =>
    patch<Record<string, any>>('/api/config/personalclaw', { path: 'tools.groups_enabled', value: enabled }),
  // Feedback Signal (plan 58): 👍/👎 on AI judgment outputs + per-producer accuracy.
  recordFeedback: (body: FeedbackRecordBody) => post<{ ok: boolean; id: string; verdict: string }>('/api/feedback', body),
  feedbackTarget: (kind: FeedbackTargetKind, id: string) =>
    get<{ verdict: 'up' | 'down' | null; reason?: string }>(`/api/feedback/target/${kind}/${encodeURIComponent(id)}`),
  feedbackProducers: (windowDays?: number) =>
    get<FeedbackProducersResponse>(`/api/feedback/producers${windowDays ? `?window_days=${windowDays}` : ''}`),
  feedbackSnooze: (producer: FeedbackProducer) => post<{ ok: boolean }>('/api/feedback/producers/snooze', producer),
  feedbackClear: (producer: FeedbackProducer) => post<{ ok: boolean }>('/api/feedback/producers/clear', producer),
  // Investigate Anywhere (plan 60): server-composed context envelope + staged session.
  investigate: (body: { kind: string; id: string; back_link?: string }) =>
    post<{ session_key: string; context: InvestigateOrigin & { snapshot: string; opening_prompt?: string } }>('/api/investigate', body),

  // upload (multipart — no JSON headers)
  // Extracted text content for an uploaded attachment (what the agent saw) — used
  // by the chat attachment-chip preview. Awaits the upload-time extraction.
  attachmentExtract: (path: string) => get<{ name: string; text: string }>(`/api/attachment-extract?path=${encodeURIComponent(path)}`),
  uploadFiles: async (
    files: File[],
    onProgress?: (fileIndex: number, p: { loaded: number; total: number; pct: number }) => void,
    signal?: AbortSignal,
  ): Promise<{ paths: string[]; error?: string }> => {
    const { needsChunked, chunkedUpload } = await import('./chunkedUpload')
    const paths: string[] = []
    // Small files (below the server threshold) → one multipart POST (unchanged).
    const small: File[] = []
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      if (await needsChunked(f)) {
        const res = await chunkedUpload(f, { target: 'attachment', onProgress: (p) => onProgress?.(i, p), signal })
        if (res?.paths) paths.push(...res.paths)
      } else {
        small.push(f)
      }
    }
    if (small.length) {
      const fd = new FormData()
      small.forEach((f) => fd.append('file', f))
      const r = await fetch('/api/upload/file', { method: 'POST', headers: { ...SK }, body: fd, signal })
      const data = await j<{ paths: string[]; error?: string }>(r)
      if (data.paths) paths.push(...data.paths)
    }
    return { paths }
  },

  // ── files (the workspace explorer) — endpoints are /api/file-* (singular) ──
  fileRoots: () => get<FileListResp>('/api/file-list'),
  fileList: (path: string) => get<FileListResp>(`/api/file-list?path=${encodeURIComponent(path)}`),
  fileRead: (path: string, resolve = false) => fetch(`/api/file-read?path=${encodeURIComponent(path)}${resolve ? '&resolve=1' : ''}`, { headers: { ...SK } }).then(async (r) => {
    if (!r.ok) throw new ApiError(await errText(r), r.status)  // ApiError carries .status so the viewer can tell a 404 (file gone → close the stale tab) from a transient 5xx (offer retry)
    // X-Binary: the server detected non-text content (NUL bytes) — don't treat the
    // empty body as an editable file; the viewer shows a binary placeholder.
    return { content: await r.text(), truncated: r.headers.get('X-Truncated') === 'true', binary: r.headers.get('X-Binary') === 'true' }
  }),
  fileWrite: (path: string, content: string) => post<{ ok: boolean }>('/api/file-write', { path, content }),
  fileCreate: (parent: string, name: string, kind: 'file' | 'dir', content?: string) =>
    post<{ ok: boolean; path: string; is_dir: boolean }>('/api/file-create', { path: parent, name, kind, content }),
  fileMove: (src: string, dest: string) => post<{ ok: boolean; path: string }>('/api/file-move', { src, dest }),
  fileDelete: (path: string) => post<{ ok: boolean }>('/api/file-delete', { path }),
  fileUpload: async (
    dir: string, files: File[],
    onProgress?: (fileIndex: number, p: { loaded: number; total: number; pct: number }) => void,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; paths?: string[]; error?: string }> => {
    const { needsChunked, chunkedUpload } = await import('./chunkedUpload')
    const paths: string[] = []
    const small: File[] = []
    try {
      for (let i = 0; i < files.length; i++) {
        const f = files[i]
        if (await needsChunked(f)) {
          const res = await chunkedUpload(f, { target: 'workspace', path: dir, onProgress: (p) => onProgress?.(i, p), signal })
          if (res?.paths) paths.push(...res.paths)
        } else {
          small.push(f)
        }
      }
      if (small.length) {
        const fd = new FormData()
        for (const f of small) fd.append('file', f, f.name)
        const r = await fetch(`/api/file-upload?path=${encodeURIComponent(dir)}`, { method: 'POST', headers: { ...SK }, body: fd, signal })
        const data = await r.json().catch(() => ({}))
        if (!r.ok) return { ok: false, error: data?.error || `HTTP ${r.status}` }
        if (data?.paths) paths.push(...data.paths)
      }
    } catch (e) {
      // A user cancel must propagate (so the caller clears silently), not become a
      // {ok:false} error result that renders as an "Upload failed" banner.
      const { isAbortError } = await import('./chunkedUpload')
      if (isAbortError(e)) throw e
      return { ok: false, error: (e as Error).message }
    }
    return { ok: true, paths }
  },
  fileGitStatus: (path: string) => get<GitStatusResp>(`/api/file-git-status?path=${encodeURIComponent(path)}`),
  /** Recent commits for the repo containing `path` (newest first). */
  fileGitLog: (path: string, limit = 20) =>
    get<{ repoRoot: string; commits: { hash: string; subject: string; relative: string; author: string }[] }>(`/api/file-git-log?path=${encodeURIComponent(path)}&limit=${limit}`),
  /** One commit's unified diff (git show), for reviewing what a stage changed. */
  fileGitCommit: (path: string, hash: string) =>
    get<{ repoRoot: string; hash: string; subject: string; diff: string; truncated?: boolean; found?: boolean }>(`/api/file-git-commit?path=${encodeURIComponent(path)}&hash=${encodeURIComponent(hash)}`),
  /** Committed (HEAD) contents of a file, for a working-vs-HEAD diff. exists=false → newly added. */
  fileGitOriginal: (path: string) => get<{ content: string; exists: boolean; truncated?: boolean }>(`/api/file-git-original?path=${encodeURIComponent(path)}`),
  fileContentSearch: (path: string, q: string, include?: string) =>
    get<ContentSearchResp>(`/api/file-content-search?path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}${include ? `&include=${encodeURIComponent(include)}` : ''}`),
  // fuzzy filename search for the @-mention picker → {results:[{path,name,size,mtime}], root}
  fileSearch: (q: string, project?: string) =>
    get<{ results: { path: string; name: string; size: number; mtime: number }[]; root?: string }>(`/api/file-search?q=${encodeURIComponent(q)}${project ? `&project=${encodeURIComponent(project)}` : ''}`),
  fileComplete: (path: string, kind?: 'dir') =>
    get<{ suggestions: FsEntry[] }>(`/api/file-complete?path=${encodeURIComponent(path)}${kind ? `&kind=${kind}` : ''}`),
  /** Directory navigator (for the Code workspace picker): list subdirs of a path
   *  (empty → home). Walks arbitrary non-sensitive dirs, unlike file-list. */
  browseDirs: (path?: string) =>
    get<{ path: string; parent: string; in_repo?: boolean; dirs: { name: string; path: string; is_repo?: boolean }[] }>(`/api/browse-dirs${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  /** Create an arbitrary directory (greenfield workspace). */
  createDir: (path: string) => post<{ ok: boolean; path: string }>('/api/create-dir', { path }),
  /** Raw URL for binary serve (images/pdf/svg/video) — used as an <img>/<object>/download src.
   *  `resolve` lets a relative chat file-mention serve its raw bytes from the resolved
   *  workspace path (matches fileRead's resolve — else media/binary 400 while text loads). */
  fileRawUrl: (path: string, resolve = false) => `/api/file-raw?path=${encodeURIComponent(path)}${resolve ? '&resolve=1' : ''}`,
  /** SSE URL for live content watch (resolve=1 lets relative chat mentions watch too). */
  fileWatchUrl: (path: string, resolve = false) => `/api/file-watch?path=${encodeURIComponent(path)}${resolve ? '&resolve=1' : ''}`,
  /** SSE URL for out-of-band config-tree changes (config.json/agents/skills/workflows). */
  configFsStreamUrl: () => `/api/config-fs/stream`,

  // ── workflows v2 (composable runs over a node DAG) ──
  // Every method hits `/api/workflows`, which the backend serves from the SAME
  // `workflows.service` the chat tools call — so the UI and an agent can never disagree
  // about what an operation does.
  workflowDefs: (f?: { tag?: string; source?: string }) => {
    const qs = new URLSearchParams(Object.entries(f ?? {}).filter(([, v]) => v) as [string, string][]).toString()
    return get<{ defs: WorkflowDefSummary[]; total: number }>(`/api/workflows${qs ? `?${qs}` : ''}`)
  },
  /** Templates with freshness, scope, packs and doctor findings. A SEPARATE call from
   *  `workflowDefs` on purpose: this one costs a run-history read per def, and the picker that
   *  only needs names should not pay it. */
  workflowSurfacing: () =>
    get<{ defs: WorkflowSurfacingRow[]; total: number; findings: WorkflowSurfacingFinding[] }>(
      '/api/workflows/surfacing',
    ),
  workflowDef: (name: string) =>
    get<{ definition: WorkflowDef; provider: string }>(`/api/workflows/${encodeURIComponent(name)}`),
  saveWorkflowDef: (body: { name: string; root: WorkflowNode; description?: string; inputs?: Record<string, unknown>; tags?: string[]; metadata?: Record<string, unknown>; save?: boolean }) =>
    post<{ saved: boolean; definition?: WorkflowDef; valid: boolean; issues: Array<{ code: string; message: string; path?: string; severity?: string }>; levels?: string[][] }>('/api/workflows', body),
  deleteWorkflowDef: (name: string) => del(`/api/workflows/${encodeURIComponent(name)}`),

  workflowRuns: (f?: { workflow?: string; status?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams(
      Object.entries(f ?? {}).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]),
    ).toString()
    return get<{ runs: WorkflowRunSummary[]; total: number; limit: number; offset: number }>(`/api/workflows/runs${qs ? `?${qs}` : ''}`)
  },
  startWorkflowRun: (body: { name: string; inputs?: Record<string, unknown>; mode?: 'blocking' | 'background'; project_id?: string; idempotency_key?: string }) =>
    post<{ run_id: string; status: string; blocking?: boolean; needs_input?: WorkflowContinuation[] }>('/api/workflows/runs', body),
  workflowRun: (id: string) => get<WorkflowRunDetailData>(`/api/workflows/runs/${encodeURIComponent(id)}`),
  /** Resolve a pending confirmation by VERB — the backend the DagView's Approve/Deny binds to.
   *  Separate from `resumeWorkflowRun` because the verb vocabulary is the point: an unknown verb is
   *  REFUSED server-side rather than treated as a reject, so a typo cannot silently decline work the
   *  user meant to allow. */
  confirmWorkflowRun: (id: string, body: { verb: 'approve' | 'reject' | 'skip' | 'quit'; resume_token?: string; note?: string }) =>
    post<{ ok?: boolean; verb?: string; approved?: boolean; resumed?: boolean; still_pending?: boolean; code?: string; message?: string }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/confirm`,
      body,
    ),
  workflowRunOutput: (id: string, nodeId: string) =>
    get<{ run_id: string; node_id: string; instance_path: string; state: string; output: unknown }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/outputs/${encodeURIComponent(nodeId)}`),
  /** The §5 reconstructability set for one TERMINAL node (WF2-A2) — resolved prompt, inputs,
   *  output, attempts, this node's ledger slice, and whether it was served from cache. Every
   *  text field arrives redacted. WV-10's inspector drawer consumes this. */
  workflowRunNodeInspect: (runId: string, nodeId: string) =>
    get<NodeInspect>(
      `/api/workflows/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/inspect`),
  workflowContinuations: (id: string) =>
    get<{ continuations: WorkflowContinuation[] }>(`/api/workflows/runs/${encodeURIComponent(id)}/continuations`),
  /** The run's workspace review: changed files + the two reintegration verbs (§4.1). A GET
   *  because reintegration is OFFERED, never performed — there is no companion POST, and that
   *  is the plan's ruling rather than a gap. 404s for an unknown run; a run with no managed
   *  workspace answers with an empty `workspace`, which the panel renders as "no diff". */
  workflowRunWorkspace: (id: string) =>
    get<WorkflowWorkspaceReview>(`/api/workflows/runs/${encodeURIComponent(id)}/workspace`),
  workflowRunOutbox: (id: string) =>
    get<{ files: WorkflowOutboxEntry[] }>(`/api/workflows/runs/${encodeURIComponent(id)}/outbox`),
  /** The §6.4 nine-question introspection projection (WORK-CONTAINERS R6).
   *
   *  ONE call rather than five, because the checklist is a property of the whole surface: the
   *  backend's `checklist_gaps` can only name a hole in a payload it sees in full, and five
   *  routes would let this panel render eight answers and never learn the ninth was missing. */
  workflowRunIntrospect: (id: string) =>
    get<WorkflowIntrospection>(`/api/workflows/runs/${encodeURIComponent(id)}/introspect`),
  workflowRunDropStatus: (id: string) =>
    get<WorkflowDropStatus>(`/api/workflows/runs/${encodeURIComponent(id)}/drop`),
  /** Drop files into a run (WORK-CONTAINERS §2.5). `confirm` ANSWERS the approval gate — the first
   *  call is deliberately made WITHOUT it so the 428 comes back carrying what would be accepted
   *  (name, size, MIME) for the operator to see before anything lands. */
  workflowRunDrop: (id: string, files: File[], confirm = false) => {
    const body = new FormData()
    for (const f of files) body.append('file', f)
    return fetch(
      `/api/workflows/runs/${encodeURIComponent(id)}/drop${confirm ? '?confirm=true' : ''}`,
      { method: 'POST', headers: { ...SK }, body },
    ).then(j<WorkflowDropStatus>)
  },
  // `preview_only` computes the cascade and queues NOTHING — the what-if a user sees
  // before accepting an edit that would re-run completed work.
  editWorkflowRun: (id: string, body: { ops: Array<Record<string, unknown>>; expect_version?: number; confirm_cascade?: boolean; preview_only?: boolean }) =>
    post<{ ok?: boolean; queued?: boolean; preview: WorkflowCascadePreview; issues: Array<{ code: string; message: string; node_id?: string }> }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/edit`, body),
  cancelWorkflowRun: (id: string) => post<{ run_id: string; cancel_requested: boolean }>(`/api/workflows/runs/${encodeURIComponent(id)}/cancel`),
  // Refused with a 409 while the run can still move: cancel and delete are two different
  // intents, and one button doing both would delete work a user only meant to stop.
  deleteWorkflowRun: (id: string) => del(`/api/workflows/runs/${encodeURIComponent(id)}`),
  pauseWorkflowRun: (id: string) => post<{ run_id: string; pause_requested: boolean }>(`/api/workflows/runs/${encodeURIComponent(id)}/pause`),
  // Mid-run steering (LOOPS-EVOLUTION R14): queued, then consumed at the next iteration
  // boundary. Queued rather than applied because injecting mid-iteration races the
  // worker's own state — and because the alternative today is cancel-and-restart, which
  // throws away the cycle context that made steering worth doing.
  steerWorkflowRun: (id: string, body: { text: string }) =>
    post<{ ok?: boolean; run_id?: string; queued?: number; error?: { code: string; message: string } }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/steer`, body),
  // Shown as pending in the UI: a queued instruction the user cannot see is
  // indistinguishable from one that was dropped, and they will queue it again.
  workflowSteering: (id: string) =>
    get<{ run_id: string; pending: Array<{ text: string; queued_at: string }>; count: number }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/steering`),
  resumeWorkflowRun: (id: string, body: { answer?: unknown; resume_token?: string; always_allow?: boolean }) =>
    post<{ ok?: boolean; approved?: boolean; node_id?: string; resumed?: boolean }>(`/api/workflows/runs/${encodeURIComponent(id)}/resume`, body),
  rewindWorkflowRun: (id: string, body: { node_id: string; redo_effects?: boolean; force?: boolean }) =>
    post<{ ok?: boolean; preview: WorkflowCascadePreview }>(`/api/workflows/runs/${encodeURIComponent(id)}/rewind`, body),
  workflowRunFrom: (id: string, body: { node_id: string }) =>
    post<{ ok?: boolean; preview: WorkflowCascadePreview }>(`/api/workflows/runs/${encodeURIComponent(id)}/run-from`, body),
  forkWorkflowRun: (id: string, body?: { checkpoint_id?: string; note?: string }) =>
    post<{ child_run_id: string; fork_axis: string; shared_axes: string[]; isolation_notes: string[] }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/fork`, body ?? {}),
  workflowAudit: (dryRun = true) =>
    get<{ healthy: boolean; dry_run: boolean; runs_scanned: number; counts: Record<string, number>; findings: Array<{ kind: string; run_id: string; detail: string; heal: string; healed: boolean }> }>(
      `/api/workflows/audit?dry_run=${dryRun ? 'true' : 'false'}`),
  workflowManifest: () => get<WorkflowManifest>('/api/workflows/manifest'),
  // Bare URL for EventSource — auth rides the same-origin cookie, as with every other
  // per-resource stream.
  workflowRunStreamUrl: (id: string) => `/api/workflows/runs/${encodeURIComponent(id)}/events`,

  // ── artifacts (named, versioned content — a curated subset of files) ──
  artifacts: (f?: { tag?: string; kind?: string; q?: string; source?: string; source_path?: string }) => {
    const qs = new URLSearchParams(Object.entries(f ?? {}).filter(([, v]) => v) as [string, string][]).toString()
    return get<{ artifacts: Artifact[] }>(`/api/artifacts${qs ? `?${qs}` : ''}`).then((d) => d.artifacts)
  },
  artifact: (slug: string) => get<Artifact>(`/api/artifacts/${encodeURIComponent(slug)}`),
  /** The dashboard pin list (WORK-CONTAINERS §6.5d). REFERENCES only — a slug plus when it was
   *  pinned. The widget resolves each slug against the artifact list, which is also how a DELETED
   *  artifact self-heals off the surface rather than leaving a broken card. */
  pinnedArtifacts: () => get<{ pins: PinnedArtifact[] }>('/api/artifacts/pinned'),
  /** Pin or unpin. ONE route for both directions: it is one piece of state with two values, and
   *  two endpoints would be two places to keep the cap and dedup rules right. */
  pinArtifact: (slug: string, pinned: boolean, runId = '') =>
    post<{ ok: boolean; pinned: boolean; pins: PinnedArtifact[] }>(
      `/api/artifacts/${encodeURIComponent(slug)}/pin`,
      { pinned, run_id: runId },
    ),
  // Existence check that returns 200 {exists} (no 404) — for "is this saved?"
  // probes that shouldn't spam the console with expected not-founds.
  artifactExists: (slug: string) =>
    get<{ exists: boolean }>(`/api/artifacts/${encodeURIComponent(slug)}?probe=1`).then((d) => d.exists),
  createArtifact: (body: { name: string; content: string; kind?: string; source?: string; source_path?: string; description?: string; tags?: string[]; slug?: string; project_id?: string }) =>
    post<Artifact>('/api/artifacts', body),
  updateArtifact: (slug: string, body: Record<string, unknown>) => patch<Artifact>(`/api/artifacts/${encodeURIComponent(slug)}`, body),
  deleteArtifact: (slug: string) => del(`/api/artifacts/${encodeURIComponent(slug)}`),
  // Re-run image generation for a deleted/missing inline image AT THE SAME SLUG
  // (recovers the original prompt from the session's tool history) so the chat
  // transcript's existing /raw ref resolves again — no new chat message.
  regenerateArtifactImage: (slug: string, body: { session?: string; prompt?: string }) =>
    post<{ ok: boolean; slug: string }>(`/api/artifacts/${encodeURIComponent(slug)}/regenerate`, body),
  artifactVersions: (slug: string) => get<{ slug: string; versions: number[] }>(`/api/artifacts/${encodeURIComponent(slug)}/versions`),
  artifactVersion: (slug: string, n: number) => get<Artifact>(`/api/artifacts/${encodeURIComponent(slug)}/versions/${n}`),
  artifactEvents: (slug: string) => get<{ slug: string; events: ArtifactEvent[] }>(`/api/artifacts/${encodeURIComponent(slug)}/events`),

  // ── dashboard-as-views registry (AMBIENT-SURFACES §1 / A2-1) ──
  // Presets are read-only: updateView/deleteView on a preset return 403. Pinning
  // (pinTile) POSTs an artifact:<slug> tile; resolveTile accepts (keep) or removes
  // (dismiss/unpin) an overlay tile.
  dashboardViews: () => get<{ views: DashboardView[] }>('/api/dashboard/views').then((d) => d.views),
  createView: (body: { name: string; icon?: string }) => post<{ view: DashboardView }>('/api/dashboard/views', body).then((d) => d.view),
  updateView: (id: string, body: Partial<Pick<DashboardView, 'name' | 'icon' | 'nav_pinned'>>) =>
    put<{ view: DashboardView }>(`/api/dashboard/views/${encodeURIComponent(id)}`, body).then((d) => d.view),
  deleteView: (id: string) => del(`/api/dashboard/views/${encodeURIComponent(id)}`),
  pinTile: (viewId: string, body: { slug: string; size?: TileSize }) =>
    post<{ view: DashboardView }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles`, body).then((d) => d.view),
  resolveTile: (viewId: string, body: { ref: string; keep: boolean }) =>
    post<{ view: DashboardView }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/resolve`, body).then((d) => d.view),

  // App Platform (A7) — install/manage apps that extend PClaw.
  // Normalize the app-category flag at the boundary: `native` is the single source
  // of truth downstream. A gateway that hasn't restarted yet still emits the legacy
  // `platform` flag for always-on providers — fold it into `native` here so the whole
  // UI stays pure-`native` and is correct against both old and new backends. (The
  // `platform` field is dropped from AppSummary; this coercion is the only place that
  // still reads it, and can be deleted once every gateway ships `native`.)
  apps: () => get<{ apps: (AppSummary & { platform?: boolean })[] }>('/api/apps')
    .then((d) => d.apps.map((a) => (a.native ?? a.platform) ? { ...a, native: true } : a)),
  app: (name: string) => get<AppDetail>(`/api/apps/${encodeURIComponent(name)}`),
  // install/update return the InstallResult body on ANY status (the scan report +
  // needs_consent ride in the 400/409 body, so we must NOT throw on non-2xx —
  // the modal needs them to render findings + the consent flow). Network failures
  // still surface as a thrown error with ok:false.
  installApp: (source: string, confirm = false) => _installReq('/api/apps', { source, confirm }),
  updateApp: (name: string, source: string, confirm = false) =>
    _installReq(`/api/apps/${encodeURIComponent(name)}/update`, { source, confirm }),
  enableApp: (name: string) => post<{ ok: boolean }>(`/api/apps/${encodeURIComponent(name)}/enable`),
  disableApp: (name: string) => post<{ ok: boolean }>(`/api/apps/${encodeURIComponent(name)}/disable`),
  // Uninstall = deactivate (keep files); force=true removes files from disk.
  uninstallApp: (name: string, force = false) =>
    del(`/api/apps/${encodeURIComponent(name)}${force ? '?force=1' : ''}`),
  appUninstallPreview: (name: string) =>
    get<{ name: string; dependencies: AppDepClassification[] }>(`/api/apps/${encodeURIComponent(name)}/uninstall-preview`),
  appConfig: (name: string) =>
    get<{ name: string; config: Record<string, unknown>; schema: Record<string, unknown>; _secret_set?: string[] }>(`/api/apps/${encodeURIComponent(name)}/config`),
  saveAppConfig: (name: string, config: Record<string, unknown>) =>
    put<{ ok: boolean; config: Record<string, unknown> }>(`/api/apps/${encodeURIComponent(name)}/config`, config),
  // Store catalog: available-to-install apps (bundled-not-installed + git sources).
  appCatalog: () => get<{ bundled: AppCatalogEntry[]; gitSources: string[]; localSources?: string[]; firstPartySources?: string[]; localApps?: AppCatalogEntry[]; remoteApps?: AppCatalogEntry[]; gitApps?: AppCatalogEntry[] }>('/api/apps/catalog'),
  appSources: () => get<{ sources: string[] }>('/api/apps/sources').then((d) => d.sources),
  addAppSource: (url: string) => post<{ ok: boolean; sources: string[] }>('/api/apps/sources', { url }),
  removeAppSource: (url: string) => del(`/api/apps/sources?url=${encodeURIComponent(url)}`),
  // Local-directory app sources (a dir of app subdirs; its apps surface in the Store).
  addLocalAppSource: (path: string) => post<{ ok: boolean; sources: string[] }>('/api/apps/local-sources', { path }),
  removeLocalAppSource: (path: string) => del(`/api/apps/local-sources?path=${encodeURIComponent(path)}`),
}
