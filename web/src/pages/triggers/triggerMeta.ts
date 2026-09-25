import { useEffect, useState } from 'react'
import { CalendarClock, Webhook, Bell, MessageSquare, ListPlus, Users, TerminalSquare, FileCode2, Zap, Anchor, Bot, Workflow, FolderClock, Globe, Moon, FileText, Inbox, Database, Plug, Wrench } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, type ScheduleJob, type HookItem, type HookEnforcement, type LifecycleEventInfo, type TriggerVariables, type Trigger as WireTrigger, type EventPattern } from '../../lib/api'
import { deriveKind, deriveMode, kindMeta as schedKindMeta, modeMeta as schedModeMeta } from '../schedule/scheduleMeta'
import { epochSeconds } from '../../lib/epoch'

// ── Trigger kind: schedule (a tick fires), lifecycle (an agent-loop event fires),
//    event (a data event — an inbox message or a memory write — fires), or store (a
//    unified TriggerStore kind with no legacy backend — file/web_watch/…) ──
export type TriggerKind = 'schedule' | 'lifecycle' | 'event' | 'store'
export interface TriggerKindMeta { key: TriggerKind; label: string; icon: LucideIcon; tone: string; hint: string }
export const TRIGGER_KINDS: TriggerKindMeta[] = [
  { key: 'schedule', label: 'Schedule', icon: CalendarClock, tone: 'var(--color-info)', hint: 'Fires on a clock — every N, on a cron, or once at a set time.' },
  { key: 'lifecycle', label: 'Lifecycle event', icon: Anchor, tone: 'var(--color-primary)', hint: 'Fires on an agent-loop event — a tool call, a prompt, session end, …' },
  { key: 'event', label: 'Data event', icon: Inbox, tone: 'var(--color-secondary)', hint: 'Fires on an inbox message, a memory write, or an app-contributed event matching a pattern you choose.' },
]

// ── Data-event patterns (EIAT-5). One row per wired `event_triggers.EVENT_PATTERNS` member,
//    in lockstep with the Python tuple. `source` is the origin the backend derives from the
//    pattern (never sent on the wire); `matcher` names the ONE spec field this pattern reads
//    (event_triggers.matches()) — so the form shows exactly that field and no inert extras. A
//    pattern with no matcher fires on every event from its source. ──
export type EventMatcherField = 'sender_glob' | 'address_glob' | 'key_glob' | 'content_re' | 'event_glob' | null
export interface EventPatternMeta {
  pattern: EventPattern
  source: 'inbox' | 'memory' | 'app'
  label: string
  desc: string
  matcher: EventMatcherField
  matcherLabel: string
  matcherHint: string
  matcherPlaceholder: string
  /** Whether an empty matcher is rejected server-side (InboxSender requires a sender_glob —
   *  otherwise it would fire on every message from its source). */
  matcherRequired: boolean
}
export const EVENT_PATTERN_META: EventPatternMeta[] = [
  { pattern: 'InboxMessage', source: 'inbox', label: 'Any inbox message', desc: 'Every accepted message from a watched inbox source (Slack, Telegram, email, …).', matcher: null, matcherLabel: '', matcherHint: '', matcherPlaceholder: '', matcherRequired: false },
  { pattern: 'InboxSender', source: 'inbox', label: 'Inbox message from a sender', desc: 'An inbox message whose sender matches a glob.', matcher: 'sender_glob', matcherLabel: 'Sender glob', matcherHint: 'Glob on the sender id (e.g. alice@example.com, U*, +1415*). Required.', matcherPlaceholder: 'alice@example.com', matcherRequired: true },
  { pattern: 'InboxAddress', source: 'inbox', label: 'Inbox message to an address', desc: 'An inbox message whose receiving address/channel matches a glob.', matcher: 'address_glob', matcherLabel: 'Address glob', matcherHint: 'Glob on the receiving address or channel (e.g. support@*, #alerts). Empty matches all.', matcherPlaceholder: 'support@*', matcherRequired: false },
  { pattern: 'MemoryUpdate', source: 'memory', label: 'Any memory write', desc: 'Every memory create, update, or delete.', matcher: null, matcherLabel: '', matcherHint: '', matcherPlaceholder: '', matcherRequired: false },
  { pattern: 'MemoryKeyPattern', source: 'memory', label: 'Memory write to a key', desc: 'A memory write whose key matches a glob.', matcher: 'key_glob', matcherLabel: 'Key glob', matcherHint: 'Glob on the memory key (e.g. project.acme.*). Empty matches nothing.', matcherPlaceholder: 'project.acme.*', matcherRequired: false },
  { pattern: 'ContentMatch', source: 'memory', label: 'Memory write matching content', desc: "A memory write whose value matches a regex (or substring if it isn't valid regex).", matcher: 'content_re', matcherLabel: 'Content matcher', matcherHint: 'Regex matched against the written value (substring fallback). Empty matches nothing.', matcherPlaceholder: 'invoice|payment', matcherRequired: false },
  // AUTO-A4. `matcherRequired: false` because an empty glob is the deliberate CATCH-ALL here, unlike
  // MemoryKeyPattern's empty glob (which matches nothing) — the backend's `matches()` documents the
  // asymmetry, and this row mirrors it rather than inventing a stricter form-side rule.
  { pattern: 'AppEvent', source: 'app', label: 'App event', desc: 'An event from an installed app that contributes a trigger source (a calendar, a device, a watched service).', matcher: 'event_glob', matcherLabel: 'Event', matcherHint: 'Pick a declared event, or glob the namespaced name (e.g. app:my-source:*). Empty matches every app event.', matcherPlaceholder: 'app:my-source:*', matcherRequired: false },
]
export function eventPatternMeta(pattern?: string): EventPatternMeta {
  return EVENT_PATTERN_META.find((p) => p.pattern === pattern) ?? EVENT_PATTERN_META[0]
}
/** The event-source icon, for the pattern option list and the panel row. */
export function eventSourceIcon(source: string): LucideIcon {
  if (source === 'inbox') return Inbox
  if (source === 'app') return Plug
  return Database
}
/** The event-source label. One mapper so the option list, the badge and the empty-state copy cannot
 *  disagree about what to call a source — the S164 lesson applied to naming rather than to colour. */
