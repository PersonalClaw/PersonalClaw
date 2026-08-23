/** AS-8 — which attention lane an item belongs to, as a pure function over the wire types.
 *
 *  Mission Control renders four lanes (Needs-approval / Your-turn / Working / Idle). This module
 *  owns the RULE; the component owns the pixels. Split out deliberately: lane assignment is the part
 *  most likely to be subtly wrong, and a wrong lane is invisible in a screenshot — everything still
 *  renders, just in the wrong column, with counts that read as authoritative.
 *
 *  ── THE THREE MEASURED FACTS THIS RULE RESTS ON ────────────────────────────────────────────────
 *
 *  1. **`inbox.py` already partitions the kinds; this file does not invent a third partition.**
 *     `ItemKind` (inbox.py:69) is exactly eight members. Two frozensets sit right below it:
 *     `NON_CHANNEL_KINDS` = {agent_request, proposal, needs_input, digest, system} — core's own
 *     attention vocabulary, raised only through `emit_attention_item` with the `refs` that make a row
 *     actionable — and `SOURCE_DECLARABLE_KINDS` = {message, mention, email}, the channel-shaped
 *     kinds a message source may claim. `BASE_LANE` below is keyed off that same split: the three
 *     channel-shaped kinds map to `null`, the five attention kinds map to a lane.
 *
 *  2. **A pending approval already appears TWICE on the wire, so a naive concat double-counts it.**
 *     `chat_runner._mirror_approval_to_inbox()` raises an `agent_request` item carrying
 *     `refs = {session, approval: <request_id>}` for any approval that outlives its prompt — and
 *     `PendingApproval.id` IS that `request_id`. So one blocked decision is one row in
 *     `GET /api/approvals` and a second row in `GET /api/inbox`. `toLanes` suppresses the mirror
 *     when its `refs.approval` matches an approval in the same snapshot. This is not a hypothetical
 *     precedence puzzle; it is the shipped behaviour of the two endpoints Mission Control reads.
 *
 *  3. **🔴 "Working" is NOT derivable from the attention store. Nothing in the inbox says
 *     "in flight".** `ItemStatus`'s own docstring (inbox.py:49) declares the lifecycle as
 *     `PENDING → SEEN → HANDLED | DISMISSED` — two open states, two closed ones, and no in-flight
 *     one. The nearest candidate, `SENT`, measures as an **enum member nobody writes**: a census of
 *     `src/personalclaw/` finds `ItemStatus.SENT` assigned at zero sites (the only hits are its own
 *     declaration and the comment conceding it "predates the others"). Keying the Working lane off
 *     `sent` would have produced a permanently empty lane wearing a confident label.
 *     `PendingApproval` carries no status at all — being in the list IS pending.
 *
 *     So Working is fed from a THIRD source that actually observes running work: `ChatSession`
 *     (`GET /api/chat/sessions`), whose `running`/`stopping` booleans are real evidence. It is an
 *     optional argument, so `toLanes(items, approvals)` stays valid; pass nothing and Working is
 *     honestly empty rather than filled by a timestamp heuristic. **`laneFor` never returns
 *     `'working'`** — no single inbox item can prove it, and `laneFor` only sees inbox items.
 *
 *  ── THE TWO `null`s, AND WHY UNKNOWN IS NOT `idle` ─────────────────────────────────────────────
 *
 *  `laneFor` returns `null` for "not on this surface", which covers three distinct cases: a
 *  channel-shaped kind (a different concern — those belong to the inbox proper, with reply
 *  machinery), a closed item, and an unrecognised kind. **An unrecognised kind must not land in
 *  `idle`.** `idle` is a real lane that reads to a user as "nothing is needed from you"; quietly
 *  routing a kind this build has never heard of into it would state the one thing we cannot know.
 *  Use `isKnownKind()` to tell the unknown `null` from the deliberate ones.
 *
 *  🪤 The two unknowns fail in OPPOSITE directions, on purpose. An unknown **kind** fails closed
 *  (off-surface): we cannot say what it wants, so we refuse to claim. An unknown or missing
 *  **status** fails open (still asking): the question is only "is this resolved?", and an unresolved
 *  item wrongly hidden is a worse failure on an attention surface than a resolved item wrongly
 *  shown. Neither is a fallthrough — both branches are written out.
 */
