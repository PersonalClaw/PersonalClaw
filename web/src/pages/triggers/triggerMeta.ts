import { useEffect, useState } from 'react'
import { CalendarClock, Webhook, Bell, MessageSquare, ListPlus, Users, TerminalSquare, FileCode2, Zap, Anchor, Bot, Workflow } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, type ScheduleJob, type HookItem, type LifecycleEventInfo, type TriggerVariables } from '../../lib/api'
import { deriveKind, deriveMode, kindMeta as schedKindMeta, modeMeta as schedModeMeta } from '../schedule/scheduleMeta'

// ── Trigger kind: schedule (a tick fires) vs lifecycle (an agent-loop event fires) ──
export type TriggerKind = 'schedule' | 'lifecycle'
export interface TriggerKindMeta { key: TriggerKind; label: string; icon: LucideIcon; tone: string; hint: string }
export const TRIGGER_KINDS: TriggerKindMeta[] = [
  { key: 'schedule', label: 'Schedule', icon: CalendarClock, tone: 'var(--color-info)', hint: 'Fires on a clock — every N, on a cron, or once at a set time.' },
  { key: 'lifecycle', label: 'Lifecycle event', icon: Anchor, tone: 'var(--color-primary)', hint: 'Fires on an agent-loop event — a tool call, a prompt, session end, …' },
]
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
/** Tool events take a tool-name matcher; others take a context glob. */
export function eventTakesToolMatcher(event?: string): boolean {
  return event === 'PreToolUse' || event === 'PostToolUse'
}

// ── Action providers (renamed from hook providers) — icon + blurb per provider. ──
export const ACTION_ICON: Record<string, LucideIcon> = {
  bash: TerminalSquare, 'run-script': FileCode2, webhook: Webhook,
  notify: Bell, 'send-message': MessageSquare, 'create-task': ListPlus, 'invoke-agent': Users,
  'run-prompt': Bot, 'run-workflow': Workflow,
}
export function actionIcon(provider?: string): LucideIcon { return ACTION_ICON[provider ?? ''] ?? Zap }

// Human label per action provider — the list/detail show this instead of the
// raw provider id or a legacy exec-mode guess. Keep in sync with the bundled
// action manifests' displayName.
const ACTION_LABEL: Record<string, string> = {
  bash: 'Bash', 'run-script': 'Script', webhook: 'Webhook',
  notify: 'Notify', 'send-message': 'Send Message', 'create-task': 'Create Task',
  'invoke-agent': 'Invoke Agent', 'run-prompt': 'Run Prompt', 'run-workflow': 'Run Workflow',
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
  lastRunTs: number | null
  lastStatus: string | null
  runCount: number | null
  usedBy: string[]           // lifecycle only
  schedule?: ScheduleJob
  hook?: HookItem
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
    lastRunTs: j.last_run_ts ?? null,
    // Honest last-run status (T7): prefer the newest run record's status (persists
    // across restarts; carries launched/failure/timeout) over last_status (only
    // ok/error — a fire-and-forget run shows "ok" there, overstating it).
    lastStatus: j.last_run_status || j.last_status || null,
    runCount: null, usedBy: [],
    schedule: j,
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
    actionLabel: actionLabel(h.provider), actionIcon: actionIcon(h.provider),
    lastRunTs: h.last_run || null, lastStatus: h.last_status || null, runCount: h.run_count, usedBy: h.used_by,
    schedule: undefined, hook: h,
  }
}

export function relPast(ts?: number | null): string {
  if (!ts) return 'never'
  const s = Date.now() / 1000 - ts
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
