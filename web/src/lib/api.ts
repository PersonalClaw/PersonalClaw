// PersonalClaw API client (web). Matches the real backend contract:
// root-relative /api paths, X-Session-Key header on every call, same-origin
// (cookie pc_token_<port> rides along via the dev proxy). See the composer
// API contract in docs.

const SK = { 'X-Session-Key': 'dashboard:ui' }

/** Read an error response body and surface the backend's {"error": "..."} message as
 *  a readable sentence rather than raw JSON text. Shared by the JSON helpers and any
 *  hand-rolled fetch (file upload, streams) so error UX is uniform. */
async function errText(r: Response): Promise<string> {
  const text = await r.text().catch(() => '')
  try { const parsed = JSON.parse(text); if (parsed && typeof parsed.error === 'string') return parsed.error } catch { /* not JSON */ }
  return text || `HTTP ${r.status}`
}

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
export interface AppPermissionsWire {
  api?: string[]; events?: string[]; mcpTools?: string[]
  storage?: boolean; network?: boolean; memory?: string; cron?: boolean; agent?: boolean
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
}
export interface ChatFolder { id: string; name: string; order?: number; collapsed?: boolean; parent_id?: string }
export interface ChatTag { id: string; name: string; color?: string; order?: number; status?: boolean }
// Magic re-tag batch job (POST/GET /api/sessions/retag-all). status 'idle' only
// appears on the GET before any job has run.
export interface RetagJob { id?: string; status: 'idle' | 'running' | 'done' | 'error' | 'cancelled'; done?: number; total?: number; updated?: number; skipped?: number; errors?: number; current?: string; error?: string }
export interface TagColumn { id: string; name?: string; tag_ids?: string[]; mode?: 'any' | 'all' | 'none'; order?: number; include_untagged?: boolean }
export interface ChatHistoryMsg {
  role: string; content: string; ts?: string; cls?: string
  // tool/permission messages carry meta {tool_call_id, input, purpose, output?, done?}
  meta?: { tool_call_id?: string; input?: string; purpose?: string; output?: string; done?: boolean; tool?: string }
}