export function eventSourceLabel(source: string): string {
  if (source === 'inbox') return 'Inbox'
  if (source === 'app') return 'App'
  return 'Memory'
}
/** Every declared app event as `{ value: source_event, label }` options for the matcher picker.
 *  Empty when no app contributes a source — the form then falls back to a free-text glob, so a user
 *  with no source app installed still sees an honest field rather than a broken picker. */
export function appEventOptions(catalog: TriggerVariables | null): { value: string; label: string; description: string }[] {
  return (catalog?.app_sources ?? []).flatMap((s) =>
    s.events.map((e) => ({ value: e.source_event, label: `${s.label} · ${e.event}`, description: e.source_event })),
  )
}

/** The lifecycle-event picker's options: live events FIRST, dormant ones under an
 *  "advanced" heading that says why they are there (PEP-1).
 *
 *  A dormant event — one no code fires yet — reads like an equal choice in a flat list of
 *  fifteen, so the only feedback a newcomer gets is a trigger that saves and never runs.
 *  Ordering fixes half of that (`Combobox` groups by `group` in first-seen order, so the sort
 *  IS the grouping) and the heading fixes the rest. Dormant events stay PICKABLE rather than
 *  hidden: pre-wiring a trigger for an event that is about to exist is legitimate, and the
 *  form already warns at the point of choice.
 *
 *  🔑 THE HEADINGS ARE CONDITIONAL, and that is measured rather than defensive. The plan
 *  describes "~15 events, 7 of which warn 'never fires'"; `GET /api/triggers/variables` on a
 *  current build returns **15 events, 0 dormant** — the dormancy was closed after the plan was
 *  written. Labelling all fifteen "Live events" under one heading would be a distinction that
 *  distinguishes nothing, so `group` is left undefined while nothing is dormant and the two
 *  groups appear the moment something is. */
export function lifecycleEventOptions(
  catalog: TriggerVariables | null,
): { value: string; label: string; description: string; group?: string }[] {
  const all = catalog?.lifecycle ?? []
  const anyDormant = all.some((e) => e.dormant)
  return [...all]
    .sort((a, b) => Number(!!a.dormant) - Number(!!b.dormant))
    .map((e) => ({
      value: e.event,
      // The label marks a dormant event inline too, so the warning is not the FIRST thing a
      // user learns after committing — they see it while choosing.
      label: e.dormant ? `${e.label} · never fires` : e.label,
      description: e.desc,
      group: anyDormant ? (e.dormant ? 'Advanced — nothing fires these yet' : 'Live events') : undefined,
    }))
}

