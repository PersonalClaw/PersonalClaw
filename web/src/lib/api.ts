// PersonalClaw API client (web). Matches the real backend contract:
// root-relative /api paths, X-Session-Key header on every call, same-origin
// (cookie pc_token_<port> rides along via the dev proxy). See the composer
// API contract in docs.

import { apiVersionHeaders } from './apiVersion'
import { errEnvelope, errText } from './errText'

// Every request helper below spreads `SK`, so folding the API-version declaration
// into it is the SPA's ONE declaration site (PL-9): the number lives only in
// `apiVersion.ts`, no call site carries it, and a gateway outside this bundle's
// supported window answers `400 api_version_unsupported` instead of failing later
// at a field that quietly changed shape.
const SK = { 'X-Session-Key': 'dashboard:ui', ...apiVersionHeaders }

/** An Error that carries the HTTP status AND the backend's typed error code, so callers can
 *  distinguish a genuine 404 (resource gone) from a transient network/5xx blip, and — within
 *  one status — which of several deliberate 404s this is. `.message` is unchanged (the
 *  backend's error text), so existing `catch(e => e.message)` callers are unaffected; only
 *  callers that branch read `.status`/`.code`.
 *
 *  🔑 `.code` is the field the panels needed and did not have. `http_errors.py` mints a
 *  DISTINCT code per meaning — `/api/evals/ablation` answers 404 as `evals_disabled` (the
 *  switch is off) or `ablation_absent` (nothing has run) — and the status alone cannot tell
 *  them apart. Before this, a panel's only handle was the human sentence, which never
 *  contains the code, so every code branch was dead. `''` when the body carried no code. */
export class ApiError extends Error {
  status: number
  code: string
  constructor(message: string, status: number, code = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** The `ApiError` a failed response should become. A body is a one-shot stream, so the
 *  sentence and the code have to come out of the SAME read — hence one builder every thrower
 *  below calls, rather than `errText` here and a second parse somewhere else. */
async function apiError(r: Response): Promise<ApiError> {
  const { message, code } = await errEnvelope(r)
  return new ApiError(message, r.status, code)
}

/** True when a rejection is this gateway's typed failure carrying exactly `code`.
 *
 *  Match on the code, NEVER on `.message`: the message is human copy that gets reworded, the
 *  code is the registry key `http_errors.py` declares. The four learning panels each hand-rolled
 *  `error.message.includes(code)`, which is false for every real response, so they shared one
 *  bug in four places — this is the one predicate they now share instead. */
export function hasApiCode(e: unknown, code: string): boolean {
  return e instanceof ApiError && e.code === code
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw await apiError(r)
  return r.json() as Promise<T>
}

const get = <T>(p: string) => fetch(p, { headers: { ...SK } }).then(j<T>)
const post = <T>(p: string, body?: unknown) =>
  fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json', ...SK }, body: body == null ? undefined : JSON.stringify(body) }).then(j<T>)
const put = <T>(p: string, body?: unknown) =>
  fetch(p, { method: 'PUT', headers: { 'Content-Type': 'application/json', ...SK }, body: body == null ? undefined : JSON.stringify(body) }).then(j<T>)
const patch = <T>(p: string, body?: unknown) =>
  fetch(p, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...SK }, body: body == null ? undefined : JSON.stringify(body) }).then(j<T>)
const del = (p: string) => fetch(p, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw await apiError(r) })

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

