import { Reply, Info, BellOff, CheckCircle2, Send, XCircle, Inbox as InboxIcon, AlertTriangle, ShieldQuestion, Eye, Filter, MessageSquare, AtSign, Mail, HelpCircle, Lightbulb, Newspaper, Settings2, StickyNote, UserCheck, TimerOff, CircleDashed, CircleAlert } from 'lucide-react'
import { epochSeconds } from '../../lib/epoch'
import { isOpenStatus } from '../../lib/attentionLanes'
import { approvalDestination } from '../../app/approvalDestination'
import { notificationLink } from '../notifications/notificationMeta'
import type { LucideIcon } from 'lucide-react'
import type { InboxClassification, InboxConfidence, InboxItemStatus, InboxItemKind, InboxItem } from '../../lib/api'

// ── classification (what KIND of message the triage layer decided) ──
export interface ClassMeta { key: InboxClassification; label: string; tone: string; icon: LucideIcon }
export const CLASSIFICATIONS: ClassMeta[] = [
  { key: 'needs_reply', label: 'Needs reply', tone: 'var(--color-info)', icon: Reply },
  { key: 'fyi', label: 'FYI', tone: 'var(--color-on-surface-low)', icon: Info },
  { key: 'noise', label: 'Noise', tone: 'var(--color-on-surface-low)', icon: BellOff },
]

/** What a message shows where its verdict goes: the verdict, or what stands in for one.
 *  `sorted` is false for the two stand-ins, which nobody can choose (Reclassify and the filters
 *  offer only CLASSIFICATIONS). A message waits for the background sorter until it has a verdict,
 *  and says so; one it could not sort says that instead. The verdict used to fall back to
 *  "Needs reply", so every message nobody had read showed it. */
export interface VerdictMeta { key: InboxClassification | 'unsorted' | 'sort_failed'; label: string; tone: string; icon: LucideIcon; sorted: boolean }
export const UNSORTED: VerdictMeta = { key: 'unsorted', label: 'Not sorted yet', tone: 'var(--color-on-surface-low)', icon: CircleDashed, sorted: false }
export const SORT_FAILED: VerdictMeta = { key: 'sort_failed', label: "Couldn't sort", tone: 'var(--color-warn)', icon: CircleAlert, sorted: false }
export function verdictMeta(it: Pick<InboxItem, 'classification' | 'classify_error'>): VerdictMeta {
  const made = CLASSIFICATIONS.find((x) => x.key === it.classification)
  if (made) return { ...made, sorted: true }
  return it.classify_error ? SORT_FAILED : UNSORTED
}

// ── confidence (how sure the triage layer is — drives review urgency) ──
export interface ConfMeta { key: InboxConfidence; label: string; tone: string; icon: LucideIcon }
export const CONFIDENCES: ConfMeta[] = [
  { key: 'high', label: 'High confidence', tone: 'var(--color-ok)', icon: CheckCircle2 },
  { key: 'needs_review', label: 'Needs review', tone: 'var(--color-warn)', icon: ShieldQuestion },
  { key: 'escalate', label: 'Escalate', tone: 'var(--color-danger)', icon: AlertTriangle },
  // A manual reclassification: the verdict is the user's own, so no machine confidence
  // applies. Neutral tone — a human decision needs no review urgency.
  { key: 'user', label: 'Set by you', tone: 'var(--color-info)', icon: UserCheck },
]
/** The confidence a verdict was given with, or null where it has none (a verdict nobody made, or
 *  one an agent gave with its post). It used to fall back to "Needs review". */
export function confMeta(c?: string): ConfMeta | null {
  return CONFIDENCES.find((x) => x.key === c) ?? null
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
  // Expired = it ended before anyone answered it (an approval out of time, or whose work stopped
  // first), and `refs.ended` says why. Not Handled: nobody decided anything.
  { key: 'expired', label: 'Expired', tone: 'var(--color-on-surface-low)', icon: TimerOff },
  // Filtered = withheld by the second-opinion pass: persisted but its notification
  // suppressed because a verification check refuted the claim. Warn-toned because it may be
  // a false positive the user will want to Restore — it is held for review, not resolved.
  { key: 'filtered', label: 'Filtered', tone: 'var(--color-warn)', icon: Filter },
]
export function statusMeta(s?: string): StatusMeta {
  return STATUSES.find((x) => x.key === s) ?? STATUSES[0]
}

/** What the second opinion says about a row that is not filtered, or '' for nothing.
 *  The check runs after the row is listed (`refs.verify` reads `checking` until it answers),
 *  and a verdict that lands on a row you had already opened or answered is written on the row
 *  instead of moving it. A filtered row explains itself with its Restore banner. */
export function verifyNote(item: Pick<InboxItem, 'status' | 'refs'>): string {
  if (item.status === 'filtered') return ''
  const verdict = item.refs?.verify
  if (verdict === 'checking') return 'A second opinion is checking this claim. Its notification waits for the answer.'
  if (verdict === 'refuted') return 'A second-opinion check flagged this claim.'
  return ''
}