// ── Store-kind presentation: the "when" label/icon per store_kind. These automations are
//    created through the automation_* chat tools (e.g. "when a file in ~/notes changes"), so the
//    UI describes what each watches rather than offering a create form (the chat is the create
//    surface). An unknown store_kind falls back to a neutral label rather than rendering blank. ──
const STORE_KIND_META: Record<string, { label: string; icon: LucideIcon }> = {
  file: { label: 'On file change', icon: FolderClock },
  web_watch: { label: 'On web page change', icon: Globe },
  idle: { label: 'When idle', icon: Moon },
  run_completed: { label: 'When a run finishes', icon: Workflow },
  view: { label: 'View trigger', icon: FileText },
  webhook: { label: 'On webhook', icon: Webhook },
}
function storeKindMeta(storeKind?: string): { label: string; icon: LucideIcon } {
  return STORE_KIND_META[storeKind ?? ''] ?? { label: storeKind || 'Automation', icon: Zap }
}
// ── Trigger $variable catalog — server-sourced ──
// The lifecycle events + the $variables each exposes to a templated action come
// from the backend (GET /api/triggers/variables → hooks.LIFECYCLE_EVENT_CATALOG +
// schedule.SCHEDULE_VARS), so this UI never mirrors the payload shape. The catalog
// is small + static for a server build, so we fetch once and module-cache it.
export type LifecycleEventMeta = LifecycleEventInfo

let _catalogCache: TriggerVariables | null = null
let _catalogPromise: Promise<TriggerVariables> | null = null

/** Fetch (once, module-cached) the trigger variable catalog. Returns null while
 *  loading; consumers fall back to empty lists so the form still renders. */
export function useTriggerVariables(): TriggerVariables | null {
  const [cat, setCat] = useState<TriggerVariables | null>(_catalogCache)
  useEffect(() => {
    if (_catalogCache) { setCat(_catalogCache); return }
    if (!_catalogPromise) _catalogPromise = api.triggerVariables().then((c) => { _catalogCache = c; return c })
    let alive = true
    _catalogPromise.then((c) => { if (alive) setCat(c) }).catch(() => { _catalogPromise = null })
    return () => { alive = false }
  }, [])
  return cat
}

/** Find one lifecycle event's metadata in a fetched catalog (defaults to the
 *  first entry, or an empty shell while the catalog is still loading). */
export function lifecycleEventMeta(cat: TriggerVariables | null, event?: string): LifecycleEventMeta {
  const list = cat?.lifecycle ?? []
  return list.find((e) => e.event === event) ?? list[0] ?? { event: event ?? '', label: event ?? '', desc: '', vars: [], blocking: false }
}
/** Whether picking this event yields a trigger that will never fire (S67).
 *
 *  7 of the 15 declared events have no fire site: the API accepts the hook, the list shows it
 *  enabled, and nothing ever runs it. Read from the server catalog rather than a local list so
 *  wiring an event on the backend retires the warning automatically — a stale hard-coded list would
 *  eventually tell a user their WORKING hook is dead, which is worse than not warning at all. */
export function eventIsDormant(cat: TriggerVariables | null, event?: string): boolean {
  if (!event) return false
  return Boolean(cat?.lifecycle?.find((e) => e.event === event)?.dormant)
}
/** The reason an event is dormant, for the warning copy. Empty when it fires. */
export function eventDormancyReason(cat: TriggerVariables | null, event?: string): string {
  if (!event) return ''
  const found = cat?.lifecycle?.find((e) => e.event === event)
  return found?.dormant ? (found.dormant_reason ?? '') : ''
}
/** Whether an event fires ONLY through an agent's own `triggers` list (issue 610). Server-sourced
 *  like `eventIsDormant` — the two fire paths live in the backend, so the backend says which one
 *  an event rides. `false` while the catalog loads or on an older backend: the honest default is
 *  to make NO claim rather than badge from a guess (a wrong "needs an agent" on a hook that fires
 *  globally is the exact lie this reader exists to end). */