// The SAME model-call audit regrouped by the SUBSYSTEM that asked (ACP-AGENT-PARITY G47).
// The per-provider rows above cannot answer "is my expensive background pass alive?": four
// unattended subsystems share one provider and one `background` use case, so a learning pass
// that dies every time is invisible inside a healthy provider's aggregate. `name` is one of
// the closed caller vocabulary (`guardrails/audit.CALLERS`) or `(unattributed)`.
export interface CallerHealth {
  name: string
  calls: number
  passed: number
  failed: number
  pass_rate: number | null
  p50_ms: number
  p90_ms: number
  p99_ms: number
  failure_modes: Record<string, number>
  dollars_est: number
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

// External Access — the shared inbound seam (EXTERNAL-ACCESS §1).
export interface ExternalAccessSurface {
  surface: string
  enabled: boolean
  allow_remote: boolean
  /** Whether a usable (≥32-byte, non-reserved) token is configured. Never the token. */
  token_configured: boolean
  /** WHY the token is unusable, when it is — the mount refusal's own reason string. */
  token_problem: string
  /** True for the control bridge, which ignores `allow_remote` by construction. */
  loopback_only: boolean
}
export interface ExternalAccessClient {
  client_id: string
  label: string
  surfaces: string[]
  /** Pinned agent — a request naming a different one is refused, never substituted. */
  agent: string
  tools: string[]
  scope: Record<string, unknown>
  rate_overrides: Record<string, unknown>
  disabled: boolean
  created_at: string
  last_seen_at: string
  /** Derived from `inbound_audit.jsonl`, not a stored counter. */
  requests_seen: number
  refusals_seen: number
  // 🔴 No `token_hash`. The server does not send it; see handlers/external_access.py.
}
export interface ExternalAccess {
  /** The master kill switch. Off means all five surfaces are unmounted. */
  enabled: boolean
  /** An active incident refuses every inbound request with 503, whatever the switches say. */
  incident_active: boolean
  public_url: string
  caps: {
    rate_rps?: number
    rate_burst?: number
    rate_concurrent?: number
    auto_disable_after_breaches?: number
    capture_retention_days?: number
    /** Hosts the capture proxy may forward to. EMPTY DENIES EVERYTHING — it is an
     *  exclusive allow-list, so this is the one cap whose default breaks its surface. */
    capture_upstream_allowlist?: string[]
  }
  surfaces: ExternalAccessSurface[]
  clients: ExternalAccessClient[]
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
/** The sync leg of the schedule (§4). `transport` is the CONFIGURED transport's provider
 *  name — empty means none is chosen, which is why "no conflicts" on this instance means
 *  "sync never ran" rather than "sync is healthy". `encrypted` is the RESOLVED verdict for
 *  that transport, not the `encrypt` tri-state, so the panel can answer "are my bytes
 *  readable in that store?" instead of echoing "auto". */
export interface DurabilitySyncStatus extends DurabilityJob {
  enabled: boolean
  transport: string
  encrypt: 'auto' | 'on' | 'off' | string
  encrypted: boolean
}
export interface DurabilityStatus {
  enabled: boolean
  export: DurabilityJob
  snapshot: DurabilityJob
  drill: DurabilityJob
  sync: DurabilitySyncStatus
}
/** One both-sides-edited divergence awaiting a decision (§4.2). Both versions travel so the
 *  reviewer can see what they are choosing between; `proposal` is the drafted merge, and
 *  `null` with a `proposal_error` is a draft that was attempted and failed — not a merge
 *  that is still coming. */
export interface DurabilityConflict {
  id: string
  entry_id: string
  entity_id: string
  domain: string
  surface: string
  ancestor_sha: string
  local_sha: string
  remote_sha: string
  local_row: Record<string, unknown>
  remote_row: Record<string, unknown>
  detected_at: string
  status: string
  proposal: Record<string, unknown> | null
  rationale: string
  proposed_at: string
  proposal_error: string
  resolution: string
  resolved_at: string
}
/** One tracked state tree in the time-travel history (DURABILITY-AND-SYNC §5). */
export interface DurabilityHistoryRoot {
  id: string
  label: string
  worktree: string
  exists: boolean
  commits: number
  memory: boolean
}
export interface DurabilityHistoryStatus {
  enabled: boolean
  git: boolean
  dir: string
  roots: DurabilityHistoryRoot[]
}
/** `unattended` is the "what changed while I slept" flag — the commit's writes came
 *  from a scheduled or background surface, not from someone at the dashboard. */
export interface DurabilityHistoryEntry {
  sha: string
  short: string
  at: number
  subject: string
  surface: string
  unattended: boolean
}
export interface DurabilityHistoryTimeline {
  root: string
  label: string
  commits: number
  entries: DurabilityHistoryEntry[]
  forward_refs: { ref: string; sha: string; at: number }[]
}
/** `rendered: false` means the diff exceeded the server's render budget: it is listed
 *  with its size, never silently shown as empty. */
export interface DurabilityHistoryDiffFile {
  path: string
  status: string
  bytes: number
  rendered: boolean
  diff: string
}
export interface DurabilityHistoryPreview {
  operation: 'rollback' | 'revert'
  root: string
  target: string
  head: string
  files: DurabilityHistoryDiffFile[]
  commits_rolled_away: number
  reversible: boolean
  /** The NORMALIZED subset this preview was taken over, repo-relative; `[]` means the whole
   *  root. Echoed back by the server rather than reflected from the request, because it is
   *  the set a confirming call must match — see `durabilityHistoryApply`. Optional only so a
   *  server that predates the subset still types: absent and `[]` both mean whole-root. */
  paths?: string[]
}
/** Phase one of the two-phase contract: the preview, plus the `expected_head` a
 *  confirming call must echo. There is no way to apply without first holding this. */
export interface DurabilityHistoryPreviewResponse {
  confirmed: boolean
  expected_head: string
  preview: DurabilityHistoryPreview
}
export interface DurabilityHistoryResult {
  ok: boolean
  operation: string
  root: string
  head: string
  prior_head?: string
  prior_ref?: string
  reverted?: string
  reload_required: boolean
  /** What was actually operated on, `[]` for the whole root — so a result can be read back as
   *  "these files moved", not just "something moved". */
  paths?: string[]
}
export interface DurabilityConflicts {
  conflicts: DurabilityConflict[]
  truncated: boolean
  counts: {
    total: number
    needs_review: number
    /** Unresolved count per review surface — what §4.2 item 3's routing actually did. A
     *  panel showing only its own surface still has to report what waits elsewhere. */
    by_surface: Record<string, number>
    selected: number
  }
  surfaces: { memory: string; knowledge: string; durability: string }
  sync: { enabled: boolean; transport: string; configured: boolean }
}
/** Which version of a conflicted row to write. `accept_proposal` refuses when no merge was
 *  drafted rather than falling back to another version. */
export type DurabilityConflictChoice = 'keep_local' | 'take_remote' | 'accept_proposal'
/** Per-domain counts recorded INSIDE an archive's manifest (§6). `null` means the
 *  archive recorded none (it predates MANIFEST v3) — which is NOT the same as an empty
 *  archive, so it must render as "not recorded" rather than as zeros. */
export type DurabilityDomainCounts = Record<string, { files: number; bytes: number; rows: number }>
/** The last restore drill's verdict. `ok: null` = ran, but the outcome was not recorded
 *  (a pre-DAS-10 stamp). Never render an unknown outcome as a pass. */
export interface DurabilityDrill {
  ran: boolean
  ok: boolean | null
  at: number
  detail: string
  archive: string
  databases_checked?: number
}
export interface DurabilityArchive {
  id: string
  name: string
  taken_at: string
  size: number
  /** False = the CURRENT retention tiers would prune this one on the next pass. */
  retained: boolean
  domains: DurabilityDomainCounts | null
  /** Present only on the archive the last drill actually exercised. */
  validate: DurabilityDrill | null
}
export interface DurabilityArchives {
  directory: string
  archives: DurabilityArchive[]
  would_prune: string[]
  tiers: { daily: number; weekly: number; monthly: number }
  last_drill: DurabilityDrill
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

// The automation would-execute description (PLATFORM-RESILIENCE §3.3) — the five facts §3.3
// names, each read from a shipped resolver server-side. `epoch`/`at` are empty when the trigger
// has no next fire at all (a `manual` or expired row), which is a THIRD state distinct from
// armed and computed: `source` is what tells them apart, so read it rather than truthiness on
// `at`.
export interface AutomationNextFire {
  cadence: string
  at: string
  epoch: number | null
  source: 'armed' | 'computed' | 'none'
  armed: boolean
}
export interface AutomationActionConfig {
  provider: string
  config: Record<string, unknown>
  vars: Record<string, unknown>
  // Secret KEYS the config references. The values are never resolved by a preview, so this is
  // the honest answer to "which credential does this use?".
  secret_refs: string[]
  rendered: string
  render_error: string
}
export interface AutomationCapabilityGrants {
  declared: Record<string, string[]>
  requested: Record<string, string[]>
  // What still needs an explicit opt-in after decision 7's read-only default has been applied.
  needs_fence: Record<string, string[]>
  refused: { key: string; value: string; reason: string }[]
  granted: boolean
}
export interface AutomationObserveMode {
  provider: string
  provider_known: boolean
  supported: boolean
  // `observe` = a real observe-mode run is possible (the spawn-based LLM providers);
  // `preview` = the T9 rule, this provider has no observe mode so we describe instead.
  mode: 'observe' | 'preview'
  executed: boolean
  ok: boolean
  detail: string
  gate_plan: { enforced?: string[]; bypassed?: string[]; dry_run?: boolean; executes?: boolean }
}
export interface AutomationWouldExecute {
  trigger: {
    id: string; name: string; kind: string; enabled: boolean; state: string; ok: boolean
    issues: { path: string; message: string; severity: string; closest: string }[]
  }
  next_fire: AutomationNextFire
  action_config: AutomationActionConfig
  session_key: { key: string; declared: string; mode: 'pinned' | 'conversation' | 'fresh' }
  capability_grants: AutomationCapabilityGrants
  observe_mode: AutomationObserveMode
  dry_run: boolean
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
/** One approved sender on a channel (EA-7). `provider` is an opaque runtime key the transport
 *  picked ("telegram", "slack", "email") — NOT the app name, which carries a `-channel` suffix.
 *
 *  `added_at` is an ISO-8601 STRING, not epoch seconds like `DeviceRec.minted_at` — the trust
 *  store writes `datetime.isoformat()`. An empty string means the store had no timestamp and
 *  must render as a distinct word, never as the epoch or as today.
 *
 *  `via` is provenance: `owner` (the owner clicked Allow on the unknown-sender notification) or
 *  `pairing` (the sender redeemed an 8-digit code). It is rendered, not assumed — an
 *  unrecognized value renders as itself. */
export interface ChannelTrustSender {
  sender_id: string
  name: string
  added_at: string
  via: string
}
export interface ChannelTrustChannel { channel_id: string; name: string; added_at: string }
/** A provider's whole trust posture. NEVER carries the pairing code or its hash — only whether
 *  one is outstanding (`pairing_active`) and when it dies. */
export interface ChannelTrustProvider {
  provider: string
  policies: { dm: string; group: string }
  allowed_senders: ChannelTrustSender[]
  tracked_channels: ChannelTrustChannel[]
  pairing_active: boolean
  pairing_expires_at: string
}
export interface ChannelTrust {
  providers: ChannelTrustProvider[]
  dm_policies: string[]
  group_policies: string[]
  default_dm_policy: string
  default_group_policy: string
}
// A background subagent (from /api/spawn) — spawned by a cron/loop/Slack/agent.
export interface SpawnedAgent { id: string; task: string; done: boolean; parent?: string; agent?: string; started?: number; result?: string; error?: string }
// A knowledge item scored for chat-context injection (from search-for-context),
// carrying its token cost so the picker can budget. P12 adds the per-item citation
// locator (source_type/section/line_range/deep_link) so a card can deep-link + cite
// where in the source the match sits; all optional (null for a structureless type).
export interface KnowledgeContextCard {
  id: string; title: string; provider?: string; match_type?: string; tokens: number; summary?: string
  /** The matched PASSAGE. `search_for_context` has always sent it (it is the text the
   *  composer injects) but it was missing from this interface, so the one thing a
   *  retrieval card exists to show was untypeable. OU-3's knowledge try-one reads it. */
  content?: string
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
  // APE-10: consented cross-app read-only file sharing (the mirror of `appMessaging`).
  // `storageShared` = this app opts IN to exposing its own data dir to a reader;
  // `storageRead` = the apps whose data THIS app reads (exact name or trailing-`*`
  // prefix). Enforced where storage is granted — the backend is mounted each granted
  // sharer's data dir READ-ONLY as `PERSONALCLAW_APP_SHARED_DIR_<SHARER>`, and a read is
  // granted only when both halves are declared (double-declaration, deny by default).
  // Writes stay broker-only (`appMessaging`). Absent = shares/reads nothing.
  storageShared?: boolean
  storageRead?: string[]
  // DC-2: native desktop capabilities this app may reach THROUGH the gateway (apps
  // never touch Electron IPC). Enforced — `/api/desktop/*` refuses an undeclared
  // capability 403 + SEL `desktop.capability_denied`. Exact names only, no wildcard.
  // Absent = no native reach at all, which the consent UI states rather than implies.
  desktop?: string[]
  // INU-7: proposal kinds this app may raise into your inbox. Enforced —
  // `POST /api/inbox/proposals` 403s a kind that is not declared here, and an app can
  // never propose a callback into ANOTHER app. Absent = it may raise no proposal at all,
  // which the consent UI states rather than implies.
  proposals?: AppProposalKindWire[]
  // APE-1: the two grants that are NOT enforced yet, and must not be shown as if they
  // were. `backgroundTasks` = the app may run a long-lived supervised worker (APE-3
  // hosts it); `eventSubscriptions` = typed platform events it subscribes to (APE-2's
  // registry owns the names — exact matches, no wildcard). Neither runtime exists today,
  // so nothing hosts a worker and no platform event is delivered to any app, declared or
  // not. They are disclosed because the declaration is a STANDING grant: it goes live
  // with no second prompt once that support ships. `PermissionList` puts them under
  // "declared, not yet in effect", never among the enforced bullets.
  backgroundTasks?: boolean
  eventSubscriptions?: string[]
}
/** INU-7. One declared proposal kind. `kind_suffix` is namespaced under the app at
 *  registration (`app:<name>` / `proposal:<suffix>`); `label` is what the user sees. */
export interface AppProposalKindWire { kind_suffix: string; label?: string }
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
/** Live state of the companion LAN advertiser (COMPANION-APPS C3).
 *  `advertising` is the RUNNING advertiser, not the config flag — the two legitimately
 *  differ (a loopback-only gateway is a designed no-op). `reason` is a closed set;
 *  `detail` is the backend's own sentence for it. `txt` is the record verbatim, so the
 *  panel can show exactly what the network is told. */
export interface CompanionDiscovery {
  advertising: boolean
  reason: 'advertising' | 'disabled' | 'loopback_only' | 'no_lan_address' | 'gateway_not_running'
  detail: string
  service_type: string
  instance_name: string
  port: number
  addresses: string[]
  txt: Record<string, string>
}
/** One paired device with a live session (COMPANION-APPS C2). Derived from `sessions.json`,
 *  so a row disappears the moment its session is revoked or expires, and it NEVER carries the
 *  nonce — the registry is read aloud, the nonce is the credential.
 *
 *  `last_seen` is 0 for a device that has never made an authorized request. That is a distinct
 *  state from `minted_at` and must render as "never": the backend deliberately does not
 *  backfill it from the pairing time, because a device that paired and never came back would
 *  otherwise read as freshly active. See `DeviceInfo` in `dashboard/session_store.py`. */
export interface DeviceRec {
  id: string
  name: string
  kind: 'browser' | 'mobile' | 'desktop' | 'cli' | 'unknown'
  minted_at: number
  last_seen: number
  issuer: string
  expires_at: number
}
/** `pair/start`'s reply. `code` arrives pre-grouped (`XXXX-XXXX`) for reading out loud, and
 *  `pairing_url` already contains it, so the URL is actionable on its own — which is what makes
 *  it the QR payload as well as the copyable link. `expires_in` is SECONDS. */
export interface DevicePairStart {
  code: string
  pairing_url: string
  expires_at: number
  expires_in: number
}
export interface AppUiPage { route: string; label: string; icon: string }
/** APE-4 — an app's DECLARED quality bar, rendered as the Store/Library card badge row.
 *  Every axis is OPTIONAL and that is load-bearing: `undefined` means the app claimed
 *  nothing on that axis, which is a different fact from `false` ("claims it does not
 *  meet the bar") and must never render as either a pass or a miss. The backend parses
 *  the manifest block through `QualityDeclaration`, so an undeclared axis arrives absent
 *  rather than defaulted — see `qualityBadges` for the three renderings. */
export interface AppQualityWire {
  tested?: boolean
  designSystem?: 'v2' | 'legacy' | 'n/a'
  a11y?: boolean
}
export interface AppSummary {
  name: string; displayName: string; version: string; description: string
  enabled: boolean; origin: string; source?: string; icon: string
  heroUrl?: string  // resolved data: URI for the optional hero/banner image; absent/"" if none
  hasBackend: boolean; hasUI: boolean
  uiPages: AppUiPage[]
  // The app's genui components module (manifest `ui.components`, relative to its ui/
  // dir) + its declared `uiCapabilities`. The shell loads the module for an ENABLED app
  // that declared `generative-component`, so its components exist for any genui widget —
  // not only inside that app's own page (AMBIENT-SURFACES §5.1).
  uiComponents?: string
  uiCapabilities?: string[]
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
  // APE-4: the app's declared quality bar. `{}`/absent = declared nothing → no badges.
  quality?: AppQualityWire
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
  /** The provider's DECLARED capabilities (`chat`, `stt`, `tts`, `search`, `messaging`, …).
   *  `providerType` alone cannot tell a chat model from a speech model — faster-whisper
   *  (stt) and piper-tts (tts) are both `providerType: 'model'` — so a surface grouping
   *  apps by what they DO must read this, not the author-controlled `tags`. Absent for a
   *  non-provider app or a registry pointer whose manifest isn't fetched yet. */
  providerCapabilities?: string[]
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
  // APE-4: the declared quality bar, so a Store card can badge it BEFORE install.
  // `{}`/absent = declared nothing (also the case for a registry pointer whose
  // manifest hasn't been fetched) → no badges, which is honest either way.
  quality?: AppQualityWire
}
export interface AppScanFinding { surface: string; severity: string; rule: string; path: string; evidence: string }
/** SH-3 contract C2. `state` is `signed` | `unsigned` | `invalid`; `signer` is the
 *  in-tree key's identity (only meaningful when signed); `reason` is the refusal text an
 *  `invalid` state must show. An `invalid` state means the install was REFUSED — it is
 *  never consentable, unlike a warning verdict. `unsigned` is normal for community apps. */
export interface AppSignature { state: string; signer: string; reason: string }
export interface AppScanReport {
  verdict: string; findings: AppScanFinding[]; tier?: string
  signature?: AppSignature | null
}
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
  memory_mode?: string; last_message?: string
  /** 🔴 DECLARED `number` UNTIL CYCLE 173, AND THE ENDPOINT HAS NEVER SENT ONE. Read from the wire:
   *  `POST /api/chat/sessions` returns `last_ts: ""`, and the sibling list (`ChatSessionSummary`,
   *  below) has always typed the same field `string` — two shapes of ONE entity disagreeing about one
   *  field. Nothing consumed it off this interface, so nothing broke; what it did do was make a real
   *  defect look plausible. Cycle 166 deferred a "renders BLANK because it is fed a NUMBER" finding on
   *  `#/chat` to its own cycle, and the number in that claim came from HERE, not from any payload.
   *
   *  🪤 The lesson `lib/epoch` already carries, in its own words: a type is "a declaration, not a
   *  check — nothing validates a fetch against it". `started_at?: number` printing "in NaNd" on
   *  `#/dashboard` was the same shape. Fetch the endpoint before believing the interface. */
  last_ts?: string
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
// The library landing surface's four shelves in one read (KNOWLEDGE-LIBRARY S3, T3.3).
// `count` here is DERIVED from the same query that produces a shelf's items — unlike
// `KnowledgeCollection.item_count`, which counts archived members the opened shelf hides.
// `count_capped` says the smart-resolve cap was hit, so the UI can render "200+" instead of
// passing a cap off as a total.
export interface KnowledgeLibraryHome {
  recently_added: KnowledgeItem[]; continue_reading: KnowledgeItem[]; favorites: KnowledgeItem[]
  collections: { id: string; name: string; kind: 'manual' | 'smart'; icon?: string; position?: number; count: number; count_capped?: boolean }[]
}
// One reading highlight on a knowledge item (KNOWLEDGE-LIBRARY T3.1). Anchored by TEXT,
// not by offset: the reader renders markdown, so a character index into the item's source
// does not survive the transform. `occurrence` says WHICH instance of `quote` this is, so
// two highlights of a repeated sentence stay distinct. See pages/knowledge/readingAnchors.ts.
export interface KnowledgeAnnotation {
  id: string; item_id: string; quote: string; occurrence: number; note: string; created_at: string
}
// A near-duplicate candidate for one item (KNOWLEDGE-LIBRARY T3.2). Deliberately NOT a
// `KnowledgeItem`: the backend returns a lean row and never the embedding, and `reason` is the
// scorer's own account of WHY these two look alike — the only thing that makes a destructive
// merge reviewable rather than a leap of faith. See knowledge/store.py::find_duplicates.
export interface KnowledgeDuplicate {
  id: string; title: string; item_type: string; created_at: string; word_count: number; reason: string
}
// What a merge moved to the survivor. The route reports it per relation so the UI can tell the
// user what it actually did ("3 collections, 2 mentions") instead of a bare "Merged".
export interface KnowledgeMergeResult {
  ok: boolean; kept: string; merged: string
  moved: {
    collections: number; tags: number; mentions: number; annotations: number
    relations: number; citations: number
  }
}
// ── Structural editing verbs (KL-19) ──
// One section boundary a split may cut on. `offset` is a character offset into the item's body,
// so the caller slices at it without re-deriving headings — and it is the SAME boundary the
// chunker sections on, which is why a split's halves re-chunk along the seam the reader chose.
export interface KnowledgeSection {
  offset: number; line: number; title: string; level: number; chars: number
}
// One inbound reference a verb would break. `relinkable` is the whole reason these are reported
// separately from a refusal: a break the store can repair is an OFFER, one it cannot is a warning
// the reader weighs. A UI that rendered both the same way would present a decision with no choice
// in it. `refs` are the ids of the items doing the referring.
export interface KnowledgeRestructureBreak {
  kind: 'citation' | 'citation_chunk' | 'wikilink' | 'annotation' | 'kind_contract' | string
  message: string; relinkable: boolean; refs: string[]
}
export interface KnowledgeRestructurePlan {
  verb: string; item_id: string; summary: string; token: string
  affected: string[]; breaks: KnowledgeRestructureBreak[]
  relink_offered: boolean
  detail: Record<string, unknown>
}
export interface KnowledgeRestructurePreview {
  confirmed: false; token: string; plan: KnowledgeRestructurePlan
}
export interface KnowledgeRestructureResult {
  ok: boolean; confirmed: true; kept: string; created: string[]
  undo_token: string; summary: string
  // True when this response REPLAYS an earlier application of the same token rather than
  // restructuring again — the server's answer to a doubled submit.
  idempotent: boolean
  annotations_moved?: number; citations_widened?: number
  moved?: KnowledgeMergeResult['moved']
  wikilinks_relinked?: { items: number; links: number }
  logical_key?: string; title?: string; kind?: string
}
export interface KnowledgeUndoEntry {
  token: string; verb: string; item_id: string; summary: string; created_at: string
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
  // an assistant message that used episodic recall carries memory_citations (§5.4); one
  // whose turn loaded skills carries skills_used (LEARNING-VISIBILITY T2.1) — absent, never
  // `[]`, when the turn loaded none, and never listing a REFUSED skill (named to the agent
  // but never loaded).
  meta?: { tool_call_id?: string; input?: string; purpose?: string; output?: string; done?: boolean; tool?: string; memory_citations?: { n: number; id: string | null; preview?: string }[]; skills_used?: { name: string; state: string; loaded_tokens: number }[] }
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
// 'other' is a STATE, not a choice: the automation's action is a provider this form cannot
// edit (notify, digest, remediation, …). It exists so the form stops presenting such a trigger
// as an agent prompt — and so `_scheduleBodyToWire` knows not to invent an action for it.
export type ScheduleExecMode = 'agent' | 'script' | 'command' | 'other'
export interface ScheduleJob {
  id: string; name: string; message: string; enabled: boolean
  // attribution (TSE-4) — see the same pair on `Trigger`. A schedule row is served by the same
  // store, so it carries the same verdict.
  author?: string; read_only?: boolean
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
  // Parse errors from the store's lenient load (S87): a schedule the store kept despite a
  // malformed field still lists, carrying its errors so the row can flag "needs attention"
  // rather than hiding an automation the user cannot otherwise debug — the same `broken`
  // contract the store-trigger wire row (`WireTrigger`) already carries.
  broken?: string[]
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
export type TaskStatus = 'open' | 'in_progress' | 'blocked' | 'done' | 'cancelled' | 'skipped'
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
    /** Whether this template is published as an A2A skill (EXTERNAL-ACCESS §5). Optional and
     *  DEFAULTS TO FALSE on both sides — an absent key means unpublished, which is what every
     *  template authored before A2A existed looks like. The detail page's toggle reads this and
     *  writes it through `publishWorkflowToA2A`, never through `saveWorkflowDef`: the def this
     *  page holds is the secret-STRIPPED read, so re-saving it would drop credential bindings. */
    a2a_published?: boolean
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
/** One recorded template version (WF2LEA-6). Immutable once written; a run pins the number it
 *  executed, and re-pin/rollback moves only the active pointer. */
export interface WorkflowVersionRow {
  version: number
  source: string // 'user' | 'refiner'
  created_at: string
  note: string
  run_ids: string[]
  ops_count: number
}
/** One typed op in a version-to-version diff (the engine's own vocabulary). */
export interface WorkflowVersionOp {
  op: string // insert | delete | update_node | move | set_input
  node_id?: string
  kind?: string
  fields?: string[]
}
/** A template's maturity (R11): L0 draft → L3 mature, from static signals + ledger activity. */
export interface WorkflowMaturity {
  level: number
  label: string // draft | shaping | proven | mature
  signals: Record<string, boolean>
  clean_runs: number
  evaluator_rejected: boolean
}
/** One row of the Run Ledger tab: a past run of this template with its totals. */
export interface WorkflowLedgerRow {
  run_id: string
  status: string
  spec_version: number
  created_at?: string
  totals: { tokens?: number; cost_usd?: number; steps_completed?: number; steps_failed?: number }
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
// One review finding as the triage panel receives it (EXECUTION-ISOLATION §7, EI-9): the
// WORKFLOWS-V2 Canonical Finding record, plus `auto_fixable`, plus the ANCHOR VERDICT computed
// against the run's diff on this request. `anchor_state: 'unanchored'` with an `anchor_reason` is a
// finding that must be shown as unverifiable rather than as truth — `resolved_path`/`resolved_line`
// are the diff's own spelling of where it landed, and are what the accepted brief cites.
export interface ReviewFinding {
  key: string; severity: string; location: string; problem: string; why: string
  recommended_fix: string; status: string; auto_fixable: boolean; line_text: string
  origin_run_id: string; origin_node_id: string; origin_session_key: string
  anchor_state: 'anchored' | 'unanchored'; anchor_reason: string
  resolved_path: string; resolved_line: number; diff_line_text: string
}
export interface WorkflowReviewPayload {
  run_id: string; workspace: string; diff: string; diff_truncated: boolean
  findings: ReviewFinding[]
  counts: { total: number; anchored: number; unanchored: number }
  terminal: boolean
}
// `delivered: false` with `reason: 'nothing_accepted'` is the CORRECT outcome of a full rejection —
// not an error. `handoff_parked` means the run was already terminal, so the brief was saved for a
// follow-up run rather than a fresh one being started unasked.
export interface WorkflowTriageResult {
  run_id: string; dry_run: boolean; brief?: string
  accepted: ReviewFinding[]
  rejected: Array<ReviewFinding & { rejection_reason: string }>
  refused: Array<ReviewFinding & { refused_reason: string }>
  untriaged: ReviewFinding[]
  receipt: { delivered: boolean; reason: string; target: string; brief: string; count: number }
  calibrated?: number
  auto_apply_candidates?: string[]
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
  /** The localhost web preview (EXECUTION-ISOLATION §6.2). Scanned per request, never stored:
   *  a persisted port outlives the process that held it, and an "Open Preview" pointing at a
   *  dead port is worse than no affordance. `reason` is always populated when `ports` is empty,
   *  because "nothing is running" and "nothing could look" are different answers to render. */
  preview?: {
    ports: Array<{ port: number; url: string; pid: number; command: string; address: string }>
    root: string
    scanned: boolean
    reason: string
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
// PP-8: one `branch` selector's case distribution across a template's routed runs. `cases` lists
// EVERY declared case — a never-taken one is a real 0, not an absent key, so the dead case is
// nameable rather than invisible. The two warning strings are non-empty ONLY over a real sample
// (routed_runs >= the said-no bar): a case unseen over three routings is unsampled, not dead.
export interface WorkflowBranchStats {
  path: string
  cases: Record<string, number>
  routed_runs: number
  never_taken: string[]
  degenerate_warning: string
}
// PP-8: one judge gate's verdict distribution. Complements the said-no table (approve/reject) by
// showing the FULL verdict vocabulary — a judge can pass every gate while returning one verdict
// every time, and only this shows the second. `degenerate_warning` is sample-gated like the rest.
export interface WorkflowJudgeStats {
  node_id: string
  verdicts: Record<string, number>
  total: number
  degenerate_warning: string
}
export interface WorkflowEdgeStats {
  branches: Record<string, WorkflowBranchStats>
  judges: Record<string, WorkflowJudgeStats>
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
  // PP-8: per-`branch` case and per-judge verdict distributions across the template, beside the
  // said-no table because a routing decision and a gate decision are the same kind of edge.
  edges: WorkflowEdgeStats
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
    risky: { degraded: unknown[]; gates: WorkflowGateStats[]; edges: WorkflowEdgeStats; verification_debt: number }
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
  max_cycles?: number; max_cost_usd?: number; deadline_secs?: number
  skill_ids?: string[]; workflow_ids?: string[]; project_id?: string
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
// `label`/`hint`/`category` come from the use-case vocabulary itself
// (`providers/prompt_use_cases.py`), not from a table in the dashboard: the
// vocabulary is open — an app may contribute its own bindable use case — so any
// copy kept here would describe only the contexts that existed when it was written.
export interface PromptBinding {
  use_case: string; ref: string; effective_ref: string
  label: string; hint: string; category: string
}
/** One Settings-UI grouping, in the catalog's declared display order. */
export interface PromptCategoryGroup { key: string; label: string; hint: string }
export interface PromptBindings {
  use_cases: string[]; default_ref: string; bindings: PromptBinding[]
  categories: PromptCategoryGroup[]; available: PromptItem[]
}
// Live authoring: render arbitrary (unsaved) template content through the real engine.
export interface PromptPreview { ok: boolean; rendered?: string; error?: string; detected_variables: PromptVariable[]; includes: string[] }
// The template-language reference the editor renders as a click-to-insert cheatsheet.
export interface PromptSyntaxFn { name: string; category: string; signature: string; description: string; insert: string }
export interface PromptSyntaxConstruct { category: string; label: string; snippet: string; description: string }
export interface PromptSyntax { functions: PromptSyntaxFn[]; constructs: PromptSyntaxConstruct[] }
export interface SkillItem { key: string; name: string; description: string; always: boolean; path?: string; source: string; type: string; loaded_by_agents: string[]; integrity?: 'intact' | 'tampered' | 'unverified'; agent?: string }
export interface EphemeralDraft { slug: string; title: string; body: string; created_at: string }
/** `trigger` is the STUMBLE that produced a refine proposal (`correction` | `failure_retry` |
 *  `rejection`), or absent/'' for one a model proposed. It is the review surface's answer to
 *  "why am I being asked this?" — a refine row without it can only say what it changes. */
export interface SkillProposal { id: string; slug: string; description: string; triggers: string; kind: string; refine_target?: string; trigger?: string; session_key: string; created_at: string; status: string; procedure_preview: string }
/** The most recent skill-ladder pass. `null` on the feed means the ladder has never
 *  run — which is the only thing that distinguishes an idle ladder from a broken one
 *  when `proposals` is empty. Both looked identical before this existed. */
export interface SkillLadderReview { verdict: string; elapsed_ms: number; session_key: string; detail: string; at: string }
export interface SkillProposalFeed { proposals: SkillProposal[]; lastReview: SkillLadderReview | null }
/** `diff`/`version` are present only for a `kind: 'refine'` proposal, and are DERIVED per
 *  request from the skill's current body — never stored. An empty `diff` is meaningful, not a
 *  load failure: the refine target no longer exists, or the refinement changes nothing.
 *  `version` is the refinement version accepting this proposal would create. */
export interface SkillProposalDetail extends SkillProposal { procedure_md: string; source_excerpt: string; diff?: string; version?: number }
/** One group of the learning summary block (LV-3). `count` is the EXACT group size;
 *  `names` is a bounded sample of it, so a renderer must never show `names.length` as
 *  the count — that would silently under-report the moment a group got busy. */
export interface LearningSummaryGroup { count: number; names: string[] }
/** The learning summary block: what was learned in the last `window_days` days.
 *  `total` is the sum of the four group counts and is what decides whether the block
 *  is worth rendering at all. */
export interface LearningSummary {
  window_days: number
  total: number
  new_skills: LearningSummaryGroup
  refined_skills: LearningSummaryGroup
  pending_proposals: LearningSummaryGroup
  facts: LearningSummaryGroup
}
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
/** One always-on convention in effect right now (PEP-10). `preview` is credential-redacted;
 *  `body` is only present on the single-doc editor read, where it is verbatim. */
export interface AlwaysOnItem {
  id: string; kind: 'always_skill' | 'project_instruction'; name: string
  scope: 'global' | 'project'; source: string; path: string; chars: number
  editable: boolean; read_only_reason: string; project_id: string; preview: string
  body?: string
}
export interface AlwaysOnResponse {
  items: AlwaysOnItem[]; project_id: string
  counts: { total: number; always_skills: number; project_instructions: number }
  /** How a user opts a skill INTO the always-on tier — so an empty tier can explain itself. */
  always_skill_mechanism: string
}
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
// `blocking` / `enforcement` (G40): whether this hook's EVENT can short-circuit the loop, and
// whether THIS hook actually does. Both are the server's verdict, not re-derived here: the backend
// computes `enforcement` from the same `AgentProfile.triggers` binding the firing path reads, so a
// row the page calls "enforcing" is a row a tool rejection would really come from. Deriving it in
// the FE from `used_by.length` would restate the bug — `used_by: []` was already on the wire and a
// user still could not tell an armed blocking hook from an inert one.
export type HookEnforcement = 'enforcing' | 'not_enforcing' | 'advisory'
export interface HookItem {
  id: string; name: string; event: string; matcher: string; provider: string; provider_config: Record<string, unknown>
  timeout: number; enabled: boolean; last_run: number; last_status: string; run_count: number; used_by: string[]
  blocking?: boolean; enforcement?: HookEnforcement
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
  // attribution (TEAM-SHARED-ENTITIES §2.2 — TSE-4). `author` is who WROTE the row; `read_only` is
  // the server's verdict that this machine's owner did not, so the harness will never arm or fire
  // it. Both are computed server-side from the same `ownership.is_owner_authored` predicate the arm
  // path uses — the page must not re-derive it from `author`, or the UI and the scheduler end up
  // with two opinions about who owns a trigger.
  author?: string; read_only?: boolean
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
  blocking?: boolean; enforcement?: HookEnforcement
}
/** Project the shared ScheduleForm's flat draft body onto the unified Trigger
 *  wire shape: a single canonical `action` + the schedule mechanism fields. The
 *  schedule executor dispatches every provider from this action, so the form's
 *  agent / script / command "exec modes" become invoke-agent / run-script / bash
 *  actions. (TriggerCreatePage already sends `action` directly; this serves the
 *  shared ScheduleForm edit path via ScheduleDetail.) */
function _scheduleBodyToWire(body: Record<string, unknown>): Record<string, unknown> {
  // Everything destructured here is ACTION config, and the only way it reaches the server is
  // inside the `action` this function builds. `rest` — the schedule mechanism — is the top level.
  // A caller that puts one of these keys on a body that already carries its own `action` is
  // discarding it, silently, in the browser: that is how the trigger-create page's
  // "Auto-approve tools" switch became decorative (issue 268). `approval_mode` in particular is
  // `invoke-agent`-only (`schedule.py`'s property returns '' for every other provider), so it can
  // only ride the invoke-agent branch below. `tests/test_trigger_wire_field_census.py` holds the
  // two field sets against each other so a future field cannot go missing the same way.
  const { message, agent, model, approval_mode, script, command, zt_timeout, action, ...rest } = body
  if (action) return { ...rest, action }  // already action-shaped (create page)
  let act: TriggerAction
  if (script) act = { provider: 'run-script', config: { script, timeout: Number(zt_timeout) || 0 } }
  else if (command) act = { provider: 'bash', config: { command, timeout: Number(zt_timeout) || 0 } }
  // 🔴 No `message` key means the form was in 'other' mode: the automation's action is a
  // provider this form cannot edit, so there is nothing here that describes an action and we
  // must send none. The server only replaces an action it is actually sent, so omitting it
  // PRESERVES the stored one.
  //
  // The `else` used to be unconditional, and that is what destroyed data (issue 689): a `notify`
  // trigger has no `script` and no `command`, so renaming it built a blank `invoke-agent` from
  // empty legacy fields and replaced the notification — 200, no warning, `task_template` (the
  // provider's required field) empty. Note the test is the PRESENCE of the key, not its
  // truthiness: an agent trigger legitimately has an empty prompt, and `'' ?? ''` must still
  // produce an agent action rather than silently skipping the update.
  else if (!('message' in body)) return rest
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
    // Carried through, never defaulted to a reassuring value: an older backend that omits these
    // leaves them undefined so the detail view renders NO enforcement claim, rather than a
    // confident "enforcing" chip over a hook nothing binds.
    blocking: t.blocking, enforcement: t.enforcement,
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
  // `evidence_strength` is WHICH KIND of evidence the refs are — anecdotal / correlated / causal /
  // ablation. The count alone cannot tell a measured on/off ablation (EVALUATION-SUBSTRATE §3.1
  // files retirements with `ablation`) from a co-occurrence, and "" is UNGRADED, never a grade.
  evidence_refs: string[]; evidence_strength: string; reinforcements: number; confidence: number
  manifest_valid: boolean; manifest_issues: string[]
  risk_tier: string; status: string
  renderable: boolean; bulk_acceptable: boolean
  // The Loop-2 gate's before/after columns (EVALUATION-SUBSTRATE amendment E2 / ES-6). ALWAYS
  // present: a proposal with no gate run arrives as `state: 'ungated'` with a reason, so the
  // absence is a sentence the reader can act on rather than an empty cell. `before`/`after`/
  // `delta` are `null` when unmeasured and must render as "not measured", never as 0 — the same
  // rule every eval panel follows. `pin` identifies WHAT produced the pair and is `{}` when
  // ungated; it is never synthesized.
  gate: LearningGate
  // The local A/B replay harness's verdict (EXTERNAL-ACCESS §9 / EA-6). ALWAYS present, for the
  // same reason `gate` is. Where the gate measures the candidate against the SHIPPED scenario
  // library, this measures it against real turns mined from the user's OWN captured sessions —
  // two corpora, two clauses, deliberately not merged into one number a reader cannot attribute.
  // `candidate_mean`/`baseline_mean` are `null` when nothing scored and must render as
  // "not measured", never as 0: a candidate that genuinely scored zero and a candidate nobody
  // scored lead a reviewer to opposite decisions. It is EVIDENCE and never a veto — a `regressed`
  // verdict leaves the row exactly as acceptable as it was.
  replay: LearningReplay
}
export interface LearningReplay {
  state: 'replayed' | 'unreplayed'
  reason: string
  verdict: 'improved' | 'neutral' | 'regressed' | 'unmeasured'
  candidate_mean: number | null; baseline_mean: number | null
  cases: number; scored: number; rejected: number; tool_free: number
  // True only when the learning replay budget was exhausted mid-pass. A deferral is a promise to
  // come back and reads differently from "there was nothing to measure".
  deferred: boolean
  // `capture:<session>#<record_hash>` per case — the pointer back to the turn each score came
  // from, so a claim about the user's own work is checkable against it.
  provenance: string[]
  ran_at: string
}
export interface LearningGate {
  state: 'gated' | 'ungated'
  reason: string
  before: number | null; after: number | null; delta: number | null
  regressed: boolean
  scenarios: number
  halted: boolean
  dollars_est: number; spend_observed: boolean
  pin: { model_fp?: string; scenario_sha256?: string }
  ran_at: string
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
/** One (rubric-class x tier x samples) row of the judge tier-recommendation table
 *  (EVALUATION-SUBSTRATE §6). Every judgement arrives DECIDED by the backend —
 *  `adequate` and `inadequate_reasons` included — because a frontend that re-derived
 *  "is this tier good enough" would eventually disagree with the harness, and the copy
 *  shipping the permissive answer would be the UI.
 *
 *  `null` means UNMEASURED, never zero: an unmeasured separation or flip rate is why a
 *  row is inadequate, and rendering it as 0 would read as a perfect score. */
export interface JudgeBenchRow {
  rubric_class: string
  tier: string
  samples: number
  agreement: number | null
  scored_cells: number
  verifier_absent: number
  protocol_errors: number
  separation: number | null
  flip_rate: number | null
  swapped_fixtures: number
  false_passes: number
  false_rejects: number
  forbidden_missed: number
  cost_usd: number | null
  wall_secs: number
  calls: number
  adequate: boolean
  inadequate_reasons: string[]
  notes: string[]
}
export interface JudgeBenchRecommendation {
  rubric_class: string
  /** 'recommended' | 'no_adequate_tier' | 'cost_unknown' — the two refusals matter more
   *  than the recommendation, so they are first-class rather than an empty tier. */
  verdict: string
  tier: string
  samples: number
  /** The model use case to rebind on Settings -> Models. */
  use_case: string
  /** The exact `Provider:model` ref the Models panel binds, or '' when nothing is bound. */
  model_ref: string
  cost_usd: number | null
  notes: string[]
}
export interface JudgeBenchView {
  bench_id: string
  columns: string[]
  rows: JudgeBenchRow[]
  floors: { agreement?: number; separation?: number; flip_rate?: number }
  recommendations: JudgeBenchRecommendation[]
  pin: Record<string, unknown> | null
  runs: string[]
}
/** One ARM's aggregate inside an ablation report (`evals.matrix.aggregate()`).
 *
 *  `mean_score` is `null` when the arm produced no SCORED cell — every cell came back
 *  `verifier_absent`. That is not a zero: §1.2's three-state contract says an absent
 *  verifier is never a failure, and an arm with no measurement is exactly why a report
 *  comes back `inconclusive` rather than `remove`. */
export interface AblationArmAggregate {
  /** outcome -> count. `verifier_absent` is counted here and never averaged into the mean. */
  counts: Record<string, number>
  total: number
  scored_count: number
  mean_score: number | null
}
/** One component's keep/remove/lighten report (EVALUATION-SUBSTRATE §3.1 / ES-7).
 *
 *  Everything arrives DECIDED — the verdict, the deltas, and the `epsilon` they were compared
 *  against. A frontend that re-derived "is this a real delta" would eventually disagree with
 *  the runner, and the copy shipping the permissive answer would be the UI. */
export interface AblationReportView {
  component_id: string
  kind: string
  target: string
  subject: string
  /** `keep` | `remove` | `lighten` | `inconclusive`. `inconclusive` is NOT one of
   *  `verdict_vocabulary`'s three recommendations — it means an arm was never measured, so
   *  there is no delta to read, and it must never be collapsed into `remove`. */
  verdict: string
  /** arm (`on` | `off` | `cheap`) -> aggregate. `cheap` is ABSENT unless the component
   *  declares a cheap form; an undeclared cheap arm would score identically to `on` and be
   *  reported as a fabricated `lighten`, so the runner omits it rather than defaulting it. */
  arms: Record<string, AblationArmAggregate>
  /** `on − off`. `null` when either arm is unmeasured. */
  delta: number | null
  /** `on − cheap`. `null` when no cheap arm ran. */
  cheap_delta: number | null
  epsilon: number
  matrix_id: string
  trials: number
  created_at: string
  /** The live files the byte-identity guard watched, with their unchanged digests — the
   *  report's own proof that the run never mutated the real config. */
  live_state: Record<string, string>
}
/** One registered ablatable component, from `evals/ablation_registry.json`. */
export interface AblationRegistryRow {
  component_id: string
  kind: string
  target: string
  subject: string
  off_value: unknown
  cheap_value: unknown
  live_refs: string[]
  description: string
}
/** One past cadence run. `proposal` is the filed LEARN-R9 retirement proposal id, or
 *  `not_filed:<reason>` when a `remove` verdict did not file one, or '' for a verdict that
 *  never files. The distinction matters: a `remove` with nothing filed is a dropped
 *  recommendation, not a completed one. */
export interface AblationHistoryEntry {
  ts: string
  component_id: string
  verdict: string
  matrix_id: string
  delta: number | null
  proposal: string
}
export interface AblationView {
  report: AblationReportView
  /** The three real recommendations, in the runner's own order. */
  verdict_vocabulary: string[]
  registry: AblationRegistryRow[]
  /** The last 20 cadence runs, oldest first. */
  history: AblationHistoryEntry[]
  last_run_ts: string
  cadence_days: number
  due: boolean
}
/** One arm's aggregates in a skill-impact benchmark task, as `harness/fanout_measure` computed
 *  them. Every field arrives computed; nothing here is re-derived in TS. */
export interface BenchmarkArmAggregate {
  trials: number
  mean_score: number
  spread: number
  tokens: number
  tokens_per_point: number
}
/** One frozen-register task's verdict.
 *
 *  `verdict` is `null` when the runner could not assemble both arms — a state deliberately
 *  OUTSIDE the five verdict strings, because "we did not measure this" and "we measured it and
 *  withheld a direction" are different claims. `delta_points` is `null` in exactly that case,
 *  and it MUST NOT render as 0.000: for a benchmark asking "does an approved skill make the next
 *  run better?", a zero delta is the case for saying skills do not help. */
export interface BenchmarkTaskRow {
  task_id: string
  skill: string
  verdict: string | null
  verdict_class: string | null
  reason: string
  delta_points: number | null
  token_ratio: number | null
  arms: Record<string, BenchmarkArmAggregate>
  absent_cells: number
  tool_calls: Record<string, number>
  /** `false` means no contributing cell observed its own spend rows — so `token_ratio` is not
   *  evidence of a token match, and the panel says so rather than printing a bare ratio. */
  spend_observed: boolean
  /** Carried from `AttemptRecord.estimated`: tokens are heuristic, not provider-reported. Any
   *  published ratio must carry that word (protocol §4). */
  spend_estimated: boolean
  notes: string[]
}
/** A task the runner refused to run at all, with the refusal's own sentence. Reported rather
 *  than omitted: a shorter table would make ten tasks look like however many ran. */
export interface BenchmarkSkippedRow {
  task_id: string
  skill: string
  blockers: string[]
}
export interface BenchmarkReport {
  run_id: string
  created_at: string
  protocol_doc: string
  task_set_version: number
  task_set_fingerprint: Record<string, string>
  trials_per_arm: number
  arms: string[]
  thresholds: {
    inconclusive_band_points: number
    token_match_tolerance: number
    min_trials_per_arm: number
    source: string
  }
  tasks: BenchmarkTaskRow[]
  skipped: BenchmarkSkippedRow[]
  measured_tasks: number
  absent_cells: number
  reproduction?: BenchmarkReproduction
}
/** The §8 (V4) reproduction judgement. The variance is NOT numeric and NOT invented by the
 *  code: `stated_variance` is the protocol's own list of conditions and `stated_variance_source`
 *  cites where it is stated, so a reader can check the tolerance rather than trust it. */
export interface BenchmarkReproduction {
  baseline_run_id: string
  rerun_run_id: string
  reproduces: boolean
  stated_variance: string[]
  stated_variance_source: string
  conditions: Record<string, boolean>
  verdict_changes: { task_id: string; baseline: string | null; rerun: string | null }[]
  notes: string[]
}
export interface BenchmarkView {
  report: BenchmarkReport
  /** The whole frozen register, so a task the report does not carry is still named. */
  register: { task_id: string; skill: string; observable: string }[]
  task_set_version: number
  protocol_doc: string
  stated_variance: string[]
}
/** One arm-mask row of the retrieval ablation (ES-3 / §5.3).
 *
 *  `p_at_k` is `null` when the mask retrieved NOTHING — 0/0, undefined, and deliberately
 *  not 0.0. `no_candidate_queries` is how many queries that was, so the absence is
 *  legible instead of just missing. `r_at_k` is `null` only when the qrels name no
 *  relevant id at all (`undefined_recall_queries`): an empty result list with real labels
 *  is a genuine `0.0` recall and is reported as one. */
export interface RetrievalMaskRow {
  mask: string
  k: number
  p_at_k: number | null
  r_at_k: number | null
  queries: number
  scored_queries: number
  no_candidate_queries: number
  undefined_recall_queries: number
}
/** One arm's leave-one-out marginal contribution and its offline verdict.
 *
 *  `verdict` is 'enable' | 'hold' | 'unmeasured'. `unmeasured` is first-class and covers
 *  three different truths — no delta, too few scored queries, or no executor at all — so
 *  `reasons[0]` is what a reader acts on. An arm with no executor never ran, which makes
 *  its `contribution_p` exactly 0.0; rendering that as "worthless" is the mistake the
 *  verdict exists to prevent. */
export interface RetrievalArmContribution {
  arm: string
  full_p_at_k: number | null
  without_p_at_k: number | null
  contribution_p: number | null
  full_r_at_k: number | null
  without_r_at_k: number | null
  contribution_r: number | null
  solo_p_at_k: number | null
  scored_queries: number
  verdict: string
  reasons: string[]
}
export interface RetrievalStoreReport {
  /** '' when this store has never been benchmarked — a real state, not a missing key. */
  run: string
  table:
    | {
        store: string
        columns: string[]
        rows: RetrievalMaskRow[]
        corpus_snapshot_ref: string
        benchmark_corpus_snapshot_ref: string
        corpus_drifted: boolean
        arm_executors: Record<string, boolean>
        /** `{qrels source: query count}` — the ground truth's provenance, counted
         *  server-side beside the numbers it produced. Optional because a run written
         *  before the census existed has no such key, and that must read as "unstated"
         *  rather than as zero queries from every source. */
        qrels_sources?: Record<string, number>
        queries?: number
        floors: { min_arm_contribution: number; min_scored_queries: number }
      }
    | null
  contributions: RetrievalArmContribution[] | null
  benchmark: { name: string; store: string; queries: unknown[] } | null
}
/** The per-arm retrieval ablation for BOTH stores (ES-3 / §5).
 *
 *  Keyed by store rather than merged: §5.1 runs knowledge and memory SEPARATELY and never
 *  shares a corpus, so one table over both would be the shape the boundary forbids. */
export interface RetrievalBenchView {
  stores: Record<string, RetrievalStoreReport>
  arms: string[]
  masks: string[]
  control_mask: string
  arm_verdicts: string[]
  k: number
  floors: { min_arm_contribution: number; min_scored_queries: number }
}
/** §5.2's hand-labeling card: head queries and the candidates the shipped retriever
 *  actually returns for each, for a human to mark. */
export interface RetrievalLabelCard {
  store: string
  benchmark: string
  candidates_per_query: number
  labelled: number
  mined: number
  queries: {
    query: string
    source: string
    already_relevant: string[]
    candidates: string[]
  }[]
}
/** One pre-registered template A/B study, as the index lists it (ES-5 / §2.4).
 *
 *  `verdict` is `null` for a study that is registered but has not run — a real state, not a
 *  missing field, so the UI renders "not run yet" from data instead of from an absent key.
 *  `agreement` and `win_rate` are `null` for the same reason AND for a second one: an
 *  UNMEASURABLE agreement is why a study is `judge_unreliable`, so drawing it as 0% would
 *  report a catastrophically biased judge where the truth is "we could not tell". */
export interface StudyRow {
  study_id: string
  kind: string
  subject: Record<string, unknown>
  hypothesis: string
  k: number
  registered_ts: number
  /** 'win' | 'loss' | 'tie' | 'invalidated' | 'judge_unreliable', or null when unrun. */
  verdict: string | null
  agreement: number | null
  agreement_floor: number
  win_rate: number | null
  low_power: boolean
  /** '' | 'locked_check_regression' | 'win_rate' | a rubric-pin state. */
  fail_reason: string
  locked_regressions: string[]
}
/** One pair, judged at both positions. `slot_a_arm` is the randomized assignment recorded
 *  OUTSIDE the judge's prompt — publishing it is what makes the blinding auditable. */
export interface StudyPair {
  case_id: string
  trial: number
  slot_a_arm: string
  direct_winner: string
  swapped_winner: string
  outcome: string
  judgeable: boolean
  agreed: boolean
  position_flipped: boolean
  cost_usd: number | null
}
export interface StudyCaseRun {
  case_id: string
  outcome: string
  pairs: StudyPair[]
}
export interface StudyVerdict {
  verdict: string
  wins: number
  losses: number
  ties: number
  no_signal: number
  win_rate: number | null
  agreement: number | null
  agreement_floor: number
  judge_below_floor: boolean
  low_power: boolean
  fail_reason: string
  detail: string
  k: number
  decided_cases: number
  locked_regressions: string[]
  ledger_row_written: boolean
}
/** 🔴 Deliberately WITHOUT the rubric text and without the `locked/` checks. The server
 *  omits them (§2.2: a check the worker can read is a check it satisfies by construction,
 *  and a dashboard is one fetch away from an agent's context), so this type omits them too
 *  — a field declared here would invite a future handler to fill it. */
export interface StudyView {
  study_id: string
  kind: string
  subject: Record<string, unknown>
  hypothesis: string
  k: number
  inputs: string[]
  metric: string
  decision_rule: string
  rubric_sha256: string
  registration_sha256: string
  agreement_floor: number
  budget_usd: number
  registered_ts: number
  locked_check_count: number
  status: 'registered' | 'complete'
  verdict: StudyVerdict | null
  runs: StudyCaseRun[]
  evidence: Record<string, unknown> | null
}
/** LV-4's periodic identity report — the accumulated shape of what has been learned.
 *
 *  Every section is an exact `count` plus a bounded `items` sample, and the two are separate
 *  fields on purpose: rendering `items.length` as the count would under-report the moment a
 *  home got busy. `narrative_status` distinguishes "nobody asked for prose" (`skipped`, what a
 *  GET returns) from "prose was asked for and no model answered" (`unavailable`) — the second
 *  is a degraded delivery the panel says out loud. */
export interface IdentityReportSection<T> { count: number; items: T[] }
export interface IdentityReportFacet {
  text: string; cls: string; stability: number; state: string; updated_at: string; pinned: boolean
}
export interface IdentityReportLesson { text: string; category: string; updated_at: string }
export interface IdentityReportSkill {
  name: string; uses: number; last_used: string; used_in_window: boolean
  aging_state: string; created_at: string
}
export interface IdentityReportProposal { label: string; kind: string }
export interface IdentityReport {
  period: { window_days: number; since: string; until: string }
  window_days: number
  generated_at: string
  total: number
  facets: IdentityReportSection<IdentityReportFacet>
  lessons: IdentityReportSection<IdentityReportLesson>
  skills: IdentityReportSection<IdentityReportSkill>
  proposals: IdentityReportSection<IdentityReportProposal>
  memory: Record<string, number>
  narrative: string
  narrative_status: 'skipped' | 'written' | 'unavailable'
  markdown: string
}
/** The READ route's body: the report plus the delivery cadence beside it.
 *
 *  A separate type rather than an optional field on `IdentityReport`, because the two shapes are
 *  genuinely different: the GET answers with a report AND a setting about future deliveries, while
 *  the POST answers with what it just WROTE. An optional `cadence?` would let the panel's control
 *  render off `undefined` on either shape without the compiler noticing which one it had. */
export interface IdentityReportView extends IdentityReport {
  /** `learning.identity_report_cadence`. `''` means the server could not READ the config, which
   *  the panel must state rather than render as a saved value. */
  cadence: '' | 'monthly' | 'weekly' | 'off'
}
export interface IdentityReportDelivery {
  artifact_slug: string
  artifact_version: number
  inbox_item_id: string
  report: IdentityReport
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
  // PEP-7's mirrored artifact. Not authorable and never listed — it reaches the UI only
  // through a search result, which is why it is absent from `TYPES` (the create picker's
  // catalog) and carried by `ARTIFACT_TYPE` instead.
  | 'artifact'
  // PROACTIVE-ASSISTANT §2.2's decision. Absent from `TYPES` for the same reason as `artifact`
  // but a DIFFERENT missing half: logging a decision also mints its one-shot review trigger, so
  // one authored from the create picker would be a decision that never comes back. It still needs
  // a label/icon/tone because a search result CAN be one — without it `resolveType` fell through
  // to `note` and every decision in the library read "Note".
  | 'decision'
export interface KnowledgeEntity { id: string; name: string; entity_type?: string; description?: string }
export interface KnowledgeRelation { id: string; source_name?: string; target_name?: string; relation_type?: string; weight?: number }
export interface KnowledgeItem {
  id: string; title?: string; content?: string; summary?: string
  item_type?: string; tags?: string[]
  provider?: string; status?: string
  /** A source item's origin identity. For PEP-7's mirrored artifacts `guid` IS the artifact
   *  slug, which is what lets a search hit link back to the artifact itself. */
  source_id?: string | null; guid?: string | null
  is_pinned?: boolean; is_archived?: boolean
  // library curation (KNOWLEDGE-LIBRARY S1). read_state is a three-value cycle, not a
  // boolean — "reading" is the state a reading list exists to represent.
  read_state?: 'unread' | 'reading' | 'read'; favorited?: boolean
  created_at?: string; updated_at?: string
  _score?: number; _match_type?: string
  // The SEMANTIC kind (`semantics.KINDS`), distinct from `item_type`/`type` which routes the
  // ingestion graph. Serialized by every item response but never declared here until KL-19 gave
  // a surface a reason to read it — the restructure panel's change-kind verb. Nullable because
  // most items have never been assigned one.
  kind?: string | null
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
  // populated by GET /items/{id}/related. `score` is the RANKING key (KL-13: a cosine
  // similarity edge above `knowledge.similarity_min_score`), and `chunk_index` /
  // `neighbour_chunk_index` are its provenance — oriented to the item asked about, so a
  // surface can explain WHY two items are related. `shared_entities` survives but is now
  // descriptive rather than the thing that chose the ordering.
  score?: number
  chunk_index?: number
  neighbour_chunk_index?: number
  shared_entities?: number
}
/** The ingestion node-graph shape for an item's type — nodes + edges + terminals. */
/** Whether a SYNTHESIZED item (insight/report/overview) has been overtaken by its sources.
 *
 *  `new_source_items` is the count the banner names. A synthesis that silently serves a
 *  stale document is the defect this answers: a reader cannot tell a current article from
 *  one written before half its sources arrived. `scope` is the server's own one-phrase
 *  account of what counted as new material, so the number is defensible rather than
 *  mysterious. */
/** What counts as material for a scheduled research report.
 *
 *  Two scopes, deliberately separate: `source` decides what counts as NEW MATERIAL (and so
 *  whether the report has anything to say), while `context` decides what may be SEARCHED
 *  while writing. Collapsing them is how a report ends up citing background it was never
 *  asked to monitor. `window_secs: 0` means "since this report's own watermark". */
export interface ResearchScope {
  tags: string[]
  window_secs: number
}

/** A scheduled research report definition (WF2KNO-12).
 *
 *  `citation_policy` is the third leg of the triple: `cite-source-only` registers only the
 *  new material as citable, `allow-citing-context` also registers the context scope. That
 *  triple is what makes a contradiction scan or an open-question tracker a configuration
 *  rather than another code path. */
export interface ResearchReport {
  id: string
  name: string
  prompt: string
  schedule: { kind: string; every_secs?: number | null; at_ts?: number | null; cron_expr?: string | null }
  tz: string
  source: ResearchScope
  context: ResearchScope | null
  citation_policy: 'cite-source-only' | 'allow-citing-context'
  iteration_cap: number
  enabled: boolean
  created_ts: number
  last_run_ts: number | null
  last_status: string
  last_error: string
  watermark_ts: number
}

export type ResearchReportInput = Omit<
  ResearchReport,
  'id' | 'created_ts' | 'last_run_ts' | 'last_status' | 'last_error' | 'watermark_ts'
>

export interface KnowledgeStaleness {
  item_id: string
  stale: boolean
  /** Distinct source items created or updated since the synthesis. */
  new_source_items: number
  /** Cited sources whose own text moved after the synthesis. */
  changed_sources: number
  checked_at: string
  scope: string
}

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
// ── Watched sources (WATCHED-SOURCES §2.4/§6.3/§12) ──
/** What the user can DO about a source's last poll. The backend resolves this from the
 *  provider's own guidance constants, so the UI never carries a copy of a remediation
 *  message — and the two kinds are deliberately distinct: a listing-page failure is fixed
 *  by a different URL, a render-tier failure by a budget knob. */
export interface SourceRemediation {
  /** '' when the source needs nothing; otherwise 'listing_page' | 'render_tier'. */
  kind: string
  /** The provider's own remediation text, full-length (the stored poll summary is clipped). */
  guidance: string
  /** The poll's reason, when it says something the guidance does not (a render tier that
   *  raised, or one allowed but not installed). Empty when it would just echo the guidance. */
  detail: string
  /** '' = advice only. 'allow_render' = one knob fixes it. 'edit_url' = point it elsewhere. */
  action: string
}
/** One watched source: its spec, its schedule, and the rollups the poll engine writes. */
export interface WatchedSource {
  id: string; name: string; provider: string; kind: string
  spec: Record<string, unknown>; budget: Record<string, unknown>
  /** 'full' | 'raw' — 'raw' is §6.3's structural no-AI promise, and what the chip reads. */
  enrichment: string
  poll_interval_secs: number; item_type: string; enabled: boolean
  created_at?: string; updated_at?: string
  last_poll_at?: string | null; next_poll_at?: string | null
  last_new_count?: number
  /** One of the backend's SOURCE_HEALTH vocabulary (shipped in the list response). */
  health_status?: string
  last_error_summary?: string
  /** The tiers the last poll had to climb, or was refused. */
  last_escalations?: string[]
  /** Is a poll-capable provider registered for this row? False = nothing will poll it. */
  enrolled: boolean
  /** Fed by an in-process change listener rather than a poll (PEP-7's artifact mirror).
   *  True means `enrolled: false`, `poll_interval_secs` and `last_poll_at` describe a
   *  mechanism this row does not use — the row must not be read as a broken poller. */
  event_driven?: boolean
  remediation: SourceRemediation
}
/** One creatable source kind, derived from the registered providers. `previewable` is
 *  MEASURED per provider — only the web kind has a detect-then-tune loop, so the create
 *  flow must not pretend feeds and directories have a dry run. */
export interface SourceKind {
  provider: string; display_name: string; kind: string
  /** Which create form to render: 'web_page' | 'feed' | 'dir' | 'spec'. */
  form: string
  previewable: boolean
  poll_interval_secs: number
  default_item_type: string
  detectors?: string[]
  max_requests?: number
  formats?: string[]
  presets?: string[]
  default_include?: string[]
  max_files?: number
  guidance?: Record<string, string>
}
export interface SourcesResponse {
  sources: WatchedSource[]
  kinds: SourceKind[]
  /** The closed health vocabulary, shipped rather than retyped in TypeScript. */
  health_statuses: string[]
  raw_enrichment: string
}
/** One bundled source recipe (§7.2) — a site shape somebody already worked out.
 *  `spec` on a MATCH arrives already resolved from the pasted URL's capture groups, so the
 *  create flow saves what it was shown rather than re-deriving it. */
export interface SourceRecipe {
  id: string; displayName: string; description: string
  /** Which registered provider polls it, and the WatchedSource `kind` that implies. */
  provider: string; kind: string
  itemType: string; enrichment: string
  matchPatterns?: string[]
  urlGuidance?: string
  spec: Record<string, unknown>
  tags?: string[]
  /** Present only on a match: the capture groups the URL supplied. */
  groups?: Record<string, string>
}
export interface SourceRecipesResponse {
  recipes: SourceRecipe[]
  /** Present only when a URL was supplied. Empty means "nobody has covered this site". */
  matches?: SourceRecipe[]
  url?: string
}
/** One extracted item from a §2.4 dry run. `snippet` is untrusted scraped text, clipped
 *  by the backend and rendered as TEXT only. */
export interface SourcePreviewItem {
  guid: string; title: string; url: string; published_at: string; snippet: string
}
export interface SourcePreviewResult {
  items: SourcePreviewItem[]
  /** Which detector won, so the user tunes something named. */
  detector: string
  escalations: string[]
  requests_used: number
  /** The remediation to show when `items` is empty (a tuning problem, not a failure). */
  guidance: string
  health_status: string
  /** A HARD failure (egress denial, invalid spec) — distinct from an empty extraction. */
  error: string
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
export type InboxItemStatus = 'pending' | 'seen' | 'sent' | 'dismissed' | 'handled' | 'filtered'
// What kind of attention an item wants. 'message' is the default so every item written
// before the inbox became a general attention store stays valid.
// 'user_note' (INU-9) is the one kind a PERSON writes; every other member is synthesized by
// the system, so the value itself carries the provenance a consumer needs.
export type InboxItemKind =
  | 'message' | 'mention' | 'email' | 'agent_request'
  | 'proposal' | 'needs_input' | 'digest' | 'system' | 'user_note'
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
  // A ref value is usually an id STRING (`refs.loop`, `refs.session`), but INU-7's C6
  // payload rides here too under `refs.proposal` — hence the widened value type. Read the
  // typed payload through `proposalOf()` rather than indexing this directly.
  refs?: Record<string, any>
}
/** INU-7 C6 — the proposal payload carried in `refs.proposal` on a `proposal` item.
 *  `apply` holds EXACTLY ONE of `action` / `workflow` / `skill_promotion` / `app_callback`;
 *  the backend refuses zero, two, or an unknown key rather than guessing. */
export interface InboxProposal {
  title: string
  preview: string
  preview_kind: 'text' | 'diff'
  provenance: string
  expires_at?: string | null
  editable: boolean
  apply: Record<string, Record<string, unknown>>
}
/** What one apply returned. `ok:false` arrives with HTTP 200: the apply failed, the item is
 *  still PENDING, and `error` is what to show — a status code could not say that. */
export interface InboxProposalApplyResult {
  ok: boolean
  case?: string
  result?: Record<string, unknown>
  error?: string
  item?: InboxItem
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
// `integrity_ok` is the server's per-row HMAC recheck: false = THIS record was altered
// on disk. Computed on the raw line before redaction, so it is a real verdict.
export interface SelEvent {
  event_id: string; timestamp: string; event_type: string; caller_identity?: string
  agent?: string; source?: string; operation?: string; tool_kind?: string; outcome?: string
  resources?: string; error?: string; prev_hash?: string; entry_hash?: string
  downstream_service?: string; request_id?: string; integrity_ok?: boolean
}
// One page of /api/security/audit. `next_cursor` empty = no further page (the server
// only hands out a cursor once it has seen a match beyond the page). `truncated` = the
// bounded tail scan filled up, so older records may exist beyond the window.
export interface AuditPage {
  events: SelEvent[]; count: number; next_cursor: string; scanned: number; truncated: boolean
  // The outcome filters, shipped by the module that owns the log's vocabulary
  // (`sel.AUDIT_OUTCOME_FAMILIES`). `values` are matched ANY-OF server-side, so a family is
  // one query and the pill cannot disagree with the pagination cursor. The dashboard used to
  // keep its own two-word list here and missed most of what the writers emit.
  outcome_families: { key: string; label: string; values: string[] }[]
}
// Server-side filters for the audit read. Empty strings are omitted by the caller —
// an unknown key is REFUSED by the endpoint, never ignored.
export interface AuditFilters {
  caller?: string; operation?: string; outcome?: string; downstream_service?: string
  since?: string; until?: string
}
// `ok` = every checked record's HMAC verified. `windowed` = only the recent window was
// checked (the default; `full` walks the whole chain).
export interface SelVerify {
  ok: boolean; checked: number; valid?: number; tampered?: number; windowed?: boolean
  /** The entry cap the server applied (`null` for an exhaustive check). `windowed` says a cap was
   *  SET; this says how big it was — the only way a consumer can tell "stopped at 5000" from
   *  "5000 is the whole log". */
  window?: number | null
  error?: string
}
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
  /** v3 only (§6). `scope` is 'full' | 'partial'; `verified` says whether the archive's
   *  per-member checksums were CHECKED — false for a v1/v2 archive, which carries none. */
  scope?: 'full' | 'partial'
  domains?: string[]
  domain_counts?: DurabilityDomainCounts
  excluded?: string[]
  verified?: boolean
}
/** `applied: false` is the validate-only answer to an import with no `mode`. */
export interface DurabilityImportResult {
  ok: boolean
  applied?: boolean
  error?: { code: string; message: string }
  summary?: { mode: string; items: string[]; refused?: string[]; pre_restore?: string }
  manifest?: PortabilityManifest
}
export interface DurabilityRestoreResult {
  ok?: boolean
  plan?: boolean
  error?: { code: string; message: string }
  [k: string]: unknown
}
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
/** A cue voice a per-kind rule may name for a mobile push (MOBILE-COMPANION MC-6) — the closed
 *  set in `web/src/design/soundCues.ts` (`CueName`). A service worker cannot play audio, so it
 *  maps a push's kind to this voice and an open client plays it; `null` means a silent push. */
export type NotificationSound = 'turn_complete' | 'approval_needed' | 'error' | 'coin_blip' | 'terminal_bell'
export interface NotificationRuleRow {
  key: string; source: string; kind: string; label: string; severity: number
  mode: NotificationMode
  /** The registry default, so the UI can show "changed from default". */
  default_mode: NotificationMode
  /** True when the user has an explicit stored rule for this kind. */
  configured: boolean
  targets: NotificationTarget[]
  conditions: { keywords: string[]; name_mention: boolean }
  /** The cue voice an open device plays for a push of this kind, or null for a silent push. */
  sound: NotificationSound | null
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
  /** null clears the sound (back to a silent push). */
  sound?: NotificationSound | null
}
export type MemoryVaultMode = 'off' | 'mirror' | 'two_way'
/** GET /api/memory/settings. Not every field is written the same way: the retention +
 *  behaviour + vault fields ride the PUT on this same path, while `graph_topology_in_context`,
 *  `holder_attribution` and `slot_size_cap` ride the `_EDITABLE_CONFIG` PATCH — one writer
 *  each, never two. See `SettingsTab`'s `patch` vs `patchCfg`. */
export interface MemorySettings { history_idle_hours: number; history_max_days: number; migrated?: boolean; l1_manifest?: boolean; active_recall?: boolean; proactive_commitments?: boolean; vault_mode?: MemoryVaultMode; vault_path?: string; graph_enabled?: boolean; push_context?: boolean; push_min_confidence?: number; graph_topology_in_context?: boolean; holder_attribution?: boolean; slot_size_cap?: number }

// ── The triage digest (PROACTIVE-ASSISTANT §5.1/§5.2/§5.4 — PA-5) ──

/** The digest card's state, and the whole reason it is a UNION of five and not a list.
 *
 *  A card that drew an empty section for every one of these would tell the user "nothing
 *  happened" when the truth was "you never installed it", "you switched it off", or "the read
 *  failed". `error` in particular is not a fallback — the server sets it deliberately when a
 *  store read raised, because an empty digest is the most reassuring sentence in the app and it
 *  must never be produced by a failure. */
export type TriageDigestState = 'uninstalled' | 'off' | 'never_run' | 'ready' | 'error'

/** One thing the machine did on its own, with the handle that takes it back.
 *  `undoable` is false when the provider had nothing to reverse — recorded, not papered over,
 *  so the card offers Undo only where an undo exists. */
export interface TriageAutoDone {
  ordinal: string; source_id: string; action_type: string; provider: string
  rule: string; reversal: string; undoable: boolean; ok: boolean; error: string
  permalink: string; title: string; source: string; item_permalink: string; materiality: string
}

/** One proposal still waiting on the user. `tier` drives the badge; `pattern_key` is what an
 *  "always" answer teaches — blank means the run recorded no pattern, and the card must not
 *  offer "always" for it rather than inventing one. */
export interface TriagePending {
  ordinal: string; action_type: string; tier: string; pattern_key: string; clamped: boolean
  reason: string; rule: string; answered: boolean; answer: string
  permalink: string; title: string; source: string; item_permalink: string; materiality: string
}

/** One ledger row in the "what your machine did" section, permalinked to the run journal. */
export interface TriageLedgerRow {
  kind: string; seq: number; ordinal: string; action_type: string; rule: string
  outcome: string; reason: string; detail: string; verb: string; permalink: string
}

export interface TriageSchedule { id: string; name: string; cron: string; enabled: boolean; created_by: string }

// ── The Decision Journal (PROACTIVE-ASSISTANT §2.5/§5.3 — PA-6) ──
/** One decision, exactly as `decisions.projection` flattens it. Mirrored field-for-field and
 *  NOT reshaped: the journal view reads these names, and a client-side rename would be a second
 *  place that knows the decision metadata shape. */
export interface DecisionRow {
  id: string; summary: string; status: string; domain: string
  expectation: string; confidence: number | null
  review_horizon: string; reminder_trigger_id: string | null
  /** How many times a `too_early` re-armed the review. Capped at 2 by the backend. */
  deferrals: number
  /** The cap was reached: still unresolved, and no reminder is coming. Not "overdue". */
  stale_pending: boolean
  outcome: string | null; outcome_grade: string | null; outcome_captured_at: string | null
  /** Soft string reference into the memory store; null when the lesson write was refused. */
  lesson_memory_key: string | null
  created_at: string
  /** Derived at read time from the horizon, never stored. */
  overdue?: boolean
}

/** One domain's calibration bucket, straight from `decisions.calibration`.
 *
 *  `count_honest` is the load-bearing field: FALSE means n is below the threshold and no rate
 *  here may be rendered as a rate. `as_expected_rate`/`mean_confidence` are still present in
 *  that case — the view must not draw them, exactly as `evidenceLabel` renders `ungraded`
 *  rather than substituting a grade and `optimize` reports `unscored` rather than `0.0`. */
export interface CalibrationBucket {
  n: number
  better: number; as_expected: number; worse: number
  mean_confidence: number | null
  as_expected_rate: number | null
  count_honest: boolean
}

export interface DecisionJournalView {
  decisions: DecisionRow[]
  /** Per-domain, and EMPTY when nothing resolved has a calibratable grade. An empty object is
   *  "nothing to calibrate from", which the strip must say out loud — never a flat line. */
  calibration: Record<string, CalibrationBucket>
  /** The threshold `count_honest` was computed against, forwarded so the caveat can name it. */
  calibration_min_n: number
  statuses: string[]; domains: string[]; grades: string[]
}

export interface TriageDigestView {
  state: TriageDigestState
  enabled: boolean
  installed: boolean
  error: string
  workflow?: string
  node_id?: string
  schedule?: TriageSchedule | null
  /** True when the config switch and the schedule's own flag disagree. Reported, not silently
   *  repaired on a read: two switches that diverged is something the user should see. */
  schedule_drift?: boolean
  run_id?: string
  status?: string
  finished_at?: string
  permalink?: string
  window_start?: string
  title?: string
  body?: string
  /** 🔴 "handed to the notification gate", NOT "the user saw it". `DashboardState.notify`
   *  returns nothing, so the run cannot know whether quiet hours held the digest back — measured
   *  by driving one inside a quiet window: the run said delivered while the notification list did
   *  not grow. Never render this as "delivered". */
  handed_to_notify?: boolean
  /** The quiet-hours window, so an absent notification can be EXPLAINED rather than read as a
   *  broken notification system. `known: false` = the settings could not be read, which is not
   *  the same as "quiet hours are off". */
  quiet_hours?: { known: boolean; enabled: boolean; start: string; end: string; mute_all: boolean }
  collected?: number
  lanes?: Record<string, number>
  dropped?: number
  /** False = the auto-execution stage did not run at all. NOT the same as "it ran and did
   *  nothing", which is `true` with an empty `auto_done` — the card must say different things. */
  auto_stage_ran?: boolean
  auto_done?: TriageAutoDone[]
  pending?: TriagePending[]
  budget_breached?: boolean
  budget_reason?: string
  degraded?: boolean
  machine_did?: TriageLedgerRow[]
  /** False = rows that should exist were NOT written. Never "there were none". */
  ledger_complete?: boolean
  ledger_rows?: number
}

/** The reply outcome, and note what is NOT here: `expired`. A stale digest is refused with a **409**
 *  carrying `error.code: "triage_digest_expired"`, so it REJECTS through `ApiError` (status 409)
 *  rather than resolving — a resolved `outcome: 'expired'` is a shape the api layer cannot produce,
 *  and a handler written for it would be dead code. */
export interface TriageReplyResult {
  ok: boolean
  outcome: 'acted' | 'help'
  /** Why the grammar refused, when it did. A help line, never an interpretation. */
  help_reason?: string
  help?: string
  results?: Array<{
    ordinal: string; outcome: 'acted' | 'already' | 'unknown'; verb?: string
    executed?: boolean; detail?: string; rule?: string; rule_error?: string
    /** False = the answer was not durably recorded, so the next tap would act again. */
    recorded?: boolean
  }>
}

/** One taught approval rule, as `GET /api/memory/approval-rules` returns it.
 *  `send_capable` is §5.2's graduation toggle: OFF, an approved `reply_draft` still only ever
 *  drafts (the `inbox-op` provider has no send path at all), so the toggle is a statement of
 *  intent that a send-capable provider would honour — not a switch that starts sending. */
export interface ApprovalRuleRow {
  key: string; pattern: string; verdict: 'approve' | 'deny' | 'suppressed'; scope: string
  hit_count?: number; expires_at?: string | null; send_capable?: boolean
  created_from_digest?: string | null; specificity?: number
  created_at?: string | null; updated_at?: string | null
  suppressed_until?: string | null; suppression_rung?: number
}

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
export interface MemoryVaultStatus { enabled: boolean; mode: MemoryVaultMode; path: string; files: number; exists: boolean }
export interface MemoryVaultSyncResult { records: number; files: number; written: number; pruned: number; path: string; mode: MemoryVaultMode; absorbed: number; rejected: number; conflicts: number; raw_ingested: number; seeded: number }
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
//
// `standing` is the injection gate (WF2LEA-15): `injected` is in the prompt right now,
// `retained` is stored and still gathering evidence but deliberately kept OUT of it —
// a declared state, not a deletion. `confidence` is DERIVED from the evidence counters
// beside it (never assigned), and `confidence_reason` is the server's sentence for why
// this lesson stands where it does; the frontend must not recompose that reasoning, or
// the studio and the gate would eventually disagree.
export type LessonStanding = 'injected' | 'retained'
export interface Lesson {
  rule: string; category: string; ts?: string
  standing?: LessonStanding; confidence?: number; confidence_reason?: string
  observations?: number; contradictions?: number; reversals?: number
}
// The auto-linked memory graph: fact nodes (grouped by key namespace) + relations.
// `ref` is a stable un-hashed handle onto the source memory (`sem:<key>`, `lesson:<rule>`,
// …) — the Memory Studio maps a selected list entry to its node by ref, not by re-hashing.
export interface MemoryGraphNode { id: string; label: string; group?: string; title?: string; ref?: string }
export interface MemoryGraphEdge { from: string; to: string }
export interface MemoryGraphData { nodes: MemoryGraphNode[]; edges: MemoryGraphEdge[] }
// The ENTITY topology (MEMORY-GRAPH-AND-VAULT §7.2) — distinct from MemoryGraphData, which is
// the record-level visualization. `community` is the Louvain partition the topology block also
// describes, so colouring by it cannot disagree with what the model is told.
export interface MemoryEntityNode {
  id: string
  name: string
  entity_type: MemoryEntityType
  aliases: string[]
  community: number | null
  inbound_count: number
}
/** An edge exists when at least one record links both entities; `records` is how many do.
 *  `confidence` is that best-supporting record's weaker leg — see `MemoryGraphStore.entity_graph`. */
export interface MemoryEntityEdge {
  from: string
  to: string
  records: number
  link_types: string[]
  provenances: string[]
  confidence: number
}
export interface MemoryEntityGraph {
  nodes: MemoryEntityNode[]
  edges: MemoryEntityEdge[]
  enabled: boolean
}
/** A record's outbound entity link. `entity_name` is resolved server-side — a row holding
 *  only `ent_9f2c` names nothing, and the name IS the evidence tag the inspect view shows. */
export interface MemoryRecordLink extends MemoryLink { entity_name: string }
/** A recurring unknown name awaiting an accept/reject decision (the notability gate). */
export interface MemoryEntityProposal {
  name: string
  mention_count: number
  first_seen_at: string
  last_seen_at: string
  refs?: string
}
// Memory slots (§6) — the bounded registers injected every session. A built-in with
// `materialized: false` has no row yet (MGAV-8 keeps them lazy); the editor still lists it so
// the first line can be written. `cap_chars` is fixed in code per slot; `block_limit` (the
// whole block's budget) is the one configurable number.
export interface MemorySlotLine {
  text: string
  added_at: string
  tombstoned: boolean
  tombstoned_by: string
  reinforcements: number
}
export interface MemorySlot {
  name: string
  title: string
  description: string
  cap_chars: number
  scope: string
  builtin: boolean
  materialized: boolean
  live_chars: number
  live_count: number
  lines: MemorySlotLine[]
}
export interface MemorySlotsResponse { slots: MemorySlot[]; block_limit: number }
/** An append's outcome. `ok: false` with a `proposal` is the cap rejection — nothing was
 *  written, and `proposal.drop_candidates` is what would have to go for it to fit. */
export interface MemorySlotAppendResult {
  ok: boolean
  lines?: MemorySlotLine[]
  error?: string
  proposal?: MemorySlotTrimProposal
}
/** What an over-cap append would cost — the 409 body, so the human picks what to drop. */
export interface MemorySlotTrimProposal {
  slot: string
  cap_chars: number
  current_chars: number
  incoming_chars: number
  over_by: number
  drop_candidates: string[]
  message: string
}
export interface SecurityStats { denied_commands: number; suspicious_patterns: number; tool_schemas: number; redaction_paths: number }
/** The packaged baseline's identity, as served by /api/security/denied-commands.
 *  `verified` is whether the file on disk still matches the fingerprint captured at
 *  import — it is an anti-drift check, NOT a claim that the baseline cannot be changed
 *  by whoever owns the machine. See docs/security/threat-model.md. */
export interface DenylistBaseline {
  version: number
  sha256: string
  /** Patterns actually enforced — unchanged by a diverged file, which is refused. */
  count: number
  verified: boolean
  /** Why `verified` is false; empty when it is true. */
  detail: string
}
export interface DeniedCommands {
  builtin: string[]
  user: string[]
  baseline: DenylistBaseline
  /** User patterns that genuinely widen the set — duplicates of a built-in are deduped
   *  server-side and do NOT count, so this can be lower than `user.length`. */
  user_additions: number
}
export interface EgressPolicyConfig { allow_hosts: string[]; deny_hosts: string[]; allow_private: boolean }
/** Where this instance's credentials live, and whether the move is reversible (SH-2).
 *
 *  `backend` is the RESOLVED outcome; `requested` is the intent. `blocked` is the mismatch
 *  named once on the server — a box that asked for a keychain it does not have must not
 *  render as ready to migrate, and deriving that in TypeScript is how the two surfaces
 *  disagree. **No field here ever carries a secret value** — `pending_keys` is names only. */
export interface CredentialStoreState {
  migration: string
  backend: 'keychain' | 'dotenv'
  requested: 'keychain' | 'dotenv'
  blocked: boolean
  pending_keys: string[]
  pending: number
  keychain_keys: number
  rollback_available: boolean
  snapshot_name: string
  verified: boolean
  verification: { checked: number; missing: string[]; still_in_dotenv: string[] }
}
export interface CredentialMoveResult extends CredentialStoreState {
  ok: boolean
  reason: string
  moved: string[]
  already: string[]
  failed: string[]
}
/** One thing that references a secret, derived from the specs that exist (EI-10). */
export interface SecretConsumerWire {
  kind: 'workflow' | 'trigger'
  id: string
  label: string
}
/** One secrets-vault row.
 *
 *  🔴 **THERE IS NO VALUE FIELD, AND THERE MUST NEVER BE ONE.** The backend's
 *  `SecretPresence` has no value field either — presence-only is a type on both sides, not a
 *  redaction step, so adding `value?: string` here would be declaring a field the server
 *  cannot send and inviting a future handler to make it sendable.
 *
 *  `scope` is the three-way trust story, and the panel MUST render the three differently:
 *  `global` and `project` values live in the credential store; a `host` value lives in the
 *  gateway's own environment, so the vault can neither rotate nor remove it. */
export interface SecretPresenceWire {
  name: string
  scope: 'global' | 'project' | 'host'
  project_id: string
  present: true
  inherited_from_host: boolean
  consumers: SecretConsumerWire[]
}
export interface SecretsVaultState {
  secrets: SecretPresenceWire[]
  counts: { total: number; global: number; project: number; host: number }
  /** The server-composed "what to do next" sentence for an empty vault; '' when non-empty.
   *  Server-composed on purpose — the CLI and the dashboard must say the same thing. */
  empty_hint: string
}
export interface SecretWriteResult {
  secret: SecretPresenceWire | Record<string, never>
  secrets: SecretPresenceWire[]
}
export interface SecretDeleteResult {
  deleted: string
  project_id: string
  secrets: SecretPresenceWire[]
}
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
  /** Below the retire threshold AND of a kind whose output is actually withheld
   *  (`feedback.ENFORCED_SUPPRESSION_KINDS` — today only `skill_synthesis`). */
  suppressed?: boolean
  /** Below the retire threshold but of a kind with NO surfacing gate: the producer keeps
   *  surfacing and gets the retire proposal only. Reporting these as `suppressed` told the
   *  user five of six producer kinds had "stopped surfacing" when they had not. */
  proposal_only?: boolean
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

// GET /api/push — what a browser needs to subscribe, plus what already has (MC-5 §C3).
// `vapid_public_key` is the PUBLIC half only, and it is empty unless the backend is
// `webpush`: the field exists to be handed to `pushManager.subscribe`, so publishing it
// under an ntfy backend would advertise a capability that cannot be used.
export interface PushStatus {
  backend: 'webpush' | 'ntfy' | 'none'
  vapid_public_key: string
  vapid_ready: boolean
  ntfy_configured: boolean
  /** Does plan 42's `approval/requested` rule route to the `push` target? The transport and
   *  the routing are two decisions, and a subscribed device with approvals unrouted is
   *  silent for a reason the user cannot otherwise see. */
  approval_targeted: boolean
  devices: string[]
  subscribed: number
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
// One BYO-runner catalog row (EXECUTION-ISOLATION §3.1). `health` is MEASURED
// evidence or `null` for "never probed" — and inside it, `version`/`latency_ms` are
// `null` when that particular value was not measured. The UI must render those as
// unknown; substituting a 0 or a dash-that-looks-like-a-reading is a fabrication.
// `error` is the probe's OWN text and is surfaced verbatim, never summarized.
export interface RunnerHealth {
  ok: boolean; probe: string; checked_at: string
  version: string | null; latency_ms: number | null; error: string | null
  resolved_command: string[]
}
export interface RunnerCapabilities {
  source: string; recorded_at: string
  models: string[]; permission_modes: string[]; efforts: string[]
}
export interface RunnerRow {
  id: string; display_name: string; runtime_id: string; source: string
  dialect: string; bin_names: string[]
  health: RunnerHealth | null
  // Whether `health` is still current per `agent.runner_health_check_secs`. `null` is
  // unknown (never probed, or a timestamp the backend could not parse) — distinct from
  // `false`, which is a positive statement that the reading is fresh.
  health_stale: boolean | null
  capabilities: RunnerCapabilities | null
  adapter: { npm_pkg: string; pinned: boolean; state: string; verified: boolean; detail: string }
  // Who is holding this runner right now (EI-6 §3.1(5)), or `null` for free. Already
  // expiry-filtered server-side: a holder that went quiet past
  // `agent.runner_idle_release_secs` arrives as `null`, so the UI never has to decide
  // whether a stale lease still counts.
  lease: RunnerLease | null
}
export interface RunnerLease {
  holder: string; taken_at: number; expires_at: number; renewals: number
  age_secs: number; expires_in_secs: number
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
  // ── Will it run HERE? (LMMV-8) ──────────────────────────────────────────────────────────
  // Only rows from a LOCAL provider carry these; a hosted/remote row carries none of them.
  // So an ABSENT `fit` is not the same as `fit: 'unknown'`: absent means "this is not a
  // local model, the question does not apply" (no chip), while 'unknown' means "it is local
  // and we could not decide" (a chip that says so). `fit_reason` is the sentence the backend
  // composed and the ONLY accessible name for the verdict — a colour is not a state.
  // 🪤 `quoted_size_mb` IS NOT THE VERDICT'S BASIS. It is the family's MEDIAN variant, while the
  // verdict is judged against this row's OWN `size_mb` (falling back to the quote only for a row
  // that publishes no size). Judging every row by the median made a 16 GB variant read yellow on
  // an 8 GB machine — exactly the promised-fit-that-OOMs this feature exists to prevent. So a
  // tagged row must state its own size and label the quote as the family's, never print the quote
  // as if it were this row's bytes.
  //
  // `fit_step_down` is the NAME of the largest variant in the family that DOES fit — non-null only
  // on a `red` row, and null on an unmeasured host. It lives in the payload rather than in the
  // download handler on purpose: substituting inside the POST would hand back a job whose name,
  // SSE stream key and byte progress all belong to a model the user never asked for. So the
  // substitution is an OFFER the UI makes, not a swap the server performs.
  fit?: ModelFitVerdict; fit_reason?: string; fit_need_mb?: number; quoted_size_mb?: number
  fit_step_down?: string | null
  // NOT a wire field — the client denormalizes the response's top-level `fit` onto each row.
  // See `api.modelsAvailable` for why.
  host_fit?: HostModelFit
}
// One local model's headroom verdict against this host. 'unknown' is a first-class answer,
// not an error: an unmeasurable host must produce it rather than a guessed red.
export type ModelFitVerdict = 'green' | 'yellow' | 'red' | 'unknown'
// The HOST's fit budget — a TOP-LEVEL fact of /api/models/available describing the machine,
// not any one model (LMMV-8, mirrors local_models/fit.py).
//
// `budget_mb` is **null** when the host could not be measured — never 0, because 0 would
// compare as "nothing fits" in every arithmetic reader. `measured` is the same guarantee
// stated separately. Both exist so a consumer can refuse to spend "we could not tell" as
// "it does not fit": `hide_unrunnable` (the user's config default for the browse filter)
// MUST NOT hide a single row while the budget is unknown.
export interface HostModelFit {
  budget_mb: number | null
  total_ram_mb: number
  unified_memory: boolean
  gpu_model: string
  measured: boolean
  hide_unrunnable: boolean
}
export interface ProviderModels {
  name: string; displayName?: string; type: string; models: AvailableModel[]
  error?: string; searchable?: boolean; local?: boolean
  // Denormalized from the response top level, like `AvailableModel.host_fit`.
  host_fit?: HostModelFit
}
// The raw /api/models/available envelope. `fit` is absent on a host that predates LMMV-8's
// budget probe, which reads as "unknown" everywhere downstream.
export interface AvailableModelsResponse { providers: ProviderModels[]; fit?: HostModelFit }
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
// One resident model occupying RAM right now (matches loaded_occupants() in
// local_models/residency.py, LMMV §7). `rss_mb` is null for an in-process model — the
// gateway's heap cannot be attributed per-model, so the honest value is "unknown", never a
// fabricated split. `is_active` is ATTRIBUTION, not liveness: false means still loaded but
// no longer bound to any use case, which is the reclaimable case.
export interface LoadedModel {
  provider: string; model: string
  kind: 'in-process' | 'sidecar'
  rss_mb: number | null
  is_active: boolean
  generation?: number; pid?: number
}
// A system memory snapshot with the local_models.pressure_warn_pct threshold applied.
// `source: 'unavailable'` means the host's memory could not be read — every number is 0
// and `warn` is false, because a false alarm about memory is worse than no alarm.
export interface MemoryPressure {
  total_mb: number; used_mb: number; available_mb: number; used_pct: number
  warn_pct: number; warn: boolean
  source: 'vm_stat' | 'meminfo' | 'unavailable'
}
export interface ResidentProvider {
  provider: string; display_name: string; ok: boolean
  state: 'ready' | 'loading' | 'unavailable'
  kind: 'in-process' | 'sidecar'
  sidecar: { generation: number; restarts: number; rss_mb: number; alive: boolean } | null
}
export interface ResidencySnapshot {
  loaded: LoadedModel[]; providers: ResidentProvider[]; pressure: MemoryPressure
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
  followup_chips: boolean; offer_check_work: boolean; stream_reveal: 'smooth' | 'immediate'
  // MI-4 master opt-in for the composer's screen-share control. OFF by default; the
  // server refuses a frame while it is off, so this is a real gate, not just UI state.
  screen_share_enabled: boolean
  // DFE-5 master opt-in for editing a generated office document in place. OFF by
  // default; the server refuses `PUT …/model` while it is off, so — like the flag
  // above — this is a real gate rather than a UI preference.
  document_editing: boolean
  // Vestigial server field from the retired customizable-bento dashboard (the
  // grid + per-user layout persistence were dropped in the v2 launcher-forward
  // redesign — everyone gets one curated content-first layout now). No FE
  // consumer reads it; kept only to type the config round-trip until the backend
  // drops the field. Do NOT re-introduce a client layout editor against it.
  dashboard_layout?: { widgets: Array<{ id: string; x: number; y: number; w: number; h: number; hidden?: boolean }>; v: number } | Record<string, never>
}
/** The four essential-app lanes of the first-run flow. `model`/`channel` hold the
 *  chosen app's NAME (or null); `search`/`speech` are "did the user set one up" flags.
 *  Mirrors `_ESSENTIALS_SCHEMA` in `personalclaw/onboarding.py`. */
export interface OnboardingEssentials {
  model: string | null
  search: boolean
  speech: boolean
  channel: string | null
}
/** The resume points of the guided first run, in order — `STEPS` in `onboarding.py`. */
export type OnboardingStep = 'name' | 'essentials' | 'first_success' | 'done'
/** `GET /api/onboarding` — the live readiness triple PLUS the persisted first-run
 *  progress from `entity_settings/onboarding.json`. The readiness fields are computed
 *  per request and never stored; the progress fields are what let a reload resume. */
export interface OnboardingState {
  needs_model: boolean; has_model_provider: boolean; has_chat_binding: boolean
  step?: OnboardingStep
  essentials?: OnboardingEssentials
  first_success?: { knowledge: boolean; trigger: boolean; loop: boolean }
}
/** A partial patch for `POST /api/onboarding/state`. The backend merges at BOTH
 *  levels, so a step sends ONLY what it learned — never a read-modify-write of the
 *  whole document, which would clobber a sibling step's progress. An unknown or
 *  mistyped key is a 400, not a silent drop. */
export interface OnboardingStatePatch {
  step?: OnboardingStep
  essentials?: Partial<OnboardingEssentials>
  first_success?: Partial<{ knowledge: boolean; trigger: boolean; loop: boolean }>
}
/** One thing another local agent tool holds that PersonalClaw could adopt (PEP-5).
 *  `existing` is the server's answer, from the fingerprint ledger of what THIS
 *  importer already wrote — so a re-entered first run marks an item instead of
 *  offering it again. `redactions` is a COUNT; the matched values never leave the
 *  scanner. */
export interface OnboardingImportItem {
  fingerprint: string; source: string; category: string; key: string; title: string
  redactions: number; existing: boolean
}
/** What one source's scanner found. `detected` is computed server-side (present on
 *  this machine AND holding something), so "did we find it" is decided once. */
export interface OnboardingImportSource {
  source: string; display_name: string; root: string; present: boolean; detected: boolean
  counts: Record<string, number>
  items: OnboardingImportItem[]
  secrets_skipped: number; redactions: number
  notes: string[]
}
/** `GET /api/onboarding/import` — every registered source (found or not) plus the
 *  closed category vocabulary, in the writers' declaration order. */
export interface OnboardingImportScan {
  sources: OnboardingImportSource[]
  categories: string[]
}
/** What happened to ONE item at its destination. The four-value vocabulary is
 *  closed: `conflict` means something different was already there and was KEPT,
 *  `rejected` means a security floor refused it. Neither is a silent success. */
export interface OnboardingImportOutcome {
  fingerprint: string; source: string; category: string; key: string
  outcome: 'imported' | 'existing' | 'conflict' | 'rejected'
  destination: string; detail: string
}
/** `POST /api/onboarding/import` — per-item outcomes plus what was withheld. */
export interface OnboardingImportReport {
  counts: Record<string, number>
  results: OnboardingImportOutcome[]
  secrets_skipped: number; redactions: number
  notes: string[]
}
export interface ChatModelOption { name: string; model_id: string; provider: string; description?: string }
export interface SavedAgent {
  name: string; provider: string; provider_agent?: string; acp_mode?: string; model?: string; approval_mode?: string
  description?: string; system_prompt?: string; voice?: string; skills?: string[]; tools?: string[]; triggers?: string[]; source?: string; default_dir?: string; memory_store?: string
  /** Plainer, less machine-sounding PROSE (PT-7) — a named set of patterns to avoid.
   *  Distinct from `voice` (WHO the agent is) and from the voice-profile speech
   *  surface. Travels with the agent; a conversation can override it for itself. */
  natural_voice?: boolean
  // Agent routing (AGENT-ROUTING) — suggest-first specialist routing metadata.
  specialty?: string; route_hints?: string
  reserved?: boolean; editable?: boolean
}


// ── Goal Loop — the unified autonomous goal engine.
// Lifecycle status is `UnifiedLoopStatus` below — ONE union for one backend enum. A
// goal-shaped and a code-shaped copy used to live here and there; the goal one omitted
// `blocked`, which the backend both emits and accepts a `resume` from, so a comparison
// against it was a type error and every hand-written affordance guard dropped that state.
// Railed against `loop.loop:LoopStatus` by `tests/test_loop_status_vocabulary.py`.
export type GoalType = 'verifiable' | 'open_ended' | 'monitor'
export type Granularity = 'quick' | 'balanced' | 'exhaustive' | 'forever'
export interface LoopFinding {
  cycle: number; summary?: string; key_insight?: string
  sources_checked?: string[]; sources_empty?: string[]
  files_touched?: string[]
  new_findings_count?: number; evidence?: string; metric?: { name?: string; value?: number }; ts?: number
}
// A loop cycle's judge verdict. Since WF2LOO-16 this is the SAME record the workflows judge
// contract uses (`judge_contract.JudgeVerdict`) — the loop's private third vocabulary was
// deleted — so the shape gained the contract's fields. Every key below that existed before is
// still spelled the same, and older stored verdicts can carry null scores, so the scored fields
// stay optional-by-guard at the read sites rather than being assumed present.
export interface LoopVerdict {
  cycle?: number; done: boolean; done_reason?: string; marginal_value: number; quality_score: number; regressed: boolean
  // P4 observability (optional — present on high-stakes/scored verdicts): whether an
  // adversarial skeptic cross-checked this verdict, and the calibrated returns-band used.
  adversarial?: boolean; band_used?: number
  // ── From the contract (WF2LOO-16). Optional: a verdict stored before the merge has none. ──
  // The closed decision vocabulary `done` is projected onto: PASS when done, REJECT on a
  // regression, RETRY on an ordinary unfinished cycle.
  verdict?: 'PASS' | 'REJECT' | 'RETRY' | 'REPLAN' | 'ESCALATE' | 'NEEDS_INPUT'
  // `passed` is STRICTER than `done` — done AND contract-valid AND not escalated. The loops UI
  // shows `done`, because the loop judge's prompt is not given the contract's PASS preconditions.
  passed?: boolean; valid?: boolean; invalid_reason?: string; protocol_error?: boolean
  // Ground truth the SUPERVISOR observed itself (ran the command / read the deliverable), not a
  // claim the worker narrated. Empty for a transcript-only cycle.
  evidence_refs?: string[]; proof?: string
  reasoning?: string; scores?: Record<string, number>; overall?: number
  shortfalls?: string[]; escalated?: boolean; escalation_reason?: string
}
export interface LoopNudge { text: string; sent_at: number; sent_at_cycle: number; applied_cycle: number | null }
export interface RosterMember { role: string; persona: string; role_hint?: string; agent_name?: string }
export interface GoalLoop {
  id: string; name: string; goal: string; sub_goals: string[]; deliverables?: string[]; scope?: string[]
  goal_type: GoalType; intake_rigor: string
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  agent: string; model: string; provider?: string; provider_agent?: string; reasoning_effort?: string
  attended: boolean; granularity: Granularity
  max_cycles: number; max_cost_usd?: number; deadline_secs?: number; idle_secs: number
  stop_reason?: string
  success_criteria: string | null; verify_command?: string
  rubric?: string[]; best_score?: number; last_score?: number | null; ratchet_mode?: string
  marginal_scores?: number[]
  status: UnifiedLoopStatus; total_cycles: number; error_message: string | null
  created_at: number; started_at: number | null; completed_at: number | null; elapsed_seconds?: number
  findings?: LoopFinding[]; verdicts?: LoopVerdict[]; pending_question?: string | null; nudges?: LoopNudge[]
  feedback_producer?: FeedbackProducer
  linked_task_ids?: string[]
  // The containing Project this loop scopes under (Projects native entity, S3a).
  // project_id = explicit user scope; tasks_project_id = the auto-provisioned backing
  // project a project-less loop gets at launch (both carried by the unified Loop).
  project_id?: string
  tasks_project_id?: string
  // The hidden worker session the engine runs this loop's cycles under
  // (`loops/manager.session_key` → `loop-<id>`, or a run-scoped key once the loop runs as a
  // workflow template). Already carried at runtime — `loopToGoalLoop` spreads the whole
  // unified `Loop`, which declares it — but absent from THIS type, so the cockpit could not
  // read it. Declared here (T2.1) so the cockpit can fetch the worker's transcript meta for
  // the "used N skills" chip through the existing session endpoint instead of a new channel.
  session_key?: string
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
// Code's lifecycle status is `UnifiedLoopStatus` too. The shared watchdog can stagnate
// ANY kind (the legacy code engine could not), and every kind can block, so a per-kind
// status union only ever encoded which states its author remembered.
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
  max_cycles: number; max_cost_usd?: number; deadline_secs?: number; idle_secs: number
  stop_reason?: string
  success_criteria: string | null; verify_command?: string; test_command?: string
  status: UnifiedLoopStatus; total_cycles: number; error_message: string | null
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
/** What one loop cost, read from the per-turn ledger (MRT-3, `loop.manager.loop_spend`).
 *
 *  `dollars_est` spans the loop's worker session AND every task-worker session under it, so a
 *  fan-out loop is one figure. `planning` is the planner session (`loop-plan-<id>`), which is NOT
 *  under that prefix and is therefore reported separately rather than summed — the two are
 *  different money and a single total would overstate "this run".
 *
 *  `priced` is False when ANY constituent turn had no price row, which makes the figure a FLOOR.
 *  Present on the loop DETAIL only (`GET /api/loops/{id}`) — never on the list, and never on the
 *  SSE snapshot, so a consumer must not store it inside the loop entity it re-derives from a
 *  snapshot or it will vanish on the first lifecycle event. */
export interface LoopSpend {
  dollars_est: number
  turns: number
  tokens: number
  priced: boolean
  planning: { dollars_est: number; turns: number }
}
export interface Loop {
  id: string; kind: LoopKind; name: string; task: string; summary?: string
  /** Detail-only (see `LoopSpend`). Absent on the list and on the SSE snapshot. */
  spend?: LoopSpend
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
  max_cycles: number; max_cost_usd?: number; deadline_secs?: number; idle_secs: number
  stop_reason?: string
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
/** A CHAT's binding to that same walkthrough (CC-8). `awaiting_step_id` is the server's
 *  own derivation of "the review gate is open" — the client never recomputes it from
 *  step statuses, so the gate the UI shows and the gate the tool guard enforces agree.
 *  `binding` carries the chat-side attachment record (the task mode to restore, and
 *  whether a mid-turn activation parked a run). */
export interface ChatPlanWire {
  session: PlanSession | null
  binding: { resume_task_mode?: string; parked?: boolean; parked_messages?: number }
  awaiting_step_id: string
  task_mode: TaskMode
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
// AMBIENT-SURFACES §2.1 — the layout/data split. `skeleton` is a SEPARATE artifact holding
// the `{{...}}` body; the tile's own `ref` holds the rendered projection. `mode: 'ttl'` is the
// pre-substrate cadence; `view` (a bound trigger) arrives with AUTOMATION-SUBSTRATE step 8.
export interface TileDataNode { id: string; provider: string; config: Record<string, unknown> }
export interface TileRefresh { mode: 'manual' | 'ttl'; ttl_secs: number; skeleton: string; data: TileDataNode[] }
export interface DashboardTile { ref: string; size: TileSize; order: number; added_by: 'user' | 'agent'; refresh: TileRefresh }
export interface DashboardView { id: string; name: string; icon?: string | null; nav_pinned: boolean; preset: boolean; tiles: DashboardTile[] }
// One `tile_refreshed` ledger row (§2.3). `tokens`/`cost_usd` are stated, not inferred: a
// header that had to guess "free" from a missing field could not tell a zero-cost refresh
// from an unrecorded one.
export interface TileNodeOutcome { id: string; provider: string; ok: boolean; error: string; duration_ms: number }
export interface TileRefreshRow {
  kind?: string; event_id?: string; ts?: string; ok?: boolean
  tokens?: number; cost_usd?: number; duration_ms?: number
  nodes?: TileNodeOutcome[]; version?: number; rendered_bytes?: number; error?: string
}
export interface TileRefreshResult { refreshed: boolean; reason: string; ok: boolean; nodes: TileNodeOutcome[]; row: TileRefreshRow }
// The L2 user/agent surface overlays (AMBIENT-SURFACES §6 / AS-6). DATA, never code: a
// `body` holding the genui DSL plus optional `define`d composites, both of which are
// references to already-registered component names. Refusals ride along on the SAME 200
// as the accepted overlays — a refused overlay the user cannot see is an invisible failure.
export interface SurfaceOverlayDefine { name: string; description: string; body: string }
export interface SurfaceOverlayDoc { file: string; surface: string; title: string; body: string; define: SurfaceOverlayDefine[] }
export interface SurfaceOverlayError { code: string; what: string; why: string; fix: string; suggestions: string[] }
export interface SurfaceOverlayRefusal { file: string; error: SurfaceOverlayError }
export interface SurfaceOverlayPayload { overlays: SurfaceOverlayDoc[]; refusals: SurfaceOverlayRefusal[]; dir: string }

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

// ── the document model the editor edits (DOCUMENT-FIDELITY-EDITOR §C1/§C4) ──
// Mirrors `personalclaw/documents/model.py` field for field. Every field is REQUIRED
// here even though the server defaults them, because this same shape is posted BACK and
// `document_from_dict` is strict — an optional field a UI forgot to echo would be a
// silently dropped run/style, which is the exact fidelity failure the plan exists to
// prevent. `loss` is the report the parse produced: what the model could not hold.
export interface DocumentRun { text: string; bold: boolean; italic: boolean; code: boolean; link: string }
export interface DocumentParagraphStyle {
  align: string; space_before_pt: number; space_after_pt: number; line_spacing: number
  // DFE-6. `first_line_indent_pt` is the one field where a NEGATIVE value is meaningful
  // (a hanging indent), so a control must not clamp it at zero.
  indent_left_pt: number; indent_right_pt: number; first_line_indent_pt: number
  keep_with_next: boolean
}
export interface DocumentCell { runs: DocumentRun[]; text: string; bold: boolean; align: string }
/** DFE-6: margins are PER EDGE and in points. A single margin could not express the
 *  asymmetric geometry every real template ships, which made every generated document
 *  parse as lossy. `size` is a closed set — see `ui/content/documentPage.ts`. */
export interface DocumentPageSetup {
  size: string; orientation: string
  margin_top_pt: number; margin_bottom_pt: number
  margin_left_pt: number; margin_right_pt: number
  header_text: string; footer_text: string; page_numbers: boolean
}
export interface DocumentBlock {
  kind: 'heading' | 'paragraph' | 'bullets' | 'numbered' | 'table' | 'image' | 'pagebreak' | 'code'
  text: string; level: number; items: string[]; rows: string[][]
  artifact_slug: string; runs: DocumentRun[]; cells: DocumentCell[][]
  style: DocumentParagraphStyle | null
}
export interface DocumentModelJson { title: string; blocks: DocumentBlock[]; page: DocumentPageSetup | null }
/** One thing the parse could not represent — `where` locates it, `detail` names it. */
export interface DocumentLossItem {
  kind: string; detail: string; where: string
  block_index: number; paragraph_ordinal: number
}
/** `lossless` and `summary` are the SERVER's verdict, carried beside the items on
 *  purpose (`LossReport.to_dict`'s own note): a client that re-derived lossless as
 *  `items.length === 0` is right today and wrong the first time a purely informational
 *  item is added. So the editor reads these — it never recomputes them. */
export interface DocumentLossReport {
  lossless: boolean; kinds: string[]; summary: string; items: DocumentLossItem[]
}
export interface DocumentModelResponse {
  slug: string; kind: string; version: number; mime: string
  model: DocumentModelJson; loss: DocumentLossReport
}

// ── the spreadsheet model (DOCUMENT-FIDELITY-EDITOR T4.1) ────────────────────
// Mirrors `personalclaw/documents/model.py`'s sheet half field for field, and every
// field is REQUIRED here for `DocumentModelJson`'s reason: this shape is posted BACK and
// `sheet_from_dict` is strict, so a field the UI forgot to echo is a dropped format.
//
// `value` and `formula` are SEPARATE, and that is the fidelity: a cell holds either a
// literal or an expression, and the file format distinguishes them. A single text field
// would force a guess, and the guess is wrong in both directions — `"=SUM(A1)"` typed as
// a label becomes a formula, and a label like `"=TBD"` becomes `#NAME?` in Excel.
// `value` is the cached result when `formula` is set (usually null: the parse keeps
// formulas rather than cached values, and says so in the loss report).
export interface SheetCellJson {
  value: string | number | boolean | null
  formula: string
  number_format: string
  bold: boolean
  italic: boolean
  font_color: string
  fill: string
  align: string
}
/** One sheet. `column_widths` is dense and index-aligned (0 = the writer's default), and
 *  `merges` are A1-notation refs because that is what the format and a person both use.
 *  There is no `rows` here on purpose — the server derives the plain view from the cells,
 *  so the wire carries one representation of a cell and it cannot go stale. */
export interface SheetJson {
  name: string
  cells: SheetCellJson[][]
  column_widths: number[]
  merges: string[]
  frozen_header: boolean
}
export interface SheetModelJson { sheets: SheetJson[] }
export interface SheetModelResponse {
  slug: string; kind: string; version: number; mime: string
  model: SheetModelJson; loss: DocumentLossReport
}

// ── the deck model (DOCUMENT-FIDELITY-EDITOR T4.3) ───────────────────────────
// Mirrors `personalclaw/documents/model.py`'s deck half field for field, and every field
// is REQUIRED here for `DocumentModelJson`'s reason: this shape is posted BACK and
// `deck_from_dict` is strict, so a field the UI forgot to echo is a dropped slide layout.
//
// `level` is the fidelity this atom is about: a bullet's indent DEPTH is a field of the
// content, not a rendering flourish. The writer used to pin it to 0, so every deck came
// out flat no matter what outline went in.
export interface DeckBulletJson { text: string; level: number }
/** Where a slide's shape sits, in inches. All zeros = "wherever the layout puts it", which
 *  the server leaves inherited rather than pinning — so a zeroed box is not a position at
 *  the top-left corner, it is the absence of an override. */
export interface DeckShapeBoxJson { left_in: number; top_in: number; width_in: number; height_in: number }
/** One slide. `layout` is a layout NAME (`DECK_LAYOUTS`), never an index — an index means
 *  a different thing in every template. `""` means "laid out from the content". */
export interface DeckSlideJson {
  title: string
  bullets: DeckBulletJson[]
  notes: string
  artifact_slug: string
  layout: string
  title_box: DeckShapeBoxJson
  body_box: DeckShapeBoxJson
}
/** `title` is the deck's COVER slide — the server reads a leading title slide back into
 *  this field and re-renders it as one, so editing it does not append a second cover.
 *  `width_in`/`height_in` are the slide size (0 = the template's own). */
export interface DeckModelJson { title: string; slides: DeckSlideJson[]; width_in: number; height_in: number }
export interface DeckModelResponse {
  slug: string; kind: string; version: number; mime: string
  model: DeckModelJson; loss: DocumentLossReport
}

/** One deployed artifact (PEP-8). `url` is the stable in-gateway path the artifact is
 *  served at — always `/artifacts/serve/<slug>/`, never a public URL: local-only
 *  deploy, so it is reachable exactly to whoever holds a dashboard session. */
export interface ArtifactDeployment {
  slug: string
  entry: string
  created_at: string
  url: string
}

// One usage-ledger aggregate (COST-AND-TOKEN-OBSERVABILITY). `priced` is false when
// the group/window mixes a model with no price row → the cost is a partial, render
// "unpriced"/a partial marker, never a confidently-complete figure.
export interface UsageAgg {
  input_tokens: number; output_tokens: number
  cache_read_tokens: number; cache_creation_tokens: number
  cost_usd: number; turns: number; priced: boolean
}

/** One row of the per-day spend fold (MODEL-ROUTING-TELEMETRY MRT-3, `GET /api/usage`).
 *
 *  Same money as `UsageAgg` above — both read the per-turn ledger — but this is the DURABLE per-day
 *  fold of it, grouped into the fixed `interactive|background|loop|eval|app` purpose vocabulary. It
 *  outlives the ledger JSONL's own trim, which is why it exists beside the rollup rather than
 *  instead of it.
 *
 *  Two disclosures, deliberately separate:
 *  · `estimated_share` — fraction of `dollars_est` that is a rate-table estimate rather than a
 *    provider-reported charge. 1.0 today for everything, so always render a "~".
 *  · `priced` / `unpriced_calls` — a model with no price row contributes 0 dollars, so a row with
 *    `priced: false` is a FLOOR. Never render it as "$0.00 spent". */
export interface UsageFoldRow {
  key: string
  calls: number
  tokens_in: number; tokens_out: number; tokens: number
  dollars_est: number
  estimated_dollars: number
  estimated_share: number
  unpriced_calls: number
  local_calls: number
  priced: boolean
}

/** `GET /api/usage` — grouped rows + the window total + the per-day series behind the chart.
 *
 *  · `uncounted` — guarded `complete()` spend (`model_calls.jsonl`) that is deliberately NOT in any
 *    figure above. A loop's inner inference is recorded in both records and they share no id, so
 *    summing them would double-count with no way to detect it. Render it as a stated exclusion; a
 *    surface that omits it silently is claiming a completeness the data does not have.
 *  · `app_sources` — which app names produced `app` turns (a census, not an error).
 *  · `unmapped` — rows that could not be attributed to a day at all; counted, never dropped.
 *  · `reachable_purposes` — the subset of the vocabulary a writer can produce today, so a UI can
 *    skip a permanently-empty row (`eval` has no writer yet). */
export interface UsageFold {
  window: string
  group: string
  dates: string[]
  rows: UsageFoldRow[]
  total: UsageFoldRow
  series: Array<{ date: string; calls: number; dollars_est: number; tokens: number }>
  estimated_share: number
  unmapped: Record<string, number>
  app_sources: Record<string, number>
  uncounted: {
    calls: number
    total_calls: number
    total_dollars_est: number
    by_use_case: Record<string, number>
  }
  reachable_purposes: string[]
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

/** One use case's row in the routing policy table (MODEL-ROUTING-TELEMETRY §6.1, MRT-4).
 *
 *  `mode` is the per-use-case lever (off | heuristic | learned), `pin` short-circuits
 *  ordering entirely ('local' | 'cloud' | a ref | ''), `candidates` are the refs actually
 *  bound to this use case (the router only ever REORDERS these — it never invents one),
 *  and `classes` holds any recorded per-query-class order with the `basis` that decided
 *  it, so the table can always explain itself. */
export interface RoutingPolicyRow {
  use_case: string
  mode: 'off' | 'heuristic' | 'learned'
  pin: string
  candidates: Array<{ ref: string; local: boolean }>
  classes: Record<string, { order: string[]; basis: Record<string, unknown> }>
}

/** One pending routing PROPOSAL (MODEL-ROUTING-TELEMETRY §6.3, MRT-5).
 *
 *  Measurement never rewrites the routing table: when the fold shows one bound model
 *  clearly beating another for a request kind, the change lands here and waits for a
 *  person. `current`/`proposed` are permutations of the same refs (a proposal reorders,
 *  it never adds or drops a binding), and `evidence` is what makes it reviewable without
 *  re-running anything — per-ref scores and sample counts, the floors that applied, the
 *  latency/cost deltas (promoted minus demoted, so negative is better) and audit ids
 *  that correlate back to the actual calls. */
export interface RoutingProposal {
  id: string
  use_case: string
  query_class: string
  current: string[]
  proposed: string[]
  created_at: string
  status: string
  evidence: {
    n?: Record<string, number>
    scores?: Record<string, number>
    min_samples?: number
    hysteresis?: number
    cloud_quality_margin?: number
    p50_delta_ms?: number
    cost_delta_usd?: number
    sample_audit_ids?: string[]
  }
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
  // AP-7 §1: which paths the pack claims ongoing ownership of, and the per-component
  // `{source, computedHash}` drift lock an update compares against.
  pack_owned?: string[]
  component_locks?: Record<string, { source: string; computedHash: string; path: string }>
}

// One Domain OS pack shipped in this build (AGENT-PACKS §4.1) — the pack store's catalog row.
export interface BundledPackRec {
  name: string
  version: string
  displayName: string
  description: string
}

// One fingerprint rule's outcome, carrying the arithmetic behind its score (AP-7 §7). The
// card renders `declared_confidence` alongside `confidence` because the number only means
// something with its derivation next to it: confidence = declared × coverage, where coverage
// is how much of the rule (globs, signals) actually matched.
export interface FingerprintMatchRec {
  label: string
  confidence: number
  declared_confidence: number
  matched_globs: string[]
  matched_signals: string[]
  declared_globs: string[]
  declared_signals: string[]
  evidence: string[]
}

// A propose-only pack card (AP-7 §7). `inspect` is the §3.1 dry-run report — what the pack
// WOULD install, computed with no writes. It is null when the scan was asked for without one
// (project-create keeps its latency independent of pack count) or when the plan failed to
// build, in which case `inspect_error` says why.
export interface PackProposalRec {
  project_id: string
  pack: string
  displayName: string
  description: string
  version: string
  confidence: number
  matches: FingerprintMatchRec[]
  files_scanned: number
  inspect: { name: string; version: string; blocked: boolean; needs_consent: boolean; components: Array<{ kind: string; orig_id: string; target_id: string; verdict: string }>; requirements: unknown[]; staged_triggers: string[] } | null
  inspect_error: string
}

// One component's update decision (AP-7 §1). `action` is `overwrite` (the only one that
// writes), `skip_not_pack_owned`, `skip_drift` (you edited it — kept), or `skip_unverifiable`.
export interface PackUpdateRec {
  pack: string
  from_version: string
  to_version: string
  applied: boolean
  components: Array<{ ref: string; action: string; reason: string; pack_path: string; home_path: string }>
  drift_notes: string[]
  overwritten: string[]
  skipped: string[]
}

// /rewind-to-turn (EXECUTION-ISOLATION §6). `action` is a closed set — "not_captured" is
// the honest case: the file was deliberately never backed up (credential-shaped, or over
// the per-file cap), so the rewind will NOT restore it and the UI must say so rather than
// implying success.
export interface RewindFileWire {
  path: string
  action: 'restore' | 'delete' | 'unchanged' | 'not_captured'
  turn: number
  reason: string
  current_size: number
  restored_size: number
  current_sha256: string
  restored_sha256: string
  diff: string
}
export interface RewindPreviewWire {
  session: string
  turn: number
  turns_affected: number[]
  warnings: string[]
  files: RewindFileWire[]
  notice?: string
}
export interface RewindApplyWire {
  ok: boolean
  turn: number
  restored: string[]
  deleted: string[]
  skipped: string[]
  errors: string[]
  safety_turn: number
  notice: string
  preview: RewindPreviewWire
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
  // The per-day spend fold (MRT-3) — the ONLY usage read that includes unattended
  // (reasoning/loop/background) model calls; usageRollup/usageTotals above see the
  // per-turn ledger only. Read-only, derived on request; a deleted fold self-heals.
  usageFold: (opts?: { window?: 'day' | 'week' | 'month'; group?: 'model' | 'provider' | 'purpose' }) => {
    const p = new URLSearchParams()
    if (opts?.window) p.set('window', opts.window)
    if (opts?.group) p.set('group', opts.group)
    const q = p.toString()
    return get<UsageFold>(`/api/usage${q ? `?${q}` : ''}`)
  },
  // Per-model routing efficiency for one (use_case, query_class) bucket
  // (MODEL-ROUTING-TELEMETRY, MRT-1d). BOTH params are required (a missing either
  // is a 400); `rows` may be empty for a bucket with no telemetry yet. Read-only —
  // this only visualizes; nothing here changes routing. The Routing & Efficiency
  // settings panel (MRT-1e) renders it.
  modelsTelemetry: (opts: { use_case: string; query_class: string }) =>
    get<{ use_case: string; query_class: string; rows: TelemetryRow[] }>(
      `/api/models/telemetry?use_case=${encodeURIComponent(opts.use_case)}&query_class=${encodeURIComponent(opts.query_class)}`,
    ),
  // The routing POLICY table (MRT-4): one row per routed use case with its mode, pin,
  // bound candidates and recorded per-class orders. Read-only view; the three user
  // levers write through setRoutingPolicy. Both are fail-open server-side — an
  // unreadable table returns an empty list rather than an error, so the tab renders
  // "no opinion yet" instead of blanking.
  routingPolicy: () =>
    get<{ enabled: boolean; use_cases: RoutingPolicyRow[] }>('/api/models/routing-policy'),
  // Set ONE lever at a time (mode, pin, or a per-class order). Fields are applied only
  // when present, so a client never reverts a control it didn't render. `order` requires
  // `query_class` — an order is always per class.
  setRoutingPolicy: (body: {
    use_case: string
    mode?: 'off' | 'heuristic' | 'learned'
    pin?: string
    query_class?: string
    order?: string[]
  }) => put<{ ok: boolean; use_case: string; applied: string[] }>('/api/models/routing-policy', body),
  // The propose-don't-write review queue (MRT-5 §6.3). `count` is the Routing tab's badge.
  // Fail-open server-side: an unreadable queue reads as empty rather than erroring.
  routingProposals: () =>
    get<{ count: number; proposals: RoutingProposal[] }>('/api/models/routing-proposals'),
  // Accept APPLIES the proposed order to the table with the proposal as its basis. `applied:false`
  // is a legitimate 200: the cell's order was set by hand, and a user decision is never
  // overwritten — `reason` is the sentence to show. Reject writes no table at all; it records a
  // suppression so the same finding cannot re-nag for routing.reproposal_cooldown_days.
  acceptRoutingProposal: (id: string) =>
    post<{ ok: boolean; applied: boolean; id: string; reason?: string }>(
      `/api/models/routing-proposals/${encodeURIComponent(id)}/accept`,
      {},
    ),
  rejectRoutingProposal: (id: string) => del(`/api/models/routing-proposals/${encodeURIComponent(id)}`),
  // full backend config (read the `agent` subtree for Agent defaults) + the
  // single-field PATCH (allowlisted dotted paths — see _EDITABLE_CONFIG).
  personalclawConfig: () => get<Record<string, any>>('/api/config/personalclaw'),
  patchConfig: (path: string, value: unknown) => patch<Record<string, any>>('/api/config/personalclaw', { path, value }),

  // ── Companion apps (COMPANION-APPS S2) ──
  // The LIVE state of the LAN advertiser, which is not the same question as whether
  // companion.discovery_enabled is set: a loopback-only gateway advertises nothing by
  // design. `detail` is the sentence to show — the backend owns the wording so this
  // surface never invents a second one for a state it does not own.
  companionDiscovery: () => get<CompanionDiscovery>('/api/companion/discovery'),
  // ── The device registry (COMPANION-APPS C2 / CA-2) ──
  // Settings → Devices is the ONLY device list in the product; other surfaces link here
  // rather than growing a second one. `devicePairStart` mints a short-lived code; the device
  // itself redeems it against `pair/complete`, which is deliberately reachable WITHOUT a
  // session (a device with no session is the whole point), so this dashboard never calls it.
  devices: () => get<{ devices: DeviceRec[] }>('/api/devices').then((d) => d.devices),
  devicePairStart: (label?: string) =>
    post<DevicePairStart>('/api/devices/pair/start', label ? { label } : {}),
  // Drops the in-memory nonce AND the durable row, so a revoke cannot un-revoke on reboot.
  deviceRevoke: (id: string) =>
    post<{ ok: boolean; revoked: number }>(`/api/devices/${encodeURIComponent(id)}/revoke`, {}),

  // ── Packs (AGENT-PACKS §3.4/§9, AP-3) ──
  // The installed-pack ledger (each pack's components, connector resolutions +
  // `connector_missing:<name>` markers, and whether a re-runnable setup interview is
  // pending) and the "Finish setup" chip backend (returns the setup skill's slash-command;
  // the interview runs in chat under normal tool approval — never server-side).
  packsInstalled: () => get<{ packs: InstalledPackRec[] }>('/api/packs/installed').then((d) => d.packs),
  packFinishSetup: (name: string) => post<{ pack: string; setup_skill: string; command: string; pending: boolean }>(`/api/packs/${encodeURIComponent(name)}/finish-setup`, {}),
  // ── Pack store + fingerprint discovery (AGENT-PACKS §4.1/§7/§1, AP-7) ──
  // `packsBundled` is the store catalog; installing one runs the full §3 import (scan,
  // integrity, leaves-first commit with rollback) at BUILTIN trust.
  packsBundled: () => get<{ packs: BundledPackRec[] }>('/api/packs/bundled').then((d) => d.packs),
  packBundledInstall: (name: string) => post<{ ok: boolean; plan: Record<string, unknown> }>(`/api/packs/bundled/${encodeURIComponent(name)}/install`, {}),
  // The propose-only fingerprint cards. This GET performs the ON-DEMAND scan ("Suggest
  // packs") — one of only two callers of the scanner (the other is project-create); §7
  // forbids a background loop, so nothing polls this on a timer.
  packProposals: (projectId?: string) => get<{ proposals: PackProposalRec[] }>(`/api/packs/proposals${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`).then((d) => d.proposals),
  // Remember a "no" per (project, pack) — forever. The proposal never reappears for that project.
  packRejectProposal: (projectId: string, pack: string) => post<{ ok: boolean }>('/api/packs/proposals/reject', { project_id: projectId, pack }),
  // The §1 pack_owned update flow. DRY-RUN by default: the interesting output is the SKIP
  // list — which of your edited copies the update would leave alone — so the UI shows that
  // before `confirm` applies anything.
  packUpdate: (name: string, confirm = false) => post<{ ok: boolean; update: PackUpdateRec }>(`/api/packs/${encodeURIComponent(name)}/update`, { confirm }),

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

  // ── External Access: the shared inbound seam (EXTERNAL-ACCESS §1.5) ──
  //
  // There is deliberately NO method here that writes `public_url`, `allow_remote` or a
  // surface token: the backend exposes no route for them, and the surface switches go
  // through `patchConfig` like every other `_EDITABLE_CONFIG` field. If you find
  // yourself adding one, that is the security boundary asking to be moved — don't.
  externalAccess: () => get<ExternalAccess>('/api/external-access'),
  externalAccessCreateClient: (body: {
    label: string
    surfaces: string[]
    agent?: string
    tools?: string[]
    scope?: Record<string, unknown>
    rate_overrides?: Record<string, unknown>
  }) =>
    post<{
      ok: boolean
      client_id: string
      label: string
      surfaces: string[]
      /** Present in THIS response only — it is stored as a hash and never returned again. */
      token: string
      token_notice: string
    }>('/api/external-access/clients', body),
  // `del` resolves to void by design (it throws on !ok), so revocation is confirmed by
  // the absence of a throw plus the re-read — not by a body this helper cannot return.
  externalAccessRevokeClient: (clientId: string) =>
    del(`/api/external-access/clients/${encodeURIComponent(clientId)}`),
  externalAccessSetClientDisabled: (clientId: string, disabled: boolean) =>
    post<{ ok: boolean; client_id: string; disabled: boolean }>(
      `/api/external-access/clients/${encodeURIComponent(clientId)}/disabled`,
      { disabled },
    ),

  // ── Guardrails: incident kill switch + derived provider health (§1.3, §2.5) ──
  incident: () => get<{ active: boolean; reason: string; started_at: string }>('/api/incident'),
  incidentOn: (reason: string) =>
    post<{ active: boolean; reason: string; started_at: string }>('/api/incident', { reason }),
  incidentResume: () => post<{ active: boolean }>('/api/incident/resume', { confirm: true }),
  modelsHealth: () =>
    get<{ providers: ProviderHealth[]; callers?: CallerHealth[]; generated_from: number }>(
      '/api/models/health',
    ),

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
  durabilityRun: (job: 'export' | 'snapshot' | 'drill') =>
    post<DurabilityJobResult>('/api/durability/run', { job }),
  // ── §6 DSAR surface (DURABILITY-AND-SYNC §6, DAS-10) ──
  // These retired `/api/durability/snapshots` and the `/api/portability/*` trio: one
  // export endpoint, one import endpoint, one archive list, one restore.
  durabilityArchive: () => get<DurabilityArchives>('/api/durability/archive'),
  /** POST because the domain selection is a body. `domains` omitted = the full export. */
  durabilityExport: (domains?: string[]) =>
    fetch('/api/durability/export', {
      method: 'POST',
      headers: { ...SK, 'Content-Type': 'application/json' },
      body: JSON.stringify(domains && domains.length ? { domains } : {}),
    }).then(async (r) => {
      if (!r.ok) throw await apiError(r)
      return r.blob()
    }),
  /** `mode` omitted VALIDATES ONLY and applies nothing — the plan-first contract every
   *  home-overwriting verb in this API uses. `replace` additionally needs confirm. */
  durabilityImport: (file: File, mode?: 'merge' | 'replace') => {
    const fd = new FormData(); fd.append('file', file)
    const qs = mode ? `?mode=${mode}${mode === 'replace' ? '&confirm=true' : ''}` : ''
    return fetch(`/api/durability/import${qs}`, { method: 'POST', headers: { ...SK }, body: fd })
      .then(j<DurabilityImportResult>)
  },
  /** `mode` omitted returns the restore PLAN and changes nothing. */
  durabilityArchiveRestore: (id: string, body: { mode?: 'merge' | 'replace'; components?: string[]; confirm?: boolean } = {}) =>
    post<DurabilityRestoreResult>(`/api/durability/archive/${encodeURIComponent(id)}/restore`, body),
  // ── §4.2 the conflict review queue (DAS-10) ──
  /** `surface` omitted returns every surface's records; the counts always cover all of them
   *  so a filtered read can still say what waits elsewhere. */
  durabilityConflicts: (surface?: string, status?: string) => {
    const qs = new URLSearchParams()
    if (surface) qs.set('surface', surface)
    if (status) qs.set('status', status)
    const q = qs.toString()
    return get<DurabilityConflicts>(`/api/durability/conflicts${q ? `?${q}` : ''}`)
  },
  // ── Time travel (DURABILITY-AND-SYNC §5) ──
  durabilityHistory: () => get<DurabilityHistoryStatus>('/api/durability/history'),
  durabilityHistoryTimeline: (root: string, opts: { limit?: number; unattended?: boolean } = {}) => {
    const q = new URLSearchParams()
    if (opts.limit) q.set('limit', String(opts.limit))
    if (opts.unattended) q.set('unattended', '1')
    const qs = q.toString()
    return get<DurabilityHistoryTimeline>(
      `/api/durability/history/${encodeURIComponent(root)}/timeline${qs ? `?${qs}` : ''}`,
    )
  },
  /** Phase one. Sends no `confirm`, so the server returns the preview and touches nothing.
   *
   *  `paths` narrows the operation to a subset of the root (repo-relative). Omitted or empty
   *  sends NO `paths` key at all rather than `[]`, so the whole-root request stays byte-identical
   *  to what it was before the subset existed — the default path is not rerouted through a new
   *  parameter it does not need. */
  durabilityHistoryPreview: (
    root: string, op: 'rollback' | 'revert', sha: string, paths?: string[],
  ) =>
    post<DurabilityHistoryPreviewResponse>(
      `/api/durability/history/${encodeURIComponent(root)}/${op}`,
      { sha, ...(paths?.length ? { paths } : {}) },
    ),
  /** Phase two. `expected_head` MUST be the value phase one returned — the server refuses a
   *  preview that went stale rather than applying it to a tree the user never saw.
   *
   *  `paths` carries the SAME rule one step further: the server also refuses a confirm whose path
   *  set differs from the one it previewed. So callers must pass the set the PREVIEW returned, not
   *  whatever the UI currently has ticked — otherwise a user who ticks another box after previewing
   *  gets a refusal instead of the narrowed restore they asked for. */
  durabilityHistoryApply: (
    root: string, op: 'rollback' | 'revert', sha: string, expectedHead: string, paths?: string[],
  ) =>
    post<DurabilityHistoryResult>(
      `/api/durability/history/${encodeURIComponent(root)}/${op}`,
      { sha, confirm: true, expected_head: expectedHead, ...(paths?.length ? { paths } : {}) },
    ),
  /** Writes the chosen version into the live store. `confirm: true` is required for every
   *  choice — the server refuses without it, so this never sends it implicitly. */
  resolveDurabilityConflict: (id: string, choice: DurabilityConflictChoice) =>
    post<{ ok: boolean; choice: string; id: string; written: number; removed: number; conflict: DurabilityConflict }>(
      `/api/durability/conflicts/${encodeURIComponent(id)}/resolve`, { choice, confirm: true },
    ),
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
  // The §3.3 automation half, beside the surfacing simulator above. Read-only: nothing executes,
  // no credential is resolved, no model is called, and the trigger row is never written.
  doctorSimulateAutomation: (triggerId: string) =>
    post<AutomationWouldExecute>('/api/doctor/simulate/automation', { trigger_id: triggerId }),
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
  memoryEntityProposals: () =>
    get<{ proposals: MemoryEntityProposal[]; enabled: boolean }>('/api/memory/entities/proposals'),
  // §7.2 — the entity topology behind the graph canvas, and its one-file export. The export
  // comes back as TEXT and is blobbed by the caller (the AuditPanel pattern) rather than
  // linked: X-Session-Key rides the fetch, and a bare <a href> would not carry it.
  memoryEntityGraph: () => get<MemoryEntityGraph>('/api/memory/graph/entities'),
  /** One record's outbound entity links — the inspect tab's "why is this in my context?". */
  memoryRecordLinks: (ref: string) =>
    get<{ links: MemoryRecordLink[]; ref: string; enabled: boolean }>(
      `/api/memory/record-links?ref=${encodeURIComponent(ref)}`),
  memoryGraphExport: () =>
    fetch('/api/memory/graph/export', { headers: { ...SK } }).then(async (r) => {
      if (!r.ok) throw await apiError(r)
      return r.text()
    }),
  // §6/§7.1 — the Slots editor. `memorySlotAppend` RESOLVES on the 409 rather than throwing:
  // the trim proposal in that body IS the answer ("nothing was written, here is what you'd
  // have to drop"), and a rejection would discard exactly what the editor must show. Same
  // shape as `_installReq` above, for the same reason.
  memorySlots: () => get<MemorySlotsResponse>('/api/memory/slots'),
  memorySlotAppend: async (name: string, text: string): Promise<MemorySlotAppendResult> => {
    const r = await fetch(`/api/memory/slots/${encodeURIComponent(name)}/lines`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ text }),
    })
    const data = await r.json().catch(() => null)
    if (data && typeof data === 'object') return data as MemorySlotAppendResult
    return { ok: false, error: `HTTP ${r.status}` }
  },
  // POST-with-body, not DELETE-with-body: a DELETE body is dropped by some proxies, and a
  // tombstone is not a delete anyway (the line stays, marked, so it is never re-derived).
  memorySlotRetireLine: (name: string, text: string) =>
    post<{ ok: boolean }>(`/api/memory/slots/${encodeURIComponent(name)}/lines/retire`, { text }),
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

  // ── Channel sender trust (EA-7) — who is allowed to talk to the agent, per channel ──
  // The allowlist was writable from two places (a pairing code, the unknown-sender
  // notification's Allow) and readable from none. This is the read half; granting stays with
  // those two deliberate acts, so this surface revokes only.
  channelTrust: () => get<ChannelTrust>('/api/channels/trust'),
  revokeChannelSender: (provider: string, senderId: string) =>
    del(`/api/channels/trust/${encodeURIComponent(provider)}/senders/${encodeURIComponent(senderId)}`),

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

  // ── Always-on conventions viewer (PEP-10): what EVERY session receives, with
  // provenance. The server slices these out of the session's own producer strings, so
  // this list cannot drift from the assembled prompt. Bodies here are redacted previews;
  // alwaysOnDoc fetches one verbatim for the editor. ──
  alwaysOn: (projectId = '') =>
    get<AlwaysOnResponse>(`/api/legibility/always-on${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  alwaysOnDoc: (id: string, projectId = '') =>
    get<AlwaysOnItem>(`/api/legibility/always-on/doc?id=${encodeURIComponent(id)}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''}`),
  /** A refused or failed write REJECTS — the server never answers a discarded edit with ok:true. */
  saveAlwaysOnDoc: (id: string, projectId: string, body: string) =>
    put<{ ok: boolean; item: AlwaysOnItem }>('/api/legibility/always-on/doc', { id, project_id: projectId, body }),

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
  // BYO runner catalog rows. A plain read returns the last PERSISTED evidence (no
  // spawns); probe=true re-measures every runner's `--version` handshake first, which
  // is what the panel's "Re-check runners" action calls.
  agentRunners: (probe = false) => get<{ runners: RunnerRow[] }>(`/api/agent-runners${probe ? '?probe=1' : ''}`).then((d) => d.runners),
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
  // discovered models across all backends + this host's fit budget. The budget arrives ONCE at
  // the top level, but the surface that renders it (`LocalModelManager`) is handed only the model
  // rows by its parent card — so rather than restructure every caller's props, the top-level fact
  // is denormalized onto each provider AND each row as `host_fit`. Absent when the gateway sent no
  // `fit` at all, which every reader must treat as "unknown", never as "nothing fits".
  modelsAvailable: () => get<AvailableModelsResponse>('/api/models/available').then((d) => {
    const hostFit = d.fit
    if (!hostFit) return d.providers
    return d.providers.map((p) => ({
      ...p, host_fit: hostFit,
      models: p.models ? p.models.map((m) => ({ ...m, host_fit: hostFit })) : p.models,
    }))
  }),
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
  // What is occupying RAM right now (LMMV §7) — resident models with attribution, each
  // provider's readiness, and the system pressure snapshot. One fetch backs both the
  // Settings section and the dashboard's "On this machine" band.
  modelsLoaded: () => get<ResidencySnapshot>('/api/models/loaded'),
  // Idempotent: unloading a provider that holds nothing reports freed:false rather than
  // pretending. The reply carries a FRESH pressure snapshot, so the UI can show that the
  // unload actually freed memory instead of asserting it.
  unloadModelProvider: (provider: string) =>
    post<{ ok: boolean; provider: string; kind: string; freed: boolean; pressure: MemoryPressure }>(
      '/api/models/unload', { provider }),
  // The resumable sidecar install (LMMV §3.2) for a provider declaring execution: sidecar.
  sidecarInstallStatus: (provider: string) =>
    get<{
      provider: string; installed: boolean; managed: boolean; install_dir: string
      job: {
        state: string; progress: number
        steps: { name: string; status: string; detail: string }[]
        log_tail: string[]; error: string; reason: string; remediation: string
        weights_progress: number
      }
    }>(`/api/models/sidecar/${encodeURIComponent(provider)}/install/status`),
  startSidecarInstall: (provider: string) =>
    post<DownloadJob>(`/api/models/sidecar/${encodeURIComponent(provider)}/install`),
  deleteSidecarInstall: (provider: string) =>
    del(`/api/models/sidecar/${encodeURIComponent(provider)}/install`),
  // Search a searchable provider's remote installable catalog (ollama's library).
  searchLocalModels: (provider: string, q: string) =>
    get<{ models: LocalModel[] }>(`/api/models/local/${encodeURIComponent(provider)}/search?q=${encodeURIComponent(q)}`).then((d) => d.models ?? []),
  // dashboard config (server-persisted prefs incl. the operator name)
  dashboardConfig: () => get<DashboardConfig>('/api/dashboard/config'),
  saveDashboardConfig: (body: Partial<DashboardConfig>) => put<{ ok: boolean }>('/api/dashboard/config', body),

  // Screen context (MULTIMODAL-IO §5). `screenShareState` says whether the control
  // should be offered and — when the bound model can read a frame in no form —
  // carries the server-composed reason the control is disabled.
  screenShareState: (session: string) =>
    get<{ enabled: boolean; delivery: 'native' | 'described' | 'none'; reason: string; staged: boolean }>(`/api/chat/screen-frame?session=${encodeURIComponent(session)}`),
  screenShareSignal: (session: string, action: 'start' | 'stop') =>
    post<{ ok: boolean; sharing: boolean }>('/api/chat/screen-frame', { session, action }),
  /** Stage ONE frame for the next turn (latest-wins; held in memory, never written). */
  stageScreenFrame: (session: string, frame_b64: string) =>
    post<{ ok: boolean; staged: boolean }>('/api/chat/screen-frame', { session, action: 'frame', frame_b64 }),
  /** Pin a frame — the ONLY path that puts one on disk, as an ordinary attachment. */
  pinScreenFrame: (session: string, frame_b64: string) =>
    post<{ ok: boolean; path: string; name: string }>('/api/chat/screen-frame/pin', { session, frame_b64 }),

  // onboarding readiness + the in-flow fix (bind a chat model)
  onboarding: () => get<OnboardingState>('/api/onboarding'),
  /** Record first-run progress — a PARTIAL merge at both levels, so each step sends
   *  only what it learned. Never read-modify-write the whole document. */
  saveOnboardingState: (patch: OnboardingStatePatch) =>
    post<{ ok: boolean; state: OnboardingState }>('/api/onboarding/state', patch),
  /** What other local agent tools on this machine hold (PEP-5). Read-only in both
   *  directions — it writes neither their config nor our home. */
  onboardingImportScan: () => get<OnboardingImportScan>('/api/onboarding/import'),
  /** Import the picked categories. The server RE-SCANS: only the two selection axes
   *  travel, never items, so a caller can never name a directory to copy in. */
  runOnboardingImport: (body: { sources: string[]; categories: string[] }) =>
    post<OnboardingImportReport>('/api/onboarding/import', body),
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
  chatSessionDetail: (key: string) => get<{ key: string; title: string; messages: ChatHistoryMsg[]; running?: boolean; pending_approval?: boolean; agent?: string; model?: string; mode?: string; acp_provider?: string; acp_provider_agent?: string; reasoning_effort?: string; task_mode?: TaskMode; approval?: ApprovalMode; memory_mode?: string; queue?: { id: string; content: string }[]; side?: { open: boolean; messages: { role: string; content: string }[] } | null
    /** Branch lineage (CC-7): the parent's persisted HISTORY key (`dashboard:<key>`) when
     *  this session was branched, plus the parent's title resolved at read time. Served
     *  here — not carried in navigation state — so the breadcrumb survives a reload.
     *  `forked_from_title: ''` with a non-empty `forked_from` = the origin is gone. */
    forked_from?: string; forked_from_title?: string
    /** Natural voice (PT-7). `natural_voice` is what THIS conversation states
     *  (`'' | 'on' | 'off'`); the other three are resolved by the backend, which owns
     *  the order — the composer displays `natural_voice_source`, it never derives it. */
    natural_voice?: string; natural_voice_agent_default?: boolean
    natural_voice_effective?: boolean; natural_voice_source?: string }>(`/api/chat/sessions/${encodeURIComponent(key)}`),
  deleteChatSession: (key: string) => del(`/api/chat/sessions/${encodeURIComponent(key)}`),
  /** Set the per-conversation natural-voice scope (PT-7). `''` clears the override so
   *  the conversation inherits the bound agent's preference again. The response is the
   *  RE-RESOLVED state, not an echo — the backend owns the resolution order, so the
   *  composer shows what actually took effect rather than assuming its click won. */
  setSessionNaturalVoice: (session: string, choice: '' | 'on' | 'off') =>
    patch<{ ok: boolean; natural_voice: string; natural_voice_agent_default: boolean; natural_voice_effective: boolean; natural_voice_source: string }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/natural-voice`, { natural_voice: choice }),
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

  // Chat plan mode (CC-8) — a chat bound to the SAME stepwise planning walkthrough the
  // loop/code planners use (`PlanSession`/`PlanStep` above, not a second shape). Manual
  // only: `chatPlanActivate` is the sole way a chat acquires one, so a quick task never
  // grows a review gate. While a step awaits review the session sits in the `plan` task
  // mode and the backend's tool gate — not a prompt — is what refuses to execute.
  chatPlanSession: (session: string) =>
    get<ChatPlanWire>(`/api/chat/sessions/${encodeURIComponent(session)}/plan-session`),
  /** Open (or, mid-conversation, extend) the walkthrough. `parked: true` means a turn
   *  was in flight and has been asked to stop — the transcript is left intact. */
  chatPlanActivate: (session: string) =>
    post<{ ok: boolean; session: PlanSession; parked: boolean }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/activate`,
    ),
  chatPlanEdit: (session: string, stepId: string, markdown: string) =>
    post<{ ok: boolean; session: PlanSession }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/edit`, { step_id: stepId, markdown }),
  chatPlanComment: (session: string, stepId: string, text: string) =>
    post<{ ok: boolean; session: PlanSession }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/comment`, { step_id: stepId, text }),
  /** Approve a step. When it completes the walkthrough the reply carries the restored
   *  task mode, and `resumed` says whether a parked run was continued server-side. */
  chatPlanApprove: (session: string, stepId: string) =>
    post<{ ok: boolean; session: PlanSession; complete: boolean; resumed: boolean; task_mode: TaskMode }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/approve`, { step_id: stepId }),
  chatPlanCancel: (session: string) =>
    post<{ ok: boolean; task_mode: TaskMode }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/cancel`),

  // composer tools: prompt optimizer + speech-to-text transcription.
  optimizePrompt: (prompt: string, context = '') =>
    post<{ optimized?: string; changed?: boolean }>('/api/optimizer/optimize', { prompt, context }),
  /** Transcribe a recording. `duplex` marks a hands-free capture: the backend then
   *  checks the transcript against what it last spoke and answers
   *  `{ text: '', filtered: 'echo' }` when the microphone heard the assistant
   *  (MULTIMODAL-IO §4.2). `input_origin`/`disclaimer` ride back so the turn can be
   *  honest about having been dictated. */
  transcribeAudio: async (
    blob: Blob,
    opts?: { duplex?: boolean; session?: string },
  ): Promise<{ text?: string; error?: string; filtered?: string; input_origin?: string; disclaimer?: string }> => {
    const fd = new FormData()
    fd.append('audio', blob, 'recording.webm')
    const qs = new URLSearchParams()
    if (opts?.duplex) qs.set('duplex', 'true')
    if (opts?.session) qs.set('session', opts.session)
    const url = qs.toString() ? `/api/stt/transcribe?${qs}` : '/api/stt/transcribe'
    const r = await fetch(url, { method: 'POST', headers: { ...SK }, body: fd })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) return { error: data?.error || `HTTP ${r.status}` }
    return data
  },

  // send / control
  sendChat: (message: string, session: string, meta?: object, queue_mode?: string, input_origin?: string) =>
    post<{ ok: boolean; session?: string; queued?: boolean; steered?: boolean }>('/api/chat?ws=1', { message, session, meta, ...(queue_mode ? { queue_mode } : {}), ...(input_origin ? { input_origin } : {}) }),
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
  // /rewind-to-turn N (EXECUTION-ISOLATION §6) — the FILESYSTEM counterpart of /undo:
  // restores files the turns after N wrote, and never touches the transcript. GET is a
  // read-only preview (what would change, with diffs); POST needs confirm:true.
  rewindPreview: (session: string, turn: number) =>
    get<RewindPreviewWire>(`/api/chat/sessions/${session}/rewind?turn=${turn}`),
  rewindToTurn: (session: string, turn: number) =>
    post<RewindApplyWire>(`/api/chat/sessions/${session}/rewind`, { turn, confirm: true }),

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
  deleteULoop: (id: string) => fetch(`/api/loops/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw await apiError(r) }),
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
  // Content-free push (MOBILE-COMPANION MC-5). `pushStatus` carries the VAPID PUBLIC key
  // and nothing secret — it is the key the browser needs as `applicationServerKey`, and
  // `push_status()` is the only shape the route serves precisely so the private half
  // cannot be added to a response by accident.
  pushStatus: () => get<PushStatus>('/api/push'),
  pushSubscribe: (device_id: string, subscription: unknown) =>
    post<{ ok: boolean; device_id: string }>('/api/push/subscribe', { device_id, subscription }),
  pushUnsubscribe: (device_id: string) =>
    post<{ ok: boolean }>('/api/push/unsubscribe', { device_id }),
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
    get<{ triggers: Trigger[]; server_tz: string; owner?: string }>(
      `/api/triggers${type ? `?type=${type}` : ''}`,
    ),
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
  deleteSnippet: (name: string) => fetch(`/api/prompt-snippets/${encodeURIComponent(name)}`, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw await apiError(r) }),
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
  /** The identity report, DETERMINISTIC (LV-4). No model call — a panel mounting must not
   *  spend one, so the narrative is only composed by the POST below.
   *
   *  `days` is OMITTED by default so the server derives the window from the configured cadence.
   *  It used to default to 30 here, which made a weekly install's panel say "last 30 days" about
   *  a document its own cron writes over 7 — the FE quietly overriding a setting it also renders. */
  identityReport: (days?: number) =>
    get<IdentityReportView>(`/api/learning/identity-report${days === undefined ? '' : `?days=${days}`}`),
  /** Compose, narrate, persist the versioned artifact and raise ONE inbox item. Separate from
   *  the GET because it spends a model call and writes two durable things; the scheduled job
   *  calls the same backend function, so there is one owner, not two. `days` is omitted by
   *  default for the same reason as the GET — the hand-run's period is the cadence's period. */
  deliverIdentityReport: (days?: number) =>
    post<IdentityReportDelivery>(
      `/api/learning/identity-report${days === undefined ? '' : `?days=${days}`}`,
      {},
    ),
  /** The judge tier-recommendation table (ES-4). Read-only: the RUN is
   *  `personalclaw judge-bench`, because the full matrix is 540 judge calls and a click
   *  must not start one. 404 carries a distinct code for "no benchmark yet" vs "evals off". */
  judgeBench: () => get<JudgeBenchView>('/api/evals/judge-bench'),
  /** The newest keep/remove/lighten ablation report (ES-7 §3.1). Read-only for the bench's
   *  reason: a POST would hold a request open for a multi-cell matrix and spend real money on
   *  a click. The RUN is `personalclaw ablation` or the monthly cadence. 404 carries THREE
   *  distinct codes — `evals_disabled`, `ablation_absent`, and a 500 `ablation_unreadable` —
   *  because they send a user to three different places (the switch, the registry, a bug), and
   *  one state for all of them would make the panel's empty state a guess. */
  ablation: () => get<AblationView>('/api/evals/ablation'),
  /** The skill-impact benchmark: does an approved skill make the next run better? (LV-7)
   *
   *  Read-only, and for the sharpest reason on this route family: §3 pairs k=5 trials per arm
   *  over ten tasks — 100 real model calls — so a click that started one would spend serious
   *  money. The RUN is `python scripts/learning_benchmark.py --run`, which has `--preflight` and
   *  `--dry-run` modes that call nothing.
   *
   *  The verdict is computed by the runner (its thresholds live in `harness/fanout_measure.py`,
   *  outside the wheel) and written into the report. Neither the gateway nor this page can
   *  synthesise one — which is exactly why an unmeasured task arrives as `verdict: null` and
   *  renders as "not measured" instead of as a zero. 404 carries a distinct code for "no
   *  benchmark yet" vs "evals off". */
  learningBenchmark: () => get<BenchmarkView>('/api/evals/learning-benchmark'),
  /** Pre-registered template A/B studies (ES-5). Read-only for the same reason as the
   *  bench: a k=5 paired study is ten template runs plus six judge calls per pair. §2.1 is
   *  also explicit that the human REGISTERS and the substrate RUNS, so there is deliberately
   *  no POST here — a click that could do both would defeat the pre-registration. */
  /** Per-arm P@k/R@k for BOTH retrieval stores (ES-3 / §5). Read-only for a different
   *  reason than the bench's: retrieval costs no model calls, but §5.1 forbids the harness
   *  writing to knowledge.db or memory.db at all, and the cheapest way to keep that promise
   *  on a web surface is to have no run trigger on it. The RUN is `personalclaw
   *  retrieval-eval`. 404 carries a distinct code for "no run yet" vs "evals off". */
  retrievalBench: () => get<RetrievalBenchView>('/api/evals/retrieval'),
  /** §5.2's hand-label card for one store. `store` is REQUIRED — the two stores never share
   *  a corpus, so a card built for the wrong one would collect labels against ids the other
   *  has never heard of, and the backend refuses a missing one rather than defaulting. */
  retrievalLabelCard: (store: string) =>
    get<RetrievalLabelCard>(`/api/evals/retrieval/card?store=${encodeURIComponent(store)}`),
  /** Save a completed card. An EMPTY array for a query is a real judgement ("none of these
   *  answer it") and MUST be sent — omitting it lets the mined weak label the human just
   *  overruled quietly survive. */
  saveRetrievalLabels: (store: string, labels: Record<string, string[]>) =>
    post<{ ok: boolean; store: string; queries: number; hand_labelled: number }>(
      '/api/evals/retrieval/labels', { store, labels }),
  evalStudies: () => get<{ studies: StudyRow[] }>('/api/evals/studies'),
  evalStudy: (studyId: string) =>
    get<StudyView>(`/api/evals/studies/${encodeURIComponent(studyId)}`),
  /** The proposals queue AND the ladder's last pass, from one read. Returns the whole
   *  feed rather than unwrapping to the array: `lastReview` is what makes an empty
   *  `proposals` falsifiable, and a second accessor over the same route would be two
   *  reads of one collection — the drift this file's own callers already warn about. */
  skillProposals: () => get<SkillProposalFeed>('/api/skills/proposals'),
  skillProposalDetail: (id: string) => get<SkillProposalDetail>(`/api/skills/proposals/${encodeURIComponent(id)}`),
  /** `version` is the refinement version this accept WROTE (0 for a `kind: 'new'` accept,
   *  which creates a skill rather than versioning one). Returned because a refinement of a
   *  skill that already had refinements is otherwise indistinguishable from its first. */
  acceptSkillProposal: (id: string, edits?: { description?: string; procedure_md?: string }) =>
    post<{ ok: boolean; name: string; version: number }>(`/api/skills/proposals/${encodeURIComponent(id)}/accept`, edits ?? {}),
  rejectSkillProposal: (id: string) => del(`/api/skills/proposals/${encodeURIComponent(id)}`),
  /** The learning summary block (LV-3). 404s when `learning.enabled` is off — the
   *  caller must let the block be ABSENT in that case rather than render zeros, which
   *  would claim nothing was learned when the truthful answer is "not being tracked". */
  learningSummary: (days?: number) => get<LearningSummary>(`/api/learning/summary${days ? `?days=${days}` : ''}`),
  // Ephemeral session-skill drafts (skill-ephemeral-promotion).
  ephemeralSkills: (session: string) =>
    get<{ drafts: EphemeralDraft[] }>(`/api/skills/ephemeral/${encodeURIComponent(session)}`).then((d) => d.drafts),
  promoteEphemeralSkill: (session: string, payload: { slug: string; scope: 'agent' | 'global'; agent?: string; title?: string; body?: string }) =>
    post<{ ok: boolean; name: string; scope: string }>(`/api/skills/ephemeral/${encodeURIComponent(session)}/promote`, payload),
  discardEphemeralSkill: (session: string, slug: string) =>
    del(`/api/skills/ephemeral/${encodeURIComponent(session)}/${encodeURIComponent(slug)}`),
  skillMarketplaces: () => get<SkillMarketplace[]>('/api/skills/marketplaces'),
  // marketplace omitted → search across ALL marketplaces; pass one to scope.
  // `counts` is the per-source matched count BEFORE the global cap, so the source filter
  // can say how many of a large catalog matched even though only the top rows come back.
  // `installable_sources` counts catalogues you can install FROM — it excludes the two
  // `native` mirrors of what is already on this machine (bundled + your own skills). Zero of
  // those and zero MATCHES used to look identical, so a fresh install with no catalogue told
  // the user "No results — try a different search term".
  searchSkillsCounted: (q: string, marketplace?: string, limit = 30) =>
    get<{ results: SkillSearchResult[]; counts?: Record<string, number>; installable_sources?: number }>(`/api/skills/search?q=${encodeURIComponent(q)}&limit=${limit}${marketplace ? `&marketplace=${encodeURIComponent(marketplace)}` : ''}`).then((d) => ({ results: d.results, counts: d.counts ?? {}, installableSources: d.installable_sources ?? 0 })),
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
  /** The whole positioned, edge-thinned entity graph (KL-17). One method for both the full
   *  canvas and the reader's ego view — `KnowledgeGraph` used to raw-`fetch` this path with a
   *  `limit` the handler no longer honours, which is how two callers drift. */
  knowledgeGraph: () => get<{
    nodes: { id: string; name?: string; type?: string; x?: number; y?: number; placed?: boolean; degree?: number; cluster?: number | null }[]
    edges: { source: string; target: string; type?: string; weight?: number }[]
  }>('/api/knowledge/graph'),
  knowledgeItemRelated: (id: string) => get<KnowledgeItem[]>(`/api/knowledge/items/${encodeURIComponent(id)}/related`),
  /** Staleness for a synthesized item. 404 for an unknown id; a non-synthesized item
   *  answers `stale: false` rather than erroring, so the caller needs no kind check. */
  researchReports: () => get<{ reports: ResearchReport[] }>('/api/knowledge/reports'),
  createResearchReport: (body: Partial<ResearchReportInput>) =>
    post<ResearchReport>('/api/knowledge/reports', body),
  updateResearchReport: (id: string, body: Partial<ResearchReportInput>) =>
    put<ResearchReport>(`/api/knowledge/reports/${encodeURIComponent(id)}`, body),
  deleteResearchReport: (id: string) =>
    del(`/api/knowledge/reports/${encodeURIComponent(id)}`),
  /** Run one now. A 409 means a scheduled fire already holds the lease — the manual run is
   *  idempotent against it rather than starting a second one. */
  runResearchReport: (id: string) =>
    post<{ ok: boolean; report_id: string; outcome?: string; note?: string }>(
      `/api/knowledge/reports/${encodeURIComponent(id)}/run`,
    ),
  knowledgeStaleness: (id: string) =>
    get<KnowledgeStaleness>(`/api/knowledge/items/${encodeURIComponent(id)}/staleness`),
  /** The ONE action the staleness banner offers. It queues a proposal the owner accepts —
   *  generated prose never overwrites human writing on its own (WF2KNO-11).
   *
   *  `already_pending` is `null` when the server could not tell (the update pipeline did
   *  not report it), which the UI must treat as "unknown", not as "no". */
  knowledgeRegenerate: (id: string) =>
    post<{
      ok: boolean
      item_id: string
      already_pending: boolean | null
      proposal: { proposal_id?: string; applied?: boolean; pending?: boolean; reason?: string } | null
    }>(`/api/knowledge/items/${encodeURIComponent(id)}/regenerate`),
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
  // ── Watched sources (WATCHED-SOURCES §2.4/§6.3/§12) ──
  // One GET for the rows AND the create flow's kind catalog: the list page and the create
  // page are one surface, and a second round trip to learn which kinds exist would just
  // make the create form flash.
  knowledgeSources: () => get<SourcesResponse>('/api/knowledge/sources'),
  createKnowledgeSource: (body: {
    name: string; provider: string; spec: Record<string, unknown>
    enrichment?: string; poll_interval_secs?: number; budget?: Record<string, unknown>
  }) => post<{ source: WatchedSource }>('/api/knowledge/sources', body),
  // The remediation + lifecycle path: `budget.allow_render` for a JS shell, `spec.url` for a
  // wrong URL, `enabled` to stop a source polling. Partial — an absent key is untouched.
  updateKnowledgeSource: (id: string, body: {
    name?: string; enabled?: boolean; enrichment?: string; poll_interval_secs?: number
    spec?: Record<string, unknown>; budget?: Record<string, unknown>
  }) => patch<{ source: WatchedSource }>(`/api/knowledge/sources/${encodeURIComponent(id)}`, body),
  // §2.4's dry run. Persists nothing but DOES spend the request budget — it is a real fetch
  // at somebody else's server. Only the web kind has one (`SourceKind.previewable`).
  previewKnowledgeSource: (body: { provider: string; spec: Record<string, unknown>; budget?: Record<string, unknown> }) =>
    post<SourcePreviewResult>('/api/knowledge/sources/preview', body),
  // §7.2's recipe directory. With a `url` it answers the create flow's FIRST question — is this
  // site already worked out? — and each match carries a spec already resolved from the URL, so
  // the form is filled from what the user was shown rather than re-derived here.
  knowledgeSourceRecipes: (url?: string) =>
    get<SourceRecipesResponse>(
      url ? `/api/knowledge/source-recipes?url=${encodeURIComponent(url)}` : '/api/knowledge/source-recipes',
    ),
  // Distinct tags (frequency-ordered) for tag-input autocomplete.
  knowledgeTags: () => get<{ tags: string[] }>('/api/knowledge/tags').then((d) => d.tags),
  // ── Knowledge collections (KNOWLEDGE-LIBRARY S1) ──
  knowledgeCollections: () =>
    get<{ collections: KnowledgeCollection[] }>('/api/knowledge/collections').then((d) => d.collections),
  // The library home's four shelves in ONE read (KNOWLEDGE-LIBRARY S3, T3.3). 🔴 NO
  // `.catch(() => …)`: four empty shelves and a failed fetch look identical, and the home is
  // the one surface where "your library is empty" and "the read failed" must not be the same
  // pixels. The rejection reaches the caller so it can say which.
  knowledgeLibraryHome: (limit?: number) =>
    get<KnowledgeLibraryHome>(`/api/knowledge/library-home${limit ? `?limit=${limit}` : ''}`),
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
  // Reading highlights. Like read-state and favorites these are NON-TOUCHING writes with
  // their own endpoints, not `updateKnowledgeItem` fields: marking a passage is reading,
  // not editing, so it must not bump `updated_at` and reshuffle a recency-sorted library.
  knowledgeAnnotations: (id: string) =>
    get<{ annotations: KnowledgeAnnotation[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/annotations`).then((d) => d.annotations),
  createKnowledgeAnnotation: (id: string, body: { quote: string; occurrence: number; note?: string }) =>
    post<{ ok: boolean; annotation: KnowledgeAnnotation }>(`/api/knowledge/items/${encodeURIComponent(id)}/annotations`, body),
  // Keyed by the highlight's OWN id, not nested under the item — repeating the item id
  // would let a caller delete row A while naming item B.
  deleteKnowledgeAnnotation: (annotationId: string) =>
    del(`/api/knowledge/annotations/${encodeURIComponent(annotationId)}`),
  // ── Dedup / merge (KNOWLEDGE-LIBRARY S3, T3.2) ──
  // 🔴 NO `.catch(() => [])` HERE, and this one is sharper than the usual case: an empty
  // duplicates list is the NORMAL answer for almost every item, so a swallowed rejection
  // renders as "no duplicates" — indistinguishable from the truth, permanently, on the one
  // surface whose whole job is to tell you two copies exist. The rejection has to reach the
  // caller so the panel can say the lookup failed instead of silently claiming it is clean.
  knowledgeDuplicates: (id: string) =>
    get<{ duplicates: KnowledgeDuplicate[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/duplicates`).then((d) => d.duplicates),
  // The SURVIVOR is the path id and the loser is in the body — the route's own shape, kept
  // in the same order here so a caller cannot silently swap them. `confirm: true` is sent by
  // this helper because the route requires it; the USER's confirmation is a separate, earlier
  // gate (a named dialog at the call site), not this flag.
  mergeKnowledgeItems: (keepId: string, mergeId: string) =>
    post<KnowledgeMergeResult>(`/api/knowledge/items/${encodeURIComponent(keepId)}/merge`, { merge_id: mergeId, confirm: true }),
  // ── Structural editing verbs (KL-19) ──
  // The boundaries a split may cut on. NO `.catch(() => [])`: an empty list is the normal
  // answer for a document with no headings, so a swallowed rejection renders as "this item
  // cannot be split" — indistinguishable from the truth, on the one call that decides whether
  // the verb is offered at all.
  knowledgeItemSections: (id: string) =>
    get<{ sections: KnowledgeSection[]; length: number }>(`/api/knowledge/items/${encodeURIComponent(id)}/sections`),
  /** Phase one. Sends no `confirm`, so the server returns the preview and touches nothing.
   *
   *  The returned `token` is a digest of the verb, its parameters, the affected items' current
   *  state AND the break list — so it is not a nonce the client may hold indefinitely. Anything
   *  that moves invalidates it, which is what makes the preview mandatory by construction rather
   *  than by this client remembering to ask first. */
  knowledgeRestructurePreview: (id: string, verb: string, params: Record<string, unknown>) =>
    post<KnowledgeRestructurePreview>(
      `/api/knowledge/items/${encodeURIComponent(id)}/restructure/${encodeURIComponent(verb)}`,
      params,
    ),
  /** Phase two. `token` MUST be the one phase one returned, and `params` MUST be the params it
   *  was previewed with — the server refuses a token whose plan no longer matches (409
   *  `preview_stale`, with the fresh plan attached) rather than applying a preview the user
   *  never saw. So callers pass what they PREVIEWED, not whatever the form now holds.
   *
   *  `relink: false` declines the repair the preview offered; the break then simply happens,
   *  which is a choice the reader is entitled to make. Re-sending the same token is safe —
   *  the server replays the first result instead of restructuring twice. */
  knowledgeRestructureApply: (
    id: string, verb: string, params: Record<string, unknown>, token: string, relink = true,
  ) =>
    post<KnowledgeRestructureResult>(
      `/api/knowledge/items/${encodeURIComponent(id)}/restructure/${encodeURIComponent(verb)}`,
      { ...params, confirm: true, token, relink },
    ),
  // The undo journal. Listed rather than only handed back by `apply`, because a reader who
  // navigates away or reloads has nowhere else to get the token from — and an undo the user
  // cannot find is not one they can rely on before a destructive restructure.
  knowledgeRestructureUndoable: () =>
    get<{ undoable: KnowledgeUndoEntry[] }>('/api/knowledge/restructure/undo').then((d) => d.undoable),
  knowledgeRestructureUndo: (token: string) =>
    post<{ ok: boolean; verb: string; item_id: string; summary: string }>('/api/knowledge/restructure/undo', { token }),
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
  // INU-9: the user writes their OWN inbox item. The tray's quick capture and the inbox
  // compose control are two entry points onto this one endpoint; 201 carries the created
  // row so a caller can render it without a refetch.
  createInboxNote: (text: string) =>
    post<{ ok: boolean; id: string; item: InboxItem }>('/api/inbox/notes', { text }),
  inboxStatus: () => get<InboxStatus>('/api/inbox/status'),
  inboxProviders: () => get<{ providers: InboxProvider[] }>('/api/inbox/providers').then((d) => d.providers),
  updateInboxItem: (id: string, body: Record<string, unknown>) => put<InboxItem>(`/api/inbox/${encodeURIComponent(id)}`, body),
  // INU-6: undo a verification filter — flips FILTERED→PENDING and fires the ONE
  // notification the second-opinion pass withheld (server enforces fire-exactly-once).
  restoreInboxItem: (id: string) => post<InboxItem>(`/api/inbox/${encodeURIComponent(id)}/restore`),
  // INU-7: approve one proposal through the C6 apply dispatcher. `edited` is the
  // edit-then-approve payload and REPLACES the stored one (server refuses it for a
  // non-editable proposal). Resolves with ok:false on a failed apply — the item is still
  // PENDING and carries the error, so the caller renders the failure instead of throwing.
  applyInboxProposal: (id: string, edited?: InboxProposal) =>
    post<InboxProposalApplyResult>(
      `/api/inbox/${encodeURIComponent(id)}/apply`,
      edited ? { proposal: edited } : {},
    ),
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
  // Cursor-paginated, NOT offset-paginated: the log is append-only and read newest-first,
  // so an offset would re-serve rows after a concurrent append and skip rows after a prune.
  // Pass the previous page's `next_cursor` to continue.
  auditEvents: (opts: { limit?: number; cursor?: string; filters?: AuditFilters } = {}) => {
    const q = new URLSearchParams({ limit: String(opts.limit ?? 50) })
    if (opts.cursor) q.set('cursor', opts.cursor)
    for (const [k, v] of Object.entries(opts.filters ?? {})) if (v) q.set(k, v)
    return get<AuditPage>(`/api/security/audit?${q}`)
  },
  auditVerify: (full = false) => get<SelVerify>(`/api/security/audit/verify${full ? '?full=1' : ''}`),
  // Mirrors `SecurityEventLog.rotate()` (src/personalclaw/sel.py): the log is archived and a fresh
  // chain started. `archive_path` is the timestamped `.bak.jsonl` the old entries moved to (empty when
  // there was nothing to archive). The old `{ ok?: boolean }` shape silently dropped all of this.
  selRotate: () => post<{ rotated: boolean; entries_before: number; entries_after: number; archive_path: string }>('/api/sel/rotate'),
  // session archive (read-only browse)
  sessionArchives: () => get<{ archives: SessionArchive[] }>('/api/session/archive').then((d) => d.archives),
  // The read endpoint serves raw NDJSON text (application/x-ndjson), NOT a JSON
  // document — parse as text or every multi-line archive throws in r.json().
  sessionArchiveRead: (name: string) =>
    fetch(`/api/session/archive/${encodeURIComponent(name)}`, { headers: { ...SK } })
      .then(async (r) => { if (!r.ok) throw await apiError(r); return r.text() }),
  // Whole-home export/import live on the durability surface — see `durabilityExport`.
  // One PROJECT as a manifest ZIP — narrower than the whole-home archive above, so a user can hand
  // a colleague a single project without shipping their memory database. Credentials never travel;
  // the response headers name the ones the far side must re-enter.
  projectExportUrl: (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/export`,
  projectImport: (file: File, opts: { preview?: boolean } = {}) => {
    const fd = new FormData(); fd.append('file', file)
    const qs = opts.preview ? '?preview=1' : ''
    return fetch(`/api/projects/import${qs}`, { method: 'POST', headers: { ...SK }, body: fd }).then(j<ProjectImportResult>)
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
  // ── The triage digest (PROACTIVE-ASSISTANT §5.1/§5.2/§5.4 — PA-5) ──
  // The card makes ONE read. The server assembles the sections, so the browser never has to
  // chain run-list → node-output → ledger and guess what a partial chain means.
  proactiveDigest: () => get<TriageDigestView>('/api/proactive/digest'),
  // The tap. `text` is the SAME reply grammar a channel message uses ('3 yes', 'always no 4'),
  // so the digest has one parser rather than a button vocabulary that can drift from it.
  proactiveReply: (runId: string, text: string) =>
    post<TriageReplyResult>('/api/proactive/digest/reply', { run_id: runId, text }),
  // §5.4's pack card: install the Morning-triage schedule, or reconcile an installed one against
  // `proactive.triage_enabled`. Idempotent, so the same call is both the install and the repair.
  proactiveInstall: (cron?: string) =>
    post<{ ok: boolean; created: boolean; schedule: TriageSchedule }>(
      '/api/proactive/install', cron ? { cron } : {}),
  // ── The Decision Journal (PROACTIVE-ASSISTANT §2.5/§5.3 — PA-6) ──
  // ONE read for the rows AND the strip. The strip is an aggregate of the rows beside it, so
  // two fetches could render 11 resolved decisions next to a rate computed from 10 — two
  // answers to one question. `status`/`domain` narrow the LIST only; `calibration` is always
  // the whole journal, because narrowing it would silently redefine the claim it makes.
  decisionJournal: (status?: string, domain?: string) => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    if (domain) p.set('domain', domain)
    const qs = p.toString()
    return get<DecisionJournalView>(`/api/knowledge/decisions${qs ? `?${qs}` : ''}`)
  },
  // §5.2's rules manager. These three routes shipped with PA-1 and had NO consumer until now.
  approvalRules: () =>
    get<{ rules: ApprovalRuleRow[]; unreadable: string[] }>('/api/memory/approval-rules'),
  saveApprovalRule: (body: { pattern: string; verdict: 'approve' | 'deny'; scope?: string; expires_at?: string | null; send_capable?: boolean }) =>
    post<{ ok: boolean; rule: ApprovalRuleRow }>('/api/memory/approval-rules', body),
  revokeApprovalRule: (key: string) => del(`/api/memory/approval-rules/${encodeURIComponent(key)}`),
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
  // SH-2 — the credential store. Both writes send `confirm: true`: the flag is the
  // protocol-level record that the user was shown the snapshot step, and the backend
  // refuses without it independently, so this client cannot skip the consent.
  credentialStore: () => get<CredentialStoreState>('/api/security/credentials'),
  migrateCredentialsToKeychain: () =>
    post<CredentialMoveResult>('/api/security/credentials/migrate', { confirm: true }),
  rollbackCredentialsToKeychain: () =>
    post<CredentialMoveResult>('/api/security/credentials/rollback', { confirm: true }),
  setCredentialKeychain: (on: boolean) =>
    patch<Record<string, any>>('/api/config/personalclaw', { path: 'security.credential_keychain', value: on }),
  // EI-10 — the secrets vault. The READ carries presence, scope and consumer links and NEVER a
  // value: `/api/secrets` has no code path to one (the server builds its rows from key names
  // only). So there is deliberately no `getSecret(name)` here — not "we chose not to add it",
  // but there is no endpoint it could call.
  secrets: (projectId = '') =>
    get<SecretsVaultState>(`/api/secrets${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  // The value travels ONE WAY, in this body, and is never returned. The result carries the new
  // row's PRESENCE plus the refreshed list, so the panel updates without a second round trip and
  // without ever holding a value it could accidentally render.
  putSecret: (name: string, value: string, projectId = '') =>
    post<SecretWriteResult>('/api/secrets', { name, value, project_id: projectId }),
  // Query params, not a body: DELETE bodies are inconsistently forwarded by proxies, and the
  // name is not secret — only values are.
  deleteSecret: (name: string, projectId = '') =>
    fetch(
      `/api/secrets?name=${encodeURIComponent(name)}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''}`,
      { method: 'DELETE', headers: { ...SK } },
    ).then(j<SecretDeleteResult>),
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
    if (!r.ok) throw await apiError(r)  // ApiError carries .status so the viewer can tell a 404 (file gone → close the stale tab) from a transient 5xx (offer retry)
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
  // EXTERNAL-ACCESS §5 — publish/unpublish one template as an A2A skill. Its own route, not a
  // field on `saveWorkflowDef`: this page holds the secret-stripped def, and re-saving that to
  // carry one bool would persist the stripped bindings.
  publishWorkflowToA2A: (name: string, published: boolean) =>
    post<{ ok: boolean; name: string; a2a_published: boolean }>(`/api/workflows/${encodeURIComponent(name)}/a2a-publish`, { published }),
  deleteWorkflowDef: (name: string) => del(`/api/workflows/${encodeURIComponent(name)}`),

  // ── template versions + refiner (WF2LEA-6) ──
  /** The monotonic version history, the pinned (active) version, and the maturity badge. */
  workflowVersions: (name: string) =>
    get<{ versions: WorkflowVersionRow[]; pinned: number; maturity: WorkflowMaturity }>(
      `/api/workflows/${encodeURIComponent(name)}/versions`,
    ),
  /** The typed-op diff between two versions (add/remove/reorder/update-node ops). */
  workflowVersionDiff: (name: string, a: number, b: number) =>
    get<{ a: number; b: number; ops: WorkflowVersionOp[] }>(
      `/api/workflows/${encodeURIComponent(name)}/versions/diff?a=${a}&b=${b}`,
    ),
  /** Rollback / re-pin the active version. Moves only the pointer; history is never rewritten. */
  repinWorkflowVersion: (name: string, version: number) =>
    post<{ ok: boolean; name: string; pinned: number }>(
      `/api/workflows/${encodeURIComponent(name)}/versions/repin`,
      { version },
    ),
  /** Recent runs of this template with their ledger totals — the Run Ledger tab. */
  workflowLedger: (name: string) =>
    get<{ name: string; runs: WorkflowLedgerRow[]; total: number }>(
      `/api/workflows/${encodeURIComponent(name)}/ledger`,
    ),
  /** Fire the propose-only refiner over this template on demand ("Refine now"). */
  refineWorkflow: (name: string) =>
    post<{ run_id?: string; status?: string }>(`/api/workflows/${encodeURIComponent(name)}/refine`, {}),

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
  // Review findings, anchored against the run's diff AT READ TIME (EI-9). The anchor verdict is
  // never cached client-side for the same reason the server never stores it: the worker keeps
  // working, and a stale `anchored` is how an accepted fix lands on the wrong line.
  workflowReview: (id: string) =>
    get<WorkflowReviewPayload>(`/api/workflows/runs/${encodeURIComponent(id)}/review`),
  workflowReviewTriage: (
    id: string,
    body: { decisions: Array<{ key: string; outcome: 'accept' | 'reject'; reason?: string }>; dry_run?: boolean },
  ) =>
    post<WorkflowTriageResult>(`/api/workflows/runs/${encodeURIComponent(id)}/review/triage`, body),
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

  // ── the document editor's read/save pair (DOCUMENT-FIDELITY-EDITOR §C4) ──
  // Structure in both directions: the browser is handed a parsed MODEL and hands one
  // back, and the server re-renders it with the shipped writer. No OOXML crosses here.
  // `If-Match` is REQUIRED on the save (not optional): two tabs editing one document
  // must collide with a 409 rather than have the second silently overwrite the first,
  // so this is spelled out with `fetch` — the shared `put` helper carries no headers.
  artifactModel: (slug: string) => get<DocumentModelResponse>(`/api/artifacts/${encodeURIComponent(slug)}/model`),
  saveArtifactModel: (slug: string, version: number, model: DocumentModelJson) =>
    fetch(`/api/artifacts/${encodeURIComponent(slug)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(version), ...SK },
      body: JSON.stringify({ model }),
    }).then(j<{ slug: string; version: number; mime: string }>),
  // The SAME two routes for a spreadsheet — one endpoint pair, the kind decides which
  // model shape crosses it (`documents/model_codec.py`). Separate accessors rather than a
  // union return, so the caller that knows it opened an .xlsx is not made to narrow a type
  // it already knows: a `DocumentModelJson | SheetModelJson` would push a `'blocks' in m`
  // check into every editor, which is a discriminator the URL already carries.
  artifactSheetModel: (slug: string) => get<SheetModelResponse>(`/api/artifacts/${encodeURIComponent(slug)}/model`),
  saveArtifactSheetModel: (slug: string, version: number, model: SheetModelJson) =>
    fetch(`/api/artifacts/${encodeURIComponent(slug)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(version), ...SK },
      body: JSON.stringify({ model }),
    }).then(j<{ slug: string; version: number; mime: string }>),
  // …and again for a deck, for the same reason: one endpoint pair, three model shapes, the
  // kind decides which crosses (`documents/model_codec.py`).
  artifactDeckModel: (slug: string) => get<DeckModelResponse>(`/api/artifacts/${encodeURIComponent(slug)}/model`),
  saveArtifactDeckModel: (slug: string, version: number, model: DeckModelJson) =>
    fetch(`/api/artifacts/${encodeURIComponent(slug)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(version), ...SK },
      body: JSON.stringify({ model }),
    }).then(j<{ slug: string; version: number; mime: string }>),

  // ── local static artifact deploy (PEP-8) ──
  // Deploying publishes an html/widget artifact at a stable IN-GATEWAY url
  // (`/artifacts/serve/<slug>/`) behind the same session auth as the dashboard and a
  // strict CSP fence (`connect-src 'none'` — the served page cannot call /api).
  // Teardown removes the route; the artifact itself is untouched.
  deployedArtifacts: () => get<{ deployments: ArtifactDeployment[] }>('/api/artifacts/deployed').then((d) => d.deployments),
  deployArtifact: (slug: string, body?: { entry?: string }) =>
    post<{ ok: boolean; deployment: ArtifactDeployment }>(`/api/artifacts/${encodeURIComponent(slug)}/deploy`, body ?? {}),
  teardownArtifact: (slug: string) =>
    del(`/api/artifacts/${encodeURIComponent(slug)}/deploy`),

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

  // ── the L2 user/agent surface overlays (AMBIENT-SURFACES §6 / AS-6) ──
  // READ only, on purpose: an overlay is authored with the ordinary file tools under
  // $PERSONALCLAW_HOME/surfaces/, so an HTTP writer would be a second producer with a
  // second set of refusals.
  surfaceOverlays: () => get<SurfaceOverlayPayload>('/api/surfaces/overlays'),

  // ── chatless refresh (AMBIENT-SURFACES §2) ──
  // `refreshTile` is TTL-GATED server-side unless `force` — a rendered dashboard may poll it
  // without turning a cadence into a fetch-per-paint. `tileRefreshRow` reads the newest ledger
  // row: the freshness stamp, the per-source chips and the cost the header shows all come from
  // that ONE row, so the chip and the ledger cannot disagree.
  bindTile: (viewId: string, body: { ref: string } & Partial<TileRefresh>) =>
    put<{ tile: DashboardTile }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/binding`, body).then((d) => d.tile),
  refreshTile: (viewId: string, body: { ref: string; force?: boolean }) =>
    post<TileRefreshResult>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/refresh`, body),
  tileRefreshRow: (viewId: string, ref: string) =>
    get<{ row: TileRefreshRow }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/refresh?ref=${encodeURIComponent(ref)}`).then((d) => d.row),
  tileLedgerHref: (viewId: string, ref: string) =>
    `/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/refresh?ref=${encodeURIComponent(ref)}`,
  /** A genui control inside a TILE widget re-firing the tile's bound workflow (§5.4).
   *  Server-side the request is checked against that tile's FROZEN capability set, so a
   *  rendered button cannot introduce an action the binding never declared — hence a
   *  refusal is a normal, expected answer here (`ok:false` + `code`), not an error. */
  tileWidgetAction: (viewId: string, body: { ref: string; action: string; payload?: Record<string, unknown> }) =>
    post<{ ok: boolean; code?: string; message?: string; outcome?: string; violations?: string[][]; row?: TileRefreshRow }>(
      `/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/action`, body),

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
  // `defaultGitSources` = the rows PersonalClaw shipped (labelled "Default"); `builtinGitSources`
  // = the subset that cannot be removed (bundled into every read), so the UI hides a remove
  // control that would silently do nothing. The seeded registry is in the first, not the second.
  appCatalog: () => get<{ bundled: AppCatalogEntry[]; gitSources: string[]; defaultGitSources?: string[]; builtinGitSources?: string[]; localSources?: string[]; firstPartySources?: string[]; localApps?: AppCatalogEntry[]; remoteApps?: AppCatalogEntry[]; gitApps?: AppCatalogEntry[] }>('/api/apps/catalog'),
  appSources: () => get<{ sources: string[] }>('/api/apps/sources').then((d) => d.sources),
  addAppSource: (url: string) => post<{ ok: boolean; sources: string[] }>('/api/apps/sources', { url }),
  removeAppSource: (url: string) => del(`/api/apps/sources?url=${encodeURIComponent(url)}`),
  // Local-directory app sources (a dir of app subdirs; its apps surface in the Store).
  addLocalAppSource: (path: string) => post<{ ok: boolean; sources: string[] }>('/api/apps/local-sources', { path }),
  removeLocalAppSource: (path: string) => del(`/api/apps/local-sources?path=${encodeURIComponent(path)}`),
}