// ── workspace / build entity types ──
export interface NotificationItem { kind: string; title: string; body: string; ts: string; job_id?: string; loop_id?: string; loop_kind?: string; acked: boolean }
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
  run_id?: string; job_id?: string; job_name?: string
  trigger?: string                          // "manual" | "scheduled"
  started_at?: number; finished_at?: number; duration_ms?: number
  status?: string                           // "success" | "error"
  summary?: string; error?: string; trace?: string
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
export interface ProjectItem { id: string; name: string; is_default?: boolean; status?: 'active' | 'archived'; workspace_dir?: string; context_dir?: string; name_locked?: boolean; agent_instructions_template?: string; brief?: string; task_list_count?: number; created_at?: string; updated_at?: string }
export interface ProjectLinkedItem { id: string; name: string; status: string; error_message?: string | null }
export interface TaskListItem { id: string; name: string; project_id: string; agent_instructions_template?: string; created_at?: string; updated_at?: string }
export interface BlockReason { is_blocked?: boolean; blocking_task_ids?: string[]; blocking_task_titles?: string[]; message?: string }
export interface TaskItem {
  id: string; title: string; status: string; description?: string
  provider?: string; project?: string; assignee?: string; priority?: string
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

// A step is either an inline step (title + instruction) OR a reference to
// another workflow (ref = workflow id) — enabling reusable, composed SOPs.
// `ref` is forward-looking: the backend doesn't expand refs yet, so the UI
// marks it "soon".
export interface WorkflowStep { id?: string; title: string; instruction: string; ref?: string }
export type WorkflowScope = 'global' | 'workspace' | 'agent' | 'session'
// Workflow composition graph: nodes (this + referenced workflows), ref edges,
// any detected cycles, and the depth-expanded step tree.
export interface WorkflowGraph {
  nodes: Array<{ id: string; name: string }>
  edges: Array<{ from?: string; to?: string; source?: string; target?: string }>
  cycles: string[][]
  // Depth-expanded step tree. Each item carries provenance: source_workflow (the
  // workflow this step came from) + depth (0 = the top workflow's own steps; >0 =
  // pulled in via a ref-step). Matches composition.build_graph's emitted shape.
  expanded: Array<{ title?: string; instruction?: string; source_workflow?: string; depth?: number }>
}
export interface WorkflowItem {
  id: string; name: string; description: string; steps: WorkflowStep[]
  tags?: string[]; scope?: WorkflowScope; scope_ref?: string; match_text?: string
  enabled?: boolean; version?: string; provider?: string; created_at?: string; updated_at?: string
}
export interface WorkflowMatch {
  eligible: Array<{ id: string; name: string; scope: string; scope_ref: string }>
  match: { id: string; name: string; scope: string; score: number; method: string } | null
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
export interface ToolItem { name: string; description: string; provider: string; parameters?: Record<string, unknown>; requires_approval?: boolean; risk_level?: 'safe' | 'caution' | 'destructive'; disabled?: boolean; locked?: boolean; providerDisabled?: boolean }
export interface ToolLoadFailure { provider: string; error: string }
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
// Unified Trigger wire shape from /api/triggers (both kinds). The schedule
// helpers project it onto ScheduleJob; the lifecycle helpers onto HookItem.
export interface TriggerAction { provider: string; config: Record<string, unknown> }
export interface Trigger {
  kind: 'schedule' | 'lifecycle'; id: string; raw_id: string; name: string; enabled: boolean
  action: TriggerAction
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
export interface LifecycleEventInfo { event: string; label: string; desc: string; vars: string[]; blocking: boolean }
export interface TriggerVariables { schedule: string[]; lifecycle: LifecycleEventInfo[] }
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
export type InboxItemStatus = 'pending' | 'sent' | 'dismissed' | 'handled'
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
}
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
  alert_keywords: string[]; alert_on_name_mention: boolean; auto_cleanup_enabled: boolean
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
// Portability (import/export archive). Manifest is the zip's MANIFEST.json;
// preview validates without applying, import returns what was merged/replaced.
export interface PortabilityManifest {
  version: number; format: string; created_at: string; hostname: string; user: string
  contents: Record<string, number>
}
export interface PortabilityPreviewResult { ok: boolean; error?: string; manifest?: PortabilityManifest }
export interface PortabilityImportResult { ok: boolean; error?: string; summary?: { mode: string; items: string[] }; manifest?: PortabilityManifest }
// Update + changelog.
export interface UpdateCheck { available: boolean; changes: string; checked: boolean; auto_update: boolean; version?: string; latest?: string; kind?: 'git' | 'pip' | 'container' | 'desktop'; current?: string; update_available?: boolean; commits_behind?: number | null; apply_method?: string; instructions?: string[]; update_dev_mode?: boolean; release_notes?: string }

// settings entity payloads
export interface NotificationSettings {
  mute_all: boolean; quiet_hours_enabled: boolean; quiet_hours_start: string; quiet_hours_end: string
  min_severity: string
}
export interface MemorySettings { history_idle_hours: number; history_max_days: number; migrated?: boolean; l1_manifest?: boolean; active_recall?: boolean; proactive_commitments?: boolean; vault_enabled?: boolean; vault_path?: string }
export interface MemoryVaultStatus { enabled: boolean; path: string; files: number; exists: boolean }
export interface MemoryVaultSyncResult { records: number; files: number; written: number; pruned: number; path: string }
export interface DailyDigest { day: string; text: string; created_at: string }
export interface MemoryStats {
  semantic_active: number; semantic_deleted: number; episodic_active: number; episodic_deleted: number
  events_count: number; embedded_count: number; embedding_provider?: string; has_legacy_memory?: boolean; migrated?: boolean
}
// A semantic memory entry. `value_json` is a JSON-encoded value (often double-
// encoded) — parse defensively for display.
export interface SemanticEntry { key: string; value_json?: string; created_at?: string; updated_at?: string; confidence?: number; source?: string; scope?: string; scope_ref?: string; tier?: string; recall_count?: number }
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
export type ProjectionStrategy = 'log' | 'diff' | 'json' | 'test' | 'csv'
export interface ProjectionRule { name: string; match_regex: string; strategy: ProjectionStrategy }

export interface SystemAgentStats {
  messages_received: number; messages_success: number; messages_failed: number
  tool_approvals: number; tool_denials: number; tool_auto_approved: number
  sessions_created: number; subagents_spawned: number; subagents_completed: number
  input_tokens: number; output_tokens: number; cache_read_tokens?: number
  total_turns: number; total_duration_ms: number
}
export interface SystemInfo {
  hostname: string; os: string; platform: string; python: string; arch: string; pid: number; cpu_count: number; cwd: string
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

// A model discovered from a configured backend (the unit you bind to a use-case).
export interface AvailableModel { id: string; name: string; capabilities: string[]; provider: string; provider_type: string; size?: number; downloaded?: boolean; gated?: boolean; description?: string; size_mb?: number; source?: string }
export interface ProviderModels { name: string; displayName?: string; type: string; models: AvailableModel[]; error?: string; searchable?: boolean; local?: boolean }
export interface ProviderTestResult { ok: boolean; status?: string; message: string }
// A local downloadable model (the uniform LocalModel shape from any local provider).
export interface LocalModel { name: string; id: string; size_mb: number; size: number; description: string; downloaded: boolean; capabilities: string[]; gated: boolean; source: string }
// A background local-model download job (matches dashboard/model_downloads.py).
export interface DownloadJob {
  id: string; provider: string; model: string
  status: 'running' | 'done' | 'error' | 'cancelled'
  phase: string; bytes: number; size_bytes: number; error: string
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
  // server-stored message display prefs (consistent across browsers)
  send_on_enter: boolean; show_timestamps: boolean; show_thinking_inline: boolean
  simplified_tool_names: boolean; confirm_close_session: boolean
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
export interface GrillPhaseStep { title: string; prompt: string }
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

export type ArtifactKind = 'widget' | 'html' | 'react' | 'markdown' | 'svg' | 'json' | 'text' | 'infographic' | 'document' | 'image'
export type ArtifactSource = 'chat' | 'cron' | 'subagent' | 'manual' | 'import'
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
  // full backend config (read the `agent` subtree for Agent defaults) + the
  // single-field PATCH (allowlisted dotted paths — see _EDITABLE_CONFIG).
  personalclawConfig: () => get<Record<string, any>>('/api/config/personalclaw'),
  patchConfig: (path: string, value: unknown) => patch<Record<string, any>>('/api/config/personalclaw', { path, value }),

