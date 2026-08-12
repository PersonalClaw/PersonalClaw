import { Reply, Info, BellOff, CheckCircle2, Send, XCircle, Inbox as InboxIcon, AlertTriangle, ShieldQuestion, Eye, MessageSquare, AtSign, Mail, HelpCircle, Lightbulb, Newspaper, Settings2 } from 'lucide-react'
import { epochSeconds } from '../../lib/epoch'
import type { LucideIcon } from 'lucide-react'
import type { InboxClassification, InboxConfidence, InboxItemStatus, InboxItemKind, InboxItem } from '../../lib/api'

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
  // Seen = surfaced but not resolved. Deliberately low-contrast: it is still open work,
  // but it is not new, so it should not compete with pending for attention.
  { key: 'seen', label: 'Seen', tone: 'var(--color-on-surface-low)', icon: Eye },
  { key: 'sent', label: 'Replied', tone: 'var(--color-ok)', icon: Send },
  { key: 'handled', label: 'Handled', tone: 'var(--color-ok)', icon: CheckCircle2 },
  { key: 'dismissed', label: 'Dismissed', tone: 'var(--color-on-surface-low)', icon: XCircle },
]
export function statusMeta(s?: string): StatusMeta {
  return STATUSES.find((x) => x.key === s) ?? STATUSES[0]
}

/** Statuses that still want the user: unresolved, whether or not already glanced at. */
export const OPEN_STATUSES: InboxItemStatus[] = ['pending', 'seen']
export function isOpen(s?: string): boolean {
  return OPEN_STATUSES.includes((s || 'pending') as InboxItemStatus)
}

// ── item kind (WHAT is asking for attention — orthogonal to classification) ──
// classification is the triage layer's judgment ABOUT a message; item_kind is what the
// row fundamentally IS. A needs_input row has no sender and no reply — treating it as a
// message would render dead controls.
export interface KindMeta { key: InboxItemKind; label: string; tone: string; icon: LucideIcon }
export const ITEM_KINDS: KindMeta[] = [
  { key: 'message', label: 'Messages', tone: 'var(--color-primary)', icon: MessageSquare },
  { key: 'mention', label: 'Mentions', tone: 'var(--color-primary)', icon: AtSign },
  { key: 'email', label: 'Email', tone: 'var(--color-primary)', icon: Mail },
  { key: 'needs_input', label: 'Needs you', tone: 'var(--color-warn)', icon: HelpCircle },
  { key: 'agent_request', label: 'Agent requests', tone: 'var(--color-warn)', icon: ShieldQuestion },
  { key: 'proposal', label: 'Proposals', tone: 'var(--color-info)', icon: Lightbulb },
  { key: 'digest', label: 'Digests', tone: 'var(--color-on-surface-low)', icon: Newspaper },
  { key: 'system', label: 'System', tone: 'var(--color-on-surface-low)', icon: Settings2 },
]
export function kindMeta(k?: string): KindMeta {
  return ITEM_KINDS.find((x) => x.key === (k || 'message')) ?? ITEM_KINDS[0]
}

/** Kinds with no channel behind them: no sender, no reply, no #channel label.
 *  Mirrors NON_CHANNEL_KINDS in inbox.py — rendering reply affordances for these
 *  would be dead controls. */
export const NON_CHANNEL_ITEM_KINDS: InboxItemKind[] = [
  'agent_request', 'proposal', 'needs_input', 'digest', 'system',
]

/** The router PATH an item's `refs` point at, or '' when it has nowhere to go.
 *  A bare path (no leading '#/') because callers hand it to RouteProps.navigate(), which
 *  owns hash-router mutation — pages must never assign location.hash themselves (there's a
 *  doctrine test).
 *  Loop kind decides the cockpit: a code loop lives at code/<id>, not loops/<id>. */
export function refTarget(it: Pick<InboxItem, 'refs'>): string {
  const refs = it.refs || {}
  if (refs.loop) return refs.loop_kind === 'code' ? `code/${refs.loop}` : `loops/${refs.loop}`
  if (refs.session) return `chat/${encodeURIComponent(refs.session)}`
  if (refs.workflow) return `workflows/${refs.workflow}`
  return ''
}

/** What the deep-link button says. Named after the REFERENT (the loop, the chat), not the
 *  item kind — "Go to needs you" is what you get from naively de-pluralizing a chip label. */
export function refLabel(it: Pick<InboxItem, 'refs'>): string {
  const refs = it.refs || {}
  if (refs.loop) return 'Go to loop'
  if (refs.session) return 'Go to chat'
  if (refs.workflow) return 'Go to workflow'
  return 'Go to source'
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

export function relPast(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return ''
  const s = Date.now() / 1000 - t
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