export function eventIsAgentScoped(cat: TriggerVariables | null, event?: string): boolean {
  if (!event) return false
  return Boolean(cat?.lifecycle?.find((e) => e.event === event)?.agent_scoped)
}
/** Tool events take a tool-name matcher; others take a context glob. */
export function eventTakesToolMatcher(event?: string): boolean {
  return event === 'PreToolUse' || event === 'PostToolUse'
}

// ── Action providers (renamed from hook providers) — icon + blurb per provider. ──
export const ACTION_ICON: Record<string, LucideIcon> = {
  bash: TerminalSquare, 'run-script': FileCode2, webhook: Webhook,
  notify: Bell, 'send-message': MessageSquare, 'create-task': ListPlus, 'invoke-agent': Users,
  'run-prompt': Bot, 'run-workflow': Workflow,
  // PR2-8: the health-scored remediation engine. Declared rather than left to the `Zap` default,
  // because the fallback is the same bolt every unmapped provider draws — a maintenance pass and a
  // shell command would be pixel-identical on the row a user manages automations from.
  'self-remediation': Wrench,
}
export function actionIcon(provider?: string): LucideIcon { return ACTION_ICON[provider ?? ''] ?? Zap }

/** Whether an action provider DELIVERS out to a channel (a reply/message a recipient sees), so a
 *  trigger wired to it warrants a draft-by-default reminder before it auto-replies (EIAT-5). This
 *  is a UI-copy heuristic, NOT a core capability flag — none exists yet; the mail-inbox app owns
 *  the real draft-by-default posture (EIAT-3). `send-message` is the one bundled send-capable
 *  provider today; a future channel provider named `send-*` inherits the note. */
export function actionIsSendCapable(provider?: string): boolean {
  if (!provider) return false
  return provider === 'send-message' || provider.startsWith('send-')
}

// Human label per action provider — the list/detail show this instead of the
// raw provider id or a legacy exec-mode guess. Keep in sync with the bundled
// action manifests' displayName.
const ACTION_LABEL: Record<string, string> = {
  bash: 'Bash', 'run-script': 'Script', webhook: 'Webhook',
  notify: 'Notify', 'send-message': 'Send Message', 'create-task': 'Create Task',
  'invoke-agent': 'Invoke Agent', 'run-prompt': 'Run Prompt', 'run-workflow': 'Run Workflow',
  // PR2-8. Spelled out rather than left to the id-prettifier, which would render
  // `Self remediation` — a second spelling of the backend `display_name` this map is meant to
  // stay in sync with.
  'self-remediation': 'Self-Remediation',
}
export function actionLabel(provider?: string): string {
  if (!provider) return 'Action'
  return ACTION_LABEL[provider] ?? (provider.charAt(0).toUpperCase() + provider.slice(1).replace(/-/g, ' '))
}