  // ── Memory Studio: health, observability, deep recall, promotion, lessons ──
  memoryGraph: () => get<MemoryGraphData>('/api/memory/graph'),
  memoryLint: () => get<MemoryLint>('/api/memory/lint'),
  memoryObservability: () => get<MemoryObservability>('/api/memory/observability'),
  memoryRecall: (q: string) => get<{ result: string; query: string; deep: boolean }>(`/api/memory/recall?q=${encodeURIComponent(q)}`),
  memoryPromote: () => post<{ ok: boolean; promoted: number }>('/api/memory/promote'),
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
  sessionsSearch: (q: string) => get<{ sessions: Array<{ key: string; title?: string; messages?: number }> }>(`/api/sessions/search?q=${encodeURIComponent(q)}`).then((d) => d.sessions),

  // ── Background subagents monitor (spawned by crons / loops / Slack) ──
  spawnedAgents: () => get<{ agents: SpawnedAgent[] }>('/api/spawn').then((d) => d.agents),
  cancelSpawnedAgent: (id: string) => del(`/api/spawn/${encodeURIComponent(id)}`),
  clearSpawnedAgents: () => del('/api/spawn'),

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

  // ── Workflow composition graph + scope promotion ──
  workflowGraph: (id: string) => get<WorkflowGraph>(`/api/workflows/${encodeURIComponent(id)}/graph`),
  promoteWorkflow: (id: string, scope: WorkflowScope, scope_ref?: string) =>
    post<WorkflowItem>(`/api/workflows/${encodeURIComponent(id)}/promote`, { scope, scope_ref }),

  // ── Chat turn-level controls ──
  /** Silently prime the next turn with background context (no visible message, no turn). */
  briefSession: (key: string, content: string, source = 'user-brief') =>
    post<{ ok: boolean }>(`/api/chat/sessions/${encodeURIComponent(key)}/context`, { content, source, ephemeral: false }),
  /** Set a live session's working directory (agent cwd + memory-partition scope). */
  setSessionWorkspaceDir: (key: string, workspace_dir: string) =>
    post<{ ok: boolean; workspace_dir?: string }>(`/api/chat/sessions/${encodeURIComponent(key)}/workspace-dir`, { workspace_dir }),

