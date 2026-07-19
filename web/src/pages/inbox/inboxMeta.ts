import { Reply, Info, BellOff, CheckCircle2, Send, XCircle, Inbox as InboxIcon, AlertTriangle, ShieldQuestion } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { InboxClassification, InboxConfidence, InboxItemStatus, InboxItem } from '../../lib/api'

// ── classification (what KIND of message the triage layer decided) ──
export interface ClassMeta { key: InboxClassification; label: string; tone: string; icon: LucideIcon }
export const CLASSIFICATIONS: ClassMeta[] = [
  { key: 'needs_reply', label: 'Needs reply', tone: 'var(--color-info)', icon: Reply },
  { key: 'fyi', label: 'FYI', tone: 'var(--color-on-surface-low)', icon: Info },
  { key: 'noise', label: 'Noise', tone: 'var(--color-on-surface-low)', icon: BellOff },
]
export function classMeta(c?: string): ClassMeta {
  return CLASSIFICATIONS.find((x) => x.key === c) ?? CLASSIFICATIONS[0]
}

// ── confidence (how sure the triage layer is — drives review urgency) ──
export interface ConfMeta { key: InboxConfidence; label: string; tone: string; icon: LucideIcon }
export const CONFIDENCES: ConfMeta[] = [
  { key: 'high', label: 'High confidence', tone: 'var(--color-ok)', icon: CheckCircle2 },
  { key: 'needs_review', label: 'Needs review', tone: 'var(--color-warn)', icon: ShieldQuestion },
  { key: 'escalate', label: 'Escalate', tone: 'var(--color-danger)', icon: AlertTriangle },
]
export function confMeta(c?: string): ConfMeta {
  return CONFIDENCES.find((x) => x.key === c) ?? CONFIDENCES[1]
}

// ── item status ──
export interface StatusMeta { key: InboxItemStatus; label: string; tone: string; icon: LucideIcon }
export const STATUSES: StatusMeta[] = [
  { key: 'pending', label: 'Pending', tone: 'var(--color-info)', icon: InboxIcon },
  { key: 'sent', label: 'Replied', tone: 'var(--color-ok)', icon: Send },
  { key: 'handled', label: 'Handled', tone: 'var(--color-ok)', icon: CheckCircle2 },
  { key: 'dismissed', label: 'Dismissed', tone: 'var(--color-on-surface-low)', icon: XCircle },
]
export function statusMeta(s?: string): StatusMeta {
  return STATUSES.find((x) => x.key === s) ?? STATUSES[0]
}

// Direct-message labels ("DM", "@name") render as-is; anything else renders as a
// #channel. Items use whatever channel_name the source provider gave — provider-neutral.
export function channelLabel(it: Pick<InboxItem, 'channel' | 'channel_name'>): string {
  const n = it.channel_name || it.channel
  if (!n) return ''
  return n === 'DM' || n.startsWith('@') ? n : `#${n.replace(/^#/, '')}`
}

/** Short label for the source provider that produced an item (agent-native vs a
 *  connected source's provider id). */
export function sourceLabel(source?: string): string {
  if (!source || source === 'native') return 'agent'
  return source
}

export function relPast(ts?: number | null): string {
  if (!ts) return ''
  const s = Date.now() / 1000 - ts
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
