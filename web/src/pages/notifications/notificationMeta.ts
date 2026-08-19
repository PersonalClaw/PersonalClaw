import { Bell, BellRing, CheckCircle2, Clock, Webhook, Bot, HeartPulse, Info, AlertTriangle, Target, XCircle, Newspaper, MessageSquare, MessageCircle, Activity, Lightbulb, Archive, Route, HelpCircle, ShieldQuestion, RefreshCw, Receipt } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { NotificationItem } from '../../lib/api'

// ── kind → icon + tone (the `kind`s the backend actually emits) ──
//
// Every key here is a wire string reachable from `src/personalclaw/notification_kinds.py`
// — i.e. a registered kind's bare `kind`, plus the `_LEGACY_FLAT` / `_ATTENTION_FLAT`
// strings emitters actually pass to `state.notify()`. Labels are the registry's own
// declared display names, NOT invented here: a kind whose label this map lacks used to
// fall through to the raw lowercase key, so the filter row read "Info", "Subagent",
// "Success" beside a bare "proposal". The registry is the authority for the wording; if
// you add a kind there, add its row here with the SAME label.
export interface KindMeta { label: string; icon: LucideIcon; tone: string }
const KINDS: Record<string, KindMeta> = {
  cron: { label: 'Scheduled job result', icon: Clock, tone: 'var(--color-info)' },
  schedule: { label: 'Scheduled job result', icon: Clock, tone: 'var(--color-info)' },
  result: { label: 'Scheduled job result', icon: CheckCircle2, tone: 'var(--color-ok)' },
  hook: { label: 'Trigger fired', icon: Webhook, tone: 'var(--color-primary)' },
  fired: { label: 'Trigger fired', icon: Webhook, tone: 'var(--color-primary)' },
  agent: { label: 'Agent message', icon: Bot, tone: 'var(--color-primary)' },
  subagent: { label: 'Subagent update', icon: Bot, tone: 'var(--color-primary)' },
  message: { label: 'Agent message', icon: MessageSquare, tone: 'var(--color-on-surface-low)' },
  agent_request: { label: 'Agent request', icon: ShieldQuestion, tone: 'var(--color-warn)' },
  heartbeat: { label: 'Heartbeat', icon: HeartPulse, tone: 'var(--color-info)' },
  status: { label: 'Heartbeat', icon: HeartPulse, tone: 'var(--color-info)' },
  inbox_alert: { label: 'Inbox alert', icon: BellRing, tone: 'var(--color-warn)' },
  alert: { label: 'Inbox alert', icon: BellRing, tone: 'var(--color-warn)' },
  loop: { label: 'Loop progress', icon: Target, tone: 'var(--color-primary)' },
  complete: { label: 'Loop complete', icon: CheckCircle2, tone: 'var(--color-ok)' },
  // Registered under BOTH loop/failed ("Loop failed") and cron/failed ("Scheduled job
  // failed"). The loop wording wins: its sibling bare kinds (complete/stalled/progress)
  // are all loop-domain, and a scheduled-job failure reaches the UI as the flat `cron`.
  failed: { label: 'Loop failed', icon: XCircle, tone: 'var(--color-danger)' },
  stalled: { label: 'Loop stalled or blocked', icon: AlertTriangle, tone: 'var(--color-warn)' },
  needs_input: { label: 'Loop needs your input', icon: HelpCircle, tone: 'var(--color-warn)' },
  progress: { label: 'Loop progress', icon: Activity, tone: 'var(--color-info)' },
  proposal: { label: 'Skill proposal', icon: Lightbulb, tone: 'var(--color-primary)' },
  // Their own rows, because the backend now emits distinct wire strings for them. Without an
  // entry each would fall to `kindMeta`'s default and display the raw wire value.
  learning_proposal: { label: 'Learning proposal', icon: Lightbulb, tone: 'var(--color-primary)' },
  planning_proposal: { label: 'Planning proposal', icon: Lightbulb, tone: 'var(--color-primary)' },
  digest: { label: 'Daily digest', icon: Newspaper, tone: 'var(--color-on-surface-low)' },
  // system/usage_recap (MRT-3). Its bare kind IS its wire string, so one row covers both.
  usage_recap: { label: 'Monthly usage recap', icon: Receipt, tone: 'var(--color-on-surface-low)' },
  // knowledge/research_finding (WF2KNO-12). Newspaper like the digest — both are written
  // output — but the primary tone, not the low one: a finding is a thing to read, whereas
  // the digest is the wrapper it may arrive in.
  research_finding: { label: 'Research report finding', icon: Newspaper, tone: 'var(--color-primary)' },
  // apps/update — bare kind `update` (persisted history) + the `app_update` wire string
  // emit_attention_item actually hands state.notify(). Both map to the registry's label.
  update: { label: 'App update available', icon: RefreshCw, tone: 'var(--color-primary)' },
  app_update: { label: 'App update available', icon: RefreshCw, tone: 'var(--color-primary)' },
  session: { label: 'Session notice', icon: MessageCircle, tone: 'var(--color-on-surface-low)' },
  retire: { label: 'Retired a learned signal', icon: Archive, tone: 'var(--color-primary)' },
  feedback_retire: { label: 'Retired a learned signal', icon: Archive, tone: 'var(--color-primary)' },
  route_drift: { label: 'App route drift', icon: Route, tone: 'var(--color-primary)' },
  'app.route.drift': { label: 'App route drift', icon: Route, tone: 'var(--color-primary)' },
  success: { label: 'Success', icon: CheckCircle2, tone: 'var(--color-ok)' },
  warning: { label: 'System warning', icon: AlertTriangle, tone: 'var(--color-warn)' },
  error: { label: 'System error', icon: AlertTriangle, tone: 'var(--color-danger)' },
  info: { label: 'Notice', icon: Info, tone: 'var(--color-on-surface-low)' },
  generic: { label: 'Uncategorized', icon: Bell, tone: 'var(--color-on-surface-low)' },
}
// The fallback is KEPT deliberately: the backend registry is fail-OPEN (an unregistered
// pair still delivers, as system/generic), so a kind added backend-side before this map
// learns about it must still render something usable rather than crash or vanish.
export function kindMeta(kind: string): KindMeta {
  return KINDS[kind] ?? { label: kind || 'Notification', icon: Bell, tone: 'var(--color-primary)' }
}