// ── Unified Trigger view-model. The list+detail speak "Trigger/Action" while the
//    bridge keeps the real ScheduleJob / HookItem underneath until the backend
//    unifies (triggers-unification.md). ──
export interface Trigger {
  kind: TriggerKind
  id: string                 // namespaced: "schedule:<id>" | "lifecycle:<id>" (unique across both stores)
  rawId: string              // the underlying store id
  name: string
  enabled: boolean
  whenLabel: string          // cadence string (schedule) | event label (lifecycle)
  whenIcon: LucideIcon
  whenTone: string
  actionLabel: string        // "Agent" / "Bash" / "Notify" …
  actionIcon: LucideIcon
  /** The raw action-provider name, when the row has one. Carried alongside the derived
   *  label/icon because the autonomy ladder is keyed on the provider IDENTITY, not on its
   *  presentation: `providerRungIndex` maps this exact string to the action type that
   *  governs it, which is the same thing the backend dispatch seams hold. */
  actionProvider?: string
  lastRunTs: number | null
  /** 🔴 THE RUN-OUTCOME vocabulary ONLY — `Outcome` / the run store's `status` / a hook's
   *  `last_status`. NEVER a `TriggerHealth` value: this field and `health` speak two different
   *  languages and the one dot that renders them collapsed them into each other (issue 496).
   *  `null` means "no run has reported an outcome", which is a fact, not a gap to fill. */
  runStatus: string | null
  /** The `TriggerHealth` ROLLUP — `ok | degraded | parked | failing`, plus the legacy `error`.
   *  Populated for EVERY kind that has one; `ok` on a row that has never fired is the dataclass
   *  DEFAULT, not an observation, which is why `triggerStatusMeta` gates it on `hasRun`. */
  health?: string | null
  /** Lifecycle state — `active | paused | autopaused | parked | quarantined | retired`. Sent by all
   *  three store-backed projections (schedule/store/event). */
  state?: string | null
  /** WHY the trigger is in a non-ok state, in words the user can act on ("Reaped after 1811s
   *  (exceeded 1800s deadline)"). Redacted server-side at the projection boundary. */
  lastError?: string | null
  /** Has this trigger EVER fired? `triggerStatusMeta` needs it to refuse a DEFAULT health rollup as
   *  a stand-in for a run outcome — the green-tick-beside-"never" half of issue 496. */
  hasRun?: boolean
  runCount: number | null
  usedBy: string[]           // lifecycle only
  /** lifecycle only — whether this hook's event CAN block the loop, and whether this hook
   *  actually does (G40). The server's verdict, passed through: `enforcement` is computed from the
   *  same `AgentProfile.triggers` binding the firing path reads, so "enforcing" on a row means a
   *  tool rejection would really come from it. `undefined` on an older backend → the row makes no
   *  enforcement claim at all, which is the safe direction. */
  blocking?: boolean
  enforcement?: HookEnforcement
  storeKind?: string         // store only: file | web_watch | idle | …
  /** Who wrote the row, and whether this machine's owner did not (TEAM-SHARED-ENTITIES §2.2 —
   *  TSE-4). `readOnly` is the SERVER's verdict, passed through rather than re-derived from
   *  `author`: the backend computes it with the same predicate that decides what the scheduler
   *  arms, so a row the page lets you toggle is always a row the service would actually fire. */
  author?: string
  readOnly?: boolean
  broken?: string[]          // parse ERRORS (S87 lenient load) — shown, not hidden
  /** WARNING-severity issues from the same load — advisory, not a fault (issue 531). Kept apart
   *  from `broken` because the row RUNS as authored: a sub-floor interval is a choice the backend
   *  allows, and rendering it as "needs attention" red would tell the user their working
   *  automation is broken. Before this the list had nowhere to put them, so the backend computed
   *  them and every surface dropped them. */
  warnings?: string[]
  schedule?: ScheduleJob
  hook?: HookItem
  store?: WireTrigger        // store only: the raw wire row for the inspector
  /** event only: the pattern key + the ONE matcher value that pattern reads, for the inspector.
   *  Deliberately NOT reusing the lifecycle `hook` field — the panel's dispatch falls through to
   *  `open.hook`, so an event row carrying one would open the wrong inspector. */
  eventPattern?: string
  eventMatcher?: string
  event?: WireTrigger        // event only: the raw wire row
}

/** The row `?open=<id>` names on the Triggers page, or null.
 *
 *  The page's own links use the namespaced id (`schedule:clock:x`, `store:file:notes`) and match
 *  first. But the trigger SUBSTRATE speaks the store's own id — `delivery.status_url`, the
 *  autopause attention card and the triage digest all mint `#/triggers?open=clock:x` — and an
 *  exact match on the namespaced id sent every one of those links to the list with no panel open.
 *  So a store id resolves too, and ONLY against the two kinds the trigger store backs (`schedule`
 *  and `store`): a lifecycle hook or a data-event trigger lives in a store of its own whose ids
 *  are minted independently, and a bare id is not allowed to wander across that boundary. */
export function resolveOpenTrigger(triggers: readonly Trigger[] | null | undefined, openId: string | null): Trigger | null {
  if (!triggers || !openId) return null
  return triggers.find((t) => t.id === openId)
    ?? triggers.find((t) => (t.kind === 'schedule' || t.kind === 'store') && t.rawId === openId)
    ?? null
}

