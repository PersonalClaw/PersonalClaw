import { Bell, BellRing, CheckCircle2, Clock, Webhook, Bot, HeartPulse, Info, AlertTriangle, Target } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { NotificationItem } from '../../lib/api'

// ── kind → icon + tone (the `kind`s the backend actually emits) ──
export interface KindMeta { label: string; icon: LucideIcon; tone: string }
const KINDS: Record<string, KindMeta> = {
  cron: { label: 'Schedule', icon: Clock, tone: 'var(--color-info)' },
  schedule: { label: 'Schedule', icon: Clock, tone: 'var(--color-info)' },
  hook: { label: 'Trigger', icon: Webhook, tone: 'var(--color-primary)' },
  agent: { label: 'Agent', icon: Bot, tone: 'var(--color-primary)' },
  subagent: { label: 'Subagent', icon: Bot, tone: 'var(--color-primary)' },
  heartbeat: { label: 'Heartbeat', icon: HeartPulse, tone: 'var(--color-info)' },
  inbox_alert: { label: 'Inbox Alert', icon: BellRing, tone: 'var(--color-warn)' },
  loop: { label: 'Goal Loop', icon: Target, tone: 'var(--color-primary)' },
  success: { label: 'Success', icon: CheckCircle2, tone: 'var(--color-ok)' },
  warning: { label: 'Warning', icon: AlertTriangle, tone: 'var(--color-warn)' },
  error: { label: 'Error', icon: AlertTriangle, tone: 'var(--color-danger)' },
  info: { label: 'Info', icon: Info, tone: 'var(--color-on-surface-low)' },
}
export function kindMeta(kind: string): KindMeta {
  return KINDS[kind] ?? { label: kind || 'Notification', icon: Bell, tone: 'var(--color-primary)' }
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