import type { ChatSession, InboxItem, InboxItemKind, InboxItemStatus, PendingApproval } from './api'

export const LANES = ['needs-approval', 'your-turn', 'working', 'idle'] as const
export type Lane = (typeof LANES)[number]

/** The fields of a wire `InboxItem` this derivation actually reads. `Pick<>` rather than a
 *  hand-rolled interface so renaming or retyping a field in `api.ts` breaks compilation HERE
 *  instead of silently mis-laning every row at runtime. */
export type AttentionInput = Pick<
  InboxItem,
  'id' | 'item_kind' | 'status' | 'created_at' | 'ts' | 'message' | 'context_summary' | 'sender_name' | 'channel_name' | 'refs'
>

/** `GET /api/approvals` rows. No status field exists on `PendingApproval` — presence in the list is
 *  the pending-ness — so every one of these is a Needs-approval card. */
export type ApprovalInput = Pick<PendingApproval, 'id' | 'source' | 'tool' | 'tool_purpose' | 'session' | 'ts'>

/** The only in-flight evidence on the wire (see fact 3). `running`/`stopping` are observed, not
 *  inferred. Optional: omit it and Working is empty rather than guessed. */
export type ActivityInput = Pick<ChatSession, 'key' | 'title' | 'running' | 'stopping' | 'pending_approval'>

/** One card, normalised across the three sources so a lane renders uniformly.
 *  `at` is epoch **seconds** — the unit both `InboxItem.created_at` and `PendingApproval.ts` arrive
 *  in (`time.time()` on the backend) — or `null` when the source carried no timestamp at all. */
export interface LaneCard {
  /** Unique across all three sources: `${origin}:${id}`. Two sources can mint the same id. */
  key: string
  lane: Lane
  origin: 'approval' | 'inbox' | 'session'
  id: string
  title: string
  subtitle?: string
  at: number | null
  refs?: Record<string, unknown>
}

/** Base kind → lane, mirroring `inbox.py`'s two frozensets (fact 1).
 *
 *  Typed as an EXHAUSTIVE `Record<InboxItemKind, …>` on purpose: adding a member to the
 *  `InboxItemKind` union in `api.ts` fails this object literal to compile, which forces whoever
 *  widens the wire vocabulary to make the lane decision here rather than inherit a default.
 *  `agent_request` is the one entry a per-item condition can upgrade — see `laneFor`. */
const BASE_LANE: Record<InboxItemKind, Lane | null> = {
  // SOURCE_DECLARABLE_KINDS — channel-shaped. A conversation is not an attention lane: it has a
  // draft, reply routing and a send affordance, all of which Mission Control deliberately lacks.
  message: null,
  mention: null,
  email: null,
  // NON_CHANNEL_KINDS — core's own attention vocabulary. These three are asking you something.
  agent_request: 'your-turn',
  proposal: 'your-turn',
  needs_input: 'your-turn',
  // …and these two are telling you something. Nothing is blocked on you, so: Idle.
  digest: 'idle',
  system: 'idle',
}

/** Which statuses still want something. Exhaustive over `InboxItemStatus` for the same reason
 *  `BASE_LANE` is exhaustive over `InboxItemKind`.
 *
 *  `filtered` is off-surface but NOT resolved — inbox.py:66 calls it "withheld by verification
 *  (INU-6); restorable to PENDING". Mission Control showing a row the verifier withheld would
 *  undo that decision, so it reads as closed here.
 *  `sent` is the dead member from fact 3; the branch is defensive, not load-bearing. */
const STATUS_OPEN: Record<InboxItemStatus, boolean> = {
  pending: true,
  seen: true, // the read/unread boundary — surfaced, not yet resolved. Still your turn.
  sent: false,
  handled: false,
  dismissed: false,
  filtered: false,
}

/** The eight kinds this build knows. Exported so a caller can distinguish "off-surface by decision"
 *  from "off-surface because we have never heard of this kind" — the second is worth counting. */
export const KNOWN_KINDS = Object.keys(BASE_LANE) as InboxItemKind[]

export function isKnownKind(kind: unknown): kind is InboxItemKind {
  return typeof kind === 'string' && Object.prototype.hasOwnProperty.call(BASE_LANE, kind)
}