  // ── Contextual prompt starters (background-computed from memory + recent activity) ──
  suggestions: (force = false) => get<{ suggestions: string[]; generated_at: number; stale: boolean }>(`/api/suggestions${force ? '?force=1' : ''}`),

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
  chatSessions: () => get<ChatSessionSummary[]>('/api/chat/sessions'),
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
  editResend: (session: string, content: string, ts?: string, index?: number, client_ts?: string) =>
    post<{ ok: boolean }>(`/api/chat/sessions/${session}/edit-resend`,
      // Prefer the original turn's ts to LOCATE the message; always send the index
      // as a fallback (un-hydrated optimistic turns have no ts) + a fresh client_ts
      // the backend stores on the re-appended message so a repeat edit still matches.
      { content, ...(ts ? { ts } : {}), ...(index !== undefined ? { index } : {}), ...(client_ts ? { client_ts } : {}) }),
  forkSession: (session: string, at_message_index?: number) =>
    post<{ ok: boolean; key: string; title: string; messages: number; prompt?: string }>(`/api/chat/sessions/${session}/fork`, at_message_index != null ? { at_message_index } : {}),
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
  triggersHistory: (limit = 20, offset = 0) =>
    get<{ runs: ScheduleRun[]; total: number }>(`/api/triggers/history?limit=${limit}&offset=${offset}`),
  unackNotification: (ts: string) => post('/api/notifications/unack', { ts }),
  ackAllNotifications: () => post('/api/notifications/ack-all'),
  deleteNotification: (ts: string) => fetch('/api/notifications', { method: 'DELETE', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ ts }) }).then((r) => { if (!r.ok) throw new Error('delete failed') }),
  clearNotifications: () => post('/api/notifications/clear'),

  // Triggers — the unified surface (schedule + lifecycle). The schedule helpers
  // below speak the schedule wire shape the shared Schedule* components already
  // use; the api layer namespaces the id (schedule:<id>) and routes to /api/triggers.
  triggers: (type?: 'schedule' | 'lifecycle') =>
    get<{ triggers: Trigger[]; server_tz: string }>(`/api/triggers${type ? `?type=${type}` : ''}`),
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
    post(`/api/triggers/schedule:${encodeURIComponent(id)}/run`, dryRun ? { dry_run: true } : undefined),
  enableSchedule: (id: string, enabled: boolean) => post(`/api/triggers/schedule:${encodeURIComponent(id)}/toggle`, { enabled }),
  scheduleToChat: (id: string) => post<{ ok: boolean; session: string }>(`/api/triggers/schedule:${encodeURIComponent(id)}/to-chat`),
  scheduleHistory: (id: string, limit = 10, offset = 0) => get<{ runs: ScheduleRun[]; total: number }>(`/api/triggers/schedule:${encodeURIComponent(id)}/history?limit=${limit}&offset=${offset}`),
  scheduleRunDetail: (id: string, runId: string) => get<{ run: ScheduleRun }>(`/api/triggers/schedule:${encodeURIComponent(id)}/history/${encodeURIComponent(runId)}`).then((d) => d.run),
  triggerVariables: () => get<TriggerVariables>('/api/triggers/variables'),