export function scheduleToTrigger(j: ScheduleJob): Trigger {
  const km = schedKindMeta(deriveKind(j))
  const mm = schedModeMeta(deriveMode(j))
  // Prefer the canonical action provider for the label/icon (covers every
  // provider incl. run-prompt/run-workflow); fall back to the legacy exec-mode
  // heuristic only when no provider is present on the wire.
  const provider = j.action?.provider
  return {
    kind: 'schedule', id: `schedule:${j.id}`, rawId: j.id, name: j.name || j.id, enabled: j.enabled,
    whenLabel: j.schedule, whenIcon: km.icon, whenTone: km.tone,
    actionLabel: provider ? actionLabel(provider) : mm.label,
    actionIcon: provider ? actionIcon(provider) : mm.icon,
    actionProvider: provider,
    lastRunTs: j.last_run_ts ?? null,
    // Honest last-run status (T7): the newest run record's status — it persists across restarts and
    // carries launched/failure/timeout. The wire's `last_status` is NOT a second source for this
    // field: it is `health_status` under an alias (`schedule_view.py`), a different vocabulary, and
    // mixing the two here is what manufactured an ok-green CheckCircle beside the word "never" on
    // rows that had never fired (2 of 7 when first measured).
    //
    // 🔴 The health rollup now travels in `health`, unmixed, and `triggerStatusMeta` owns the
    // precedence for every kind — including the `hasRun` gate that used to live in this expression.
    // It moved because the LIST reached around it: `TriggersListPage` passed the raw wire pair
    // (`j.last_run_status, j.last_status`) straight to the reconciler, so the guard protected
    // `t.runStatus` while the rendered dot never read it, and 6 of 11 rows drew the green tick
    // again (issue 496). One gate, in the one place that renders the dot.
    runStatus: j.last_run_status || null,
    health: j.last_status || null,
    state: j.state || null,
    lastError: j.last_error || null,
    hasRun: j.last_run_ts != null || (j.run_count ?? 0) > 0,
    runCount: null, usedBy: [],
    schedule: j,
    // Parse errors carried, not hidden (S87 lenient load) — same as `storeToTrigger`, so a
    // schedule that failed to parse flags "needs attention" instead of listing as if healthy.
    broken: j.broken ?? [],
    warnings: j.warnings ?? [],
    author: j.author, readOnly: j.read_only === true,
  }
}
/** Humanize an event name for a list label without needing the fetched catalog
 *  (PreToolUse → "Pre tool use"). The full label/desc come from the catalog in
 *  the detail/create views. */
function humanizeEvent(event: string): string {
  if (!event) return ''
  const spaced = event.replace(/([a-z])([A-Z])/g, '$1 $2')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase()
}
export function hookToTrigger(h: HookItem): Trigger {
  return {
    kind: 'lifecycle', id: `lifecycle:${h.id}`, rawId: h.id, name: h.name, enabled: h.enabled,
    whenLabel: humanizeEvent(h.event), whenIcon: Anchor, whenTone: 'var(--color-primary)',
    actionLabel: actionLabel(h.provider), actionIcon: actionIcon(h.provider), actionProvider: h.provider,
    // A hook keeps no run store and no health rollup — `last_status` here IS the run outcome, from
    // the nine-member vocabulary `hooks.py::_record` closes (`ok`/`error`/`timeout`/`launched`/
    // `queued`/`blocked`/`advisory`/`held_for_rung`/`skipped_incident`). So it lands in `runStatus`,
    // and `health`/`state` stay absent rather than fabricated.
    lastRunTs: h.last_run || null, runStatus: h.last_status || null,
    hasRun: h.last_run != null || (h.run_count ?? 0) > 0,
    runCount: h.run_count, usedBy: h.used_by,
    blocking: h.blocking, enforcement: h.enforcement,
    schedule: undefined, hook: h,
  }
}

/** Project a store-backed Trigger (file/web_watch/idle/…) onto the shared view-model. The wire
 *  id is already `store:<kind>:<slug>`; `rawId` keeps the store's own `<kind>:<slug>` so the
 *  toggle/run/delete helpers re-namespace it. A broken row (S87 lenient load) carries its parse
 *  errors so the list can flag it rather than hiding an automation the user can't otherwise debug. */