// ── shared visual helpers (consolidation, S2/T2.2) ──
// The tinted icon-chip background was duplicated verbatim across NotificationsPage
// (Row) and NotificationBell (ShadeRow). Centralize the EXACT same value so the
// pattern has one home. `tone` is a kindMeta().tone (already a CSS var / color
// token), so this stays token-routed.
//
// The rail that used to live here as `unreadRail()` moved to `UnreadRail.tsx`: it had
// to stop being an inline box-shadow, which was silently suppressing both rows' focus
// ring. Same two consumers, same one home, a property that no longer collides.

/** Tinted background for a kind's icon chip (16% of the tone over transparent). */
export function toneChipBg(tone: string): string {
  return `color-mix(in srgb, ${tone} 16%, transparent)`
}

/** Distinct kinds present in a list, for the filter row. */
export function kindsPresent(items: NotificationItem[]): string[] {
  const seen = new Set<string>()
  for (const n of items) seen.add(n.kind || 'info')
  return [...seen]
}

// ── time bucketing for grouped display ──
export type Bucket = 'Today' | 'Yesterday' | 'Earlier'
export function bucketOf(iso: string, now: number): Bucket {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return 'Earlier'
  const startOfToday = new Date(now); startOfToday.setHours(0, 0, 0, 0)
  if (t >= startOfToday.getTime()) return 'Today'
  if (t >= startOfToday.getTime() - 86400_000) return 'Yesterday'
  return 'Earlier'
}
export const BUCKET_ORDER: Bucket[] = ['Today', 'Yesterday', 'Earlier']

export function relTime(iso: string, now: number): string {
  const t = Date.parse(iso); if (Number.isNaN(t)) return ''
  const s = Math.max(0, (now - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export function clockTime(iso: string): string {
  const t = Date.parse(iso); if (Number.isNaN(t)) return ''
  return new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function firstLine(body: string, max = 120): string {
  const line = (body || '').split('\n').find((l) => l.trim()) ?? ''
  return line.length > max ? line.slice(0, max) + '…' : line
}