/** Lanes that rank OLDEST first, because they are the ones asking you something and the oldest
 *  unanswered question is the most overdue. An attention surface where the thing that has waited
 *  longest sinks to the bottom hides exactly what it exists to show. Working and Idle rank NEWEST
 *  first: nothing there is overdue, and the interesting row is the latest one. */
const OLDEST_FIRST: ReadonlySet<Lane> = new Set<Lane>(['needs-approval', 'your-turn'])

/** The timestamp a row sorts by, in epoch seconds.
 *
 *  🪤 `created_at` is `number | undefined` while `ts` is a `string` — inbox.py mints ids as
 *  `{kind}_{uuid8}_{ts}` and `InboxItem.ts` is the rsplit tail of that id, so it is a stringified
 *  float. Both are optional on the wire; `null` means "no evidence of age" and sorts LAST in either
 *  direction, so an undated row can never squat the top of an oldest-first lane. */
function timeOf(item: AttentionInput): number | null {
  if (typeof item.created_at === 'number' && Number.isFinite(item.created_at)) return item.created_at
  if (typeof item.ts === 'string' && item.ts.trim() !== '') {
    const parsed = Number.parseFloat(item.ts)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

/** True when this item is the inbox MIRROR of a tool approval (fact 2), rather than an agent's own
 *  question. `refs.approval` holds the approval's `request_id`, which is `PendingApproval.id`. */
function mirroredApprovalId(item: AttentionInput): string {
  const raw = item.refs?.approval
  return typeof raw === 'string' && raw !== '' ? raw : ''
}

/** Which lane one inbox item belongs to, or `null` for "not on this surface".
 *
 *  PRECEDENCE — `needs-approval` > `your-turn` > `working` > `idle`, applied strictly so an item
 *  that qualifies twice lands once. Only one item can qualify twice: an `agent_request` carrying
 *  `refs.approval` is both an unresolved question (your-turn) and a blocked tool decision
 *  (needs-approval). Needs-approval wins because it is the narrower and more consequential claim —
 *  a tool is halted mid-run waiting on an approve/reject, and the user's next action is a decision
 *  with a side effect, not a reply. Demoting it to your-turn would bury a halted run among
 *  ordinary questions.
 *
 *  Never returns `'working'`: no single inbox item carries evidence that anything is running
 *  (fact 3). That is a measurement, not an omission.
 */
export function laneFor(item: AttentionInput): Lane | null {
  // Render path: a malformed row must not take the surface down with it.
  if (item === null || typeof item !== 'object') return null

  // Kind is optional on the wire and defaults to `message` on the backend (inbox.py:71-73: "MESSAGE
  // is the default so every item written before this existed stays valid"). Honour that default
  // rather than treating absence as unknown — those legacy rows really are channel messages.
  const kind = item.item_kind === undefined || item.item_kind === null ? 'message' : item.item_kind

  // Unrecognised kind → off-surface. NOT `idle`: see the header. A kind this build cannot name
  // cannot be truthfully claimed to need nothing.
  if (!isKnownKind(kind)) return null

  // Closed items leave the surface. An unknown or missing status fails OPEN (still asking) — the
  // opposite direction from an unknown kind, argued in the header.
  const status = item.status
  if (typeof status === 'string' && Object.prototype.hasOwnProperty.call(STATUS_OPEN, status)) {
    if (!STATUS_OPEN[status as InboxItemStatus]) return null
  }

  const base = BASE_LANE[kind]
  if (base === null) return null

  // The one precedence upgrade (fact 2).
  if (kind === 'agent_request' && mirroredApprovalId(item) !== '') return 'needs-approval'
  return base
}

function firstLine(text: unknown): string {
  if (typeof text !== 'string') return ''
  const trimmed = text.trim()
  if (trimmed === '') return ''
  const nl = trimmed.indexOf('\n')
  return nl === -1 ? trimmed : trimmed.slice(0, nl)
}

/** Sort one lane in place. Nulls last in both directions; `key` breaks ties so the order is a total
 *  one and a test cannot pass on incidental sort stability. */
function sortLane(lane: Lane, cards: LaneCard[]): LaneCard[] {
  const ascending = OLDEST_FIRST.has(lane)
  return cards.sort((a, b) => {
    if (a.at === null && b.at === null) return a.key < b.key ? -1 : a.key > b.key ? 1 : 0
    if (a.at === null) return 1
    if (b.at === null) return -1
    if (a.at !== b.at) return ascending ? a.at - b.at : b.at - a.at
    return a.key < b.key ? -1 : a.key > b.key ? 1 : 0
  })
}

function emptyLanes(): Record<Lane, LaneCard[]> {
  // Built from LANES so all four keys always exist. A component that indexes a lane the derivation
  // forgot to emit crashes the whole surface; `Object.keys(toLanes(…))` is always these four.
  const out = {} as Record<Lane, LaneCard[]>
  for (const lane of LANES) out[lane] = []
  return out
}

/** Every attention source, folded into the four lanes, each item appearing exactly ONCE.
 *
 *  `activity` is optional so the fixed two-argument call stays valid; it is the only input that can
 *  populate Working (fact 3). Every argument is defended as if it came straight off a failed fetch:
 *  a non-array, a `null` element or a non-object element is skipped, never thrown on. This runs on
 *  a render path, where one exception blanks the entire surface instead of one card.
 */
export function toLanes(
  items: AttentionInput[],
  approvals: ApprovalInput[],
  activity: ActivityInput[] = [],
): Record<Lane, LaneCard[]> {
  const out = emptyLanes()

  // ── Approvals: the authoritative form of a blocked decision, so they go in first and own the id.
  const approvalIds = new Set<string>()
  for (const a of Array.isArray(approvals) ? approvals : []) {
    if (a === null || typeof a !== 'object') continue
    const id = typeof a.id === 'string' ? a.id : ''
    if (id === '') continue
    approvalIds.add(id)
    out['needs-approval'].push({
      key: `approval:${id}`,
      lane: 'needs-approval',
      origin: 'approval',
      id,
      title: firstLine(a.tool) || 'a tool',
      subtitle: firstLine(a.tool_purpose) || firstLine(a.session) || undefined,
      at: typeof a.ts === 'number' && Number.isFinite(a.ts) ? a.ts : null,
    })
  }

  // ── Inbox items.
  for (const item of Array.isArray(items) ? items : []) {
    if (item === null || typeof item !== 'object') continue
    const lane = laneFor(item)
    if (lane === null) continue
    const id = typeof item.id === 'string' ? item.id : ''
    if (id === '') continue

    // Fact 2's dedup. Suppressed only when the approval is in THIS snapshot: if the approval has
    // already been answered while the mirror row is still open, the item keeps its own card rather
    // than vanishing — losing a row is worse than showing a stale one on an attention surface.
    const mirrored = mirroredApprovalId(item)
    if (mirrored !== '' && approvalIds.has(mirrored)) continue

    out[lane].push({
      key: `inbox:${id}`,
      lane,
      origin: 'inbox',
      id,
      title: firstLine(item.message) || firstLine(item.context_summary) || '(no message)',
      subtitle: firstLine(item.sender_name) || firstLine(item.channel_name) || undefined,
      at: timeOf(item),
      refs: item.refs,
    })
  }

  // ── Running work. `pending_approval` sessions are deliberately NOT mirrored into Needs-approval:
  // `GET /api/approvals` already carries that row with the `tool_input` needed to decide, and a
  // boolean cannot say WHICH tool is waiting. A session that is neither running nor stopping
  // contributes nothing — an idle session is not an attention item, and Idle is fed only by the
  // informational inbox kinds.
  for (const s of Array.isArray(activity) ? activity : []) {
    if (s === null || typeof s !== 'object') continue
    const key = typeof s.key === 'string' ? s.key : ''
    if (key === '') continue
    if (s.running !== true && s.stopping !== true) continue
    out['working'].push({
      key: `session:${key}`,
      lane: 'working',
      origin: 'session',
      id: key,
      title: firstLine(s.title) || key,
      // `stopping` still counts as working: it is winding down, not idle.
      subtitle: s.stopping === true ? 'stopping' : 'running',
      at: null,
    })
  }

  for (const lane of LANES) sortLane(lane, out[lane])
  return out
}