  // tasks
  tasks: (opts: { project?: string; task_list?: string; status?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams()
    if (opts.project) qs.set('project', opts.project)
    if (opts.task_list) qs.set('task_list', opts.task_list)
    if (opts.status) qs.set('status', opts.status)
    if (opts.limit) qs.set('limit', String(opts.limit))
    const s = qs.toString()
    return get<{ tasks: TaskItem[]; total: number }>(`/api/tasks${s ? `?${s}` : ''}`)
  },
  task: (id: string, provider?: string) => get<TaskItem>(`/api/tasks/${encodeURIComponent(id)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  taskGraph: (provider?: string) => get<TaskGraphData>(`/api/tasks/graph${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  createTask: (body: Record<string, unknown>) => post<TaskItem>('/api/tasks', body),
  updateTask: (id: string, body: Record<string, unknown>) => put<TaskItem>(`/api/tasks/${encodeURIComponent(id)}`, body),
  deleteTask: (id: string, provider?: string) => del(`/api/tasks/${encodeURIComponent(id)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  taskComments: (id: string, provider?: string) => get<{ comments: TaskComment[] }>(`/api/tasks/${encodeURIComponent(id)}/comments${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`).then((d) => d.comments),
  addTaskComment: (id: string, body: string, provider?: string) => post<TaskComment>(`/api/tasks/${encodeURIComponent(id)}/comments`, { body, provider }),
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
  projectLinked: (id: string) => get<{ loops: ProjectLinkedItem[]; code: ProjectLinkedItem[]; artifacts: { slug: string; name: string; kind: string }[]; chats: { key: string; title: string; running: boolean }[] }>(`/api/projects/${encodeURIComponent(id)}/linked`),
  createProject: (body: { name: string; brief?: string; agent_instructions_template?: string; workspace_dir?: string; name_locked?: boolean }) => post<ProjectItem>('/api/projects', body),
  updateProject: (id: string, body: Record<string, unknown>) => put<ProjectItem>(`/api/projects/${encodeURIComponent(id)}`, body),
  deleteProject: (id: string, force = false) => del(`/api/projects/${encodeURIComponent(id)}${force ? '?force=true' : ''}`),
  taskLists: (projectId?: string) => get<{ task_lists: TaskListItem[] }>(`/api/task-lists${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`).then((d) => d.task_lists),
  createTaskList: (body: Record<string, unknown>) => post<TaskListItem>('/api/task-lists', body),
  updateTaskList: (id: string, body: Record<string, unknown>) => put<TaskListItem>(`/api/task-lists/${encodeURIComponent(id)}`, body),
  deleteTaskList: (id: string) => del(`/api/task-lists/${encodeURIComponent(id)}`),
  resetTaskList: (id: string) => post<{ ok: boolean; reset_task_ids: string[] }>(`/api/task-lists/${encodeURIComponent(id)}/reset`, {}),

  // workflows
  workflows: () => get<{ workflows: WorkflowItem[] }>('/api/workflows').then((d) => d.workflows),
  createWorkflow: (body: Record<string, unknown>) => post<WorkflowItem>('/api/workflows', body),
  updateWorkflow: (id: string, body: Record<string, unknown>) => put<WorkflowItem>(`/api/workflows/${encodeURIComponent(id)}`, body),
  deleteWorkflow: (id: string) => del(`/api/workflows/${encodeURIComponent(id)}`),
  previewWorkflowMatch: (query: string) => post<WorkflowMatch>('/api/workflows/preview-match', { query }),
  workflowProviders: () => get<{ providers: string[] }>('/api/workflows/providers').then((d) => d.providers),
  workflowsUsedBy: (agent: string) => get<{ agent: string; workflows: WorkflowItem[] }>(`/api/workflows/used-by/${encodeURIComponent(agent)}`).then((d) => d.workflows),

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
  inbox: () => get<InboxItem[]>('/api/inbox'),
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
  memorySettings: () => get<MemorySettings>('/api/memory/settings'),
  saveMemorySettings: (s: Partial<MemorySettings>) => put<MemorySettings>('/api/memory/settings', s),
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
  setSecurityEgress: (cfg: EgressPolicyConfig) => patch<Record<string, any>>('/api/config/personalclaw', { path: 'security.egress', value: cfg }),
  // Tool-output projection rules (TokenJuice OP6). Read from the whole-config GET
  // (tools.projection_rules); written via the config PATCH allowlist.
  projectionRules: () => get<Record<string, any>>('/api/config/personalclaw').then(
    (c) => ((c?.tools?.projection_rules ?? []) as ProjectionRule[])),
  setProjectionRules: (rules: ProjectionRule[]) => patch<Record<string, any>>('/api/config/personalclaw', { path: 'tools.projection_rules', value: rules }),

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

  // ── artifacts (named, versioned content — a curated subset of files) ──
  artifacts: (f?: { tag?: string; kind?: string; q?: string; source?: string; source_path?: string }) => {
    const qs = new URLSearchParams(Object.entries(f ?? {}).filter(([, v]) => v) as [string, string][]).toString()
    return get<{ artifacts: Artifact[] }>(`/api/artifacts${qs ? `?${qs}` : ''}`).then((d) => d.artifacts)
  },
  artifact: (slug: string) => get<Artifact>(`/api/artifacts/${encodeURIComponent(slug)}`),
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