export function storeToTrigger(t: WireTrigger): Trigger {
  const km = storeKindMeta(t.store_kind)
  const provider = t.action?.provider
  return {
    kind: 'store', id: t.id, rawId: t.raw_id, name: t.name || t.raw_id, enabled: t.enabled,
    whenLabel: km.label, whenIcon: km.icon, whenTone: 'var(--color-primary)',
    actionLabel: provider ? actionLabel(provider) : 'Action',
    actionIcon: provider ? actionIcon(provider) : Zap,
    actionProvider: provider,
    // 🔴 `t.health` used to land in `runStatus` and the list then read that field with the HEALTH
    // mapper — the one-field-two-vocabularies shape itself. It now travels as `health`; a store row
    // carries no run-outcome field at all, so `runStatus` is honestly null and `run_count` answers
    // "has it ever fired" for the `hasRun` gate.
    lastRunTs: null, runStatus: null,
    health: t.health || null, state: t.state || null, lastError: t.last_error || null,
    hasRun: (t.run_count ?? 0) > 0,
    runCount: t.run_count ?? null, usedBy: [],
    storeKind: t.store_kind, broken: t.broken ?? [], warnings: t.warnings ?? [], store: t,
    author: t.author, readOnly: t.read_only === true,
  }
}

/** Project a wire data-event trigger onto the list's `Trigger` shape.
 *
 *  The list renders `whenLabel` / `whenIcon` / `whenTone` / `actionLabel` / `actionIcon`, and the
 *  unified endpoint sends NONE of them — every kind gets its presentation from a converter here.
 *  Event rows had no converter, which is the other half of why they never appeared: even once
 *  fetched, `open.whenIcon` on a raw row would be `undefined` at render.
 *
 *  `whenLabel` comes from `eventPatternMeta()` — the same owner the create form and the pattern
 *  option list read, so a pattern cannot be called one thing on the form and another in the list. */
export function eventToTrigger(t: WireTrigger): Trigger {
  const pm = eventPatternMeta(t.pattern)
  const provider = t.action?.provider
  return {
    kind: 'event', id: t.id, rawId: t.raw_id || t.id.replace(/^event:/, ''), name: t.name || t.id, enabled: t.enabled,
    whenLabel: pm.label, whenIcon: eventSourceIcon(pm.source), whenTone: 'var(--color-secondary)',
    actionLabel: provider ? actionLabel(provider) : 'Action',
    actionIcon: provider ? actionIcon(provider) : Zap,
    actionProvider: provider,
    // A data event has no clock, so there is no next run and no duration to show. `runCount` is
    // the fire count — the one live number an event row can honestly report, and the `hasRun`
    // signal for the dot.
    //
    // 🔴 `state: null` AND `runStatus: null` WERE HARDCODED (issue 496). `_serialize_event` sends
    // `state`, `health` and `last_error`, and its docstring says why: *"`health` rides along so the
    // shared `triggerHealthMeta` mapper works here exactly as it does for store triggers, rather
    // than a third vocabulary on a third surface."* This converter threw all three away, so the
    // backend's whole point was inert. Measured on a live gateway: an event trigger PARKED because
    // the app that owns its event was uninstalled rendered the neutral "never run" dot —
    // indistinguishable from a healthy active one — with the reason on the wire, unread.
    //
    // `runStatus` stays null on purpose: this store keeps no run records, so there IS no run
    // outcome. The rollup goes in `health`, where the mapper that speaks its vocabulary reads it.
    lastRunTs: null, runStatus: null,
    health: t.health || null, state: t.state || null, lastError: t.last_error || null,
    hasRun: (t.fire_count ?? 0) > 0,
    runCount: t.fire_count ?? null, usedBy: [],
    // Carried so the inspector can show the pattern + matcher without refetching.
    eventPattern: t.pattern, eventMatcher: eventMatcherValue(t, pm.matcher), event: t,
    // Uniform with scheduleToTrigger/storeToTrigger: the server's verdict, passed through.
    // Inert until `_serialize_event` sends attribution, but the mapper should not be the
    // reason a foreign event row renders an action row its siblings would hide.
    author: t.author, readOnly: t.read_only === true,
  }
}

/** The ONE matcher value this pattern reads, as a display string. `eventPatternMeta().matcher`
 *  names the field; anything else on the row is inert for this pattern, so showing it would
 *  claim a constraint that does not apply. */
export function eventMatcherValue(t: WireTrigger, field: EventMatcherField): string {
  if (!field) return ''
  return String((t as unknown as Record<string, unknown>)[field] ?? '')
}

export function relPast(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return 'never'
  const s = Date.now() / 1000 - t
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