/** Statuses that still want the user: unresolved, whether or not already glanced at.
 *
 *  RE-EXPORTED, not defined here. `lib/attentionLanes` owns the exhaustive
 *  `Record<InboxItemStatus, boolean>` these derive from, so this page's counts and Mission
 *  Control's lane counts read ONE set. Spelling the pair out here was the frontend's half of
 *  issue 493: the server published a PENDING-only `pending_count` for the header while this
 *  predicate drove every filter and chip, so one screen showed 33 and 37, and a glance — which
 *  marks a row SEEN — decremented the header without resolving anything. */
export { OPEN_STATUSES, isOpenStatus as isOpen } from '../../lib/attentionLanes'

/** Statuses of a row nothing is left to do about: handled, replied (`sent`, older rows),
 *  dismissed, or expired before anyone answered it.
 *
 *  Not simply "not open": a filtered row is held for review behind its Restore banner, not done.
 *  The Handled filter, its count and the detail panel's settled state all read this one set. */
export const SETTLED_STATUSES: readonly InboxItemStatus[] = ['handled', 'sent', 'dismissed', 'expired']
export function isSettled(status?: string): boolean {
  return (SETTLED_STATUSES as readonly string[]).includes(status ?? '')
}

/** Whether *item* is attributed to somebody OTHER than *owner*.
 *
 *  The ONE foreignness test, mirroring the server's `InboxItem.belongs_to` inverted, and it
 *  must keep mirroring it: an unattributed item is NOT foreign (it predates attribution, so
 *  it reads as the owner's), and nothing is foreign when the install has no username
 *  configured. A `!!item.owner_username` shortcut would mark every attributed row foreign,
 *  including the owner's own.
 *
 *  🪤 Lives HERE and not in `lib/api.ts` alongside the `InboxItem` type it takes. It is a pure
 *  predicate, so it belongs with `isOpen` — and putting it in `api.ts` broke six unrelated test
 *  files at once: those suites `vi.mock('../../lib/api')` with a partial object, so every NEW
 *  named export from that module is a missing-export error in each of them. A module that is
 *  routinely partially mocked is the wrong home for a helper. */
export function isForeignItem(item: Pick<InboxItem, 'owner_username'>, owner: string): boolean {
  const me = (owner || '').trim().toLowerCase()
  const who = (item.owner_username || '').trim().toLowerCase()
  return !!me && !!who && who !== me
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
  // The only kind the USER writes. `info`, matching `proposal`: both are open items
  // you will decide something about. NOT coral — coral in this registry is reserved for the
  // three channel-shaped kinds and means "a conversation" (`design/accentChipTone.test.tsx`
  // censuses exactly that, and it caught this row set to primary). NOT the low tone either:
  // a note you left yourself is something you meant to return to, not background chatter.
  { key: 'user_note', label: 'Notes', tone: 'var(--color-info)', icon: StickyNote },
]

/** The kind a row is, for every surface of this page: its own when this build knows it, `message`
 *  when it has none (the server's default for rows written before kinds existed), and `system` for
 *  a kind this build does not know. An unknown kind is a notice the system raised — it came through
 *  `emit_attention_item`, the only door that writes one — never a message someone sent; it read as
 *  "Messages" here, with a Reply arrow, a triage verdict and a draft box (an app-update notice once
 *  arrived as `update`). */
export function itemKindOf(it: Pick<InboxItem, 'item_kind'>): InboxItemKind {
  return kindMeta(it.item_kind).key
}

const SYSTEM_KIND = ITEM_KINDS.find((x) => x.key === 'system')!

export function kindMeta(k?: string | null): KindMeta {
  const key = k || 'message'
  return ITEM_KINDS.find((x) => x.key === key) ?? SYSTEM_KIND
}

/** The kinds with a channel behind them: a sender, reply routing, a draft, and the triage verdict
 *  (needs reply / FYI / noise) that only a channel message is ever judged by. Mirrors
 *  SOURCE_DECLARABLE_KINDS in inbox.py.
 *
 *  An ALLOWLIST, and it replaced a list of the kinds WITHOUT a channel: that list could only let a
 *  kind it did not name through as a message, which is how every app-update notice got the reply
 *  machinery and sat under "Needs reply" beside the user's own note. */
export const CHANNEL_ITEM_KINDS: readonly InboxItemKind[] = ['message', 'mention', 'email']

/** Whether a row has a channel behind it (`CHANNEL_ITEM_KINDS`). */
export function isChannelItem(it: Pick<InboxItem, 'item_kind'>): boolean {
  return CHANNEL_ITEM_KINDS.includes(itemKindOf(it))
}

/** Whether a row is open and its triage verdict is *classification*. Only a channel message is
 *  triaged: every other row carries the store's default verdict (`needs_reply`), which nobody made
 *  — so a note she wrote to herself and an app's update notice both read "Needs reply". */
export function isOpenWithVerdict(it: Pick<InboxItem, 'item_kind' | 'classification' | 'status'>, classification: string): boolean {
  return isChannelItem(it) && it.classification === classification && isOpenStatus(it.status)
}

/** The router PATH an item's `refs` point at, or '' when it has nowhere to go.
 *  A bare path (no leading '#/') because callers hand it to RouteProps.navigate(), which
 *  owns hash-router mutation — pages must never assign location.hash themselves (there's a
 *  doctrine test).
 *  Loop kind decides the cockpit: a code loop lives at code/<id>, not loops/<id>. */
export function refTarget(it: Pick<InboxItem, 'refs'>): string {
  const refs = it.refs || {}
  if (refs.loop) return refs.loop_kind === 'code' ? `code/${refs.loop}` : `loops/${refs.loop}`
  // A pending approval's row goes where the approval is ANSWERED, derived by the one parser of an
  // approval's session key — a workflow stage's `workflow:<run>:<node>` is not a chat, and
  // spelling `chat/<session>` for it is the 404 `approvalDestination` exists to prevent (#258).
  // The router path is its href without the leading `#/`, which `navigate` owns.
  // …and so does the note a call denied without an answer leaves: it names the same
  // session, and the place it happened is where the approval would have been answered. Work a
  // trigger started goes to that trigger instead: its session is the run's own helper, and the
  // trigger is what the owner knows it by and runs again.
  if (refs.auto_denied && typeof refs.trigger === 'string' && refs.trigger) {
    return `triggers?open=${encodeURIComponent(refs.trigger)}`
  }
  if ((refs.approval || refs.auto_denied) && typeof refs.session === 'string' && refs.session) {
    return approvalDestination(refs.session).href.replace(/^#\//, '')
  }
  if (refs.session) return `chat/${encodeURIComponent(refs.session)}`
  // A run opens at `workflows/runs/<id>`: `workflows/<id>` is no route of the Workflows section,
  // which read the id as nothing and showed its list.
  if (refs.workflow) return `workflows/runs/${encodeURIComponent(refs.workflow)}`
  // The identity report links the artifact it wrote. LAST in the chain, so every
  // pre-existing ref resolves exactly as before — an item that carries both a session and
  // an artifact still goes to the session, which is the referent it always went to.
  if (refs.artifact) return `artifacts/${encodeURIComponent(refs.artifact)}`
  // AGENT-ROOMS' pause item (`agent/room_paused`) stamps `refs.room` and nothing else, so this
  // is the row's only route. LAST in the chain like `artifact` above, so every pre-existing ref
  // resolves exactly as it did. It is the reason the pause card is not a second notice: the row
  // and the card are one event with one destination, and this is the link between them.
  if (refs.room) return `chat/room/${encodeURIComponent(refs.room)}`
  // The review item for triggers an upgrade brought over (`legacy_import.announce`) lists them in
  // `refs.triggers`; the Triggers page is where each one is opened, reviewed and switched on.
  if (Array.isArray(refs.triggers) && refs.triggers.length > 0) return 'triggers'
  // A trigger's failure filed in the Inbox carries its note's own link (`statusUrl`): the trigger,
  // or the run it started. Read by the one parser of that link, which takes an in-app route only.
  const status = statusLink(refs)
  if (status) return status.path
  return ''
}

/** The in-app place an item's `refs.statusUrl` names, or null (`notificationLink`). */
function statusLink(refs: Record<string, unknown>): { label: string; path: string } | null {
  return notificationLink({ statusUrl: typeof refs.statusUrl === 'string' ? refs.statusUrl : '' })
}

/** What the deep-link button says. Named after the REFERENT (the loop, the chat), not the
 *  item kind — "Go to needs you" is what you get from naively de-pluralizing a chip label. */
export function refLabel(it: Pick<InboxItem, 'refs'>): string {
  const refs = it.refs || {}
  if (refs.loop) return 'Go to loop'
  if (refs.auto_denied && typeof refs.trigger === 'string' && refs.trigger) return 'Open the trigger'
  if ((refs.approval || refs.auto_denied) && typeof refs.session === 'string' && refs.session) {
    return approvalDestination(refs.session).linkLabel
  }
  if (refs.session) return 'Go to chat'
  if (refs.workflow) return 'Open the workflow run'
  if (refs.artifact) return 'Open the report'
  if (refs.room) return 'Go to the room'
  if (Array.isArray(refs.triggers) && refs.triggers.length > 0) return 'Go to Triggers'
  return statusLink(refs)?.label ?? 'Go to source'
}

// Direct-message labels ("DM", "@name") render as-is; anything else renders as a
// #channel. Items use whatever channel_name the source provider gave — provider-neutral.
export function channelLabel(it: Pick<InboxItem, 'channel' | 'channel_name'>): string {
  const n = it.channel_name || it.channel
  if (!n) return ''
  return n === 'DM' || n.startsWith('@') ? n : `#${n.replace(/^#/, '')}`
}

/** Short label for the source provider that produced an item (agent-native vs a
 *  connected source's provider id). A row a channel held for you (someone new) is
 *  `channel:<its name>`, and reads as that channel. */
export function sourceLabel(source?: string): string {
  if (!source || source === 'native') return 'agent'
  return source.startsWith('channel:') ? source.slice('channel:'.length) : source
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
