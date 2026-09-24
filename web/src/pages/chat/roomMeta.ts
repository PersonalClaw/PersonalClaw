/** Agent Rooms — the pure derivations the room surface renders from (`AGENT-ROOMS` AR-8).
 *
 *  Everything here is a function of the wire payload and nothing else: no fetching, no React,
 *  no clock. That is deliberate rather than tidy. The whole difficulty of a room UI is
 *  ATTRIBUTION — whose words these are, which member is owed a turn, which one cannot speak —
 *  and every one of those answers is a claim about data the backend published. Keeping them
 *  pure is what lets each be pinned by a test that feeds a payload and reads a verdict,
 *  instead of being asserted against a rendered pixel.
 *
 *  🔑 THE ONE RULE THIS MODULE EXISTS TO HOLD: **absent is UNKNOWN, never healthy.** A member
 *  with no posture row and no binding row reports `unknown`, not `ok`. A member whose binding was
 *  deleted from `config.json` reports `unconfigured`, not the default agent's model. Nothing here
 *  substitutes a plausible value for a missing one.
 */
import { AtSign, Bot, Clock, Ear, Eye, HelpCircle, ShieldAlert, Unplug } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type {
  RoomDetail,
  RoomListenPolicy,
  RoomMemberBinding,
  RoomMemberPosture,
  RoomMemberRecord,
  RoomMessage,
  RoomRecord,
} from '../../lib/api'
import type { StatusPillTone } from '../../ui/StatusPill'

// ── who said this line ──────────────────────────────────────────────────────

/** What a transcript line IS, which is not the same question as who wrote it.
 *
 *  `room` is the third voice and the reason this is a union rather than a boolean. A line the
 *  ROOM wrote — today a tool refusal — carries the refused MEMBER's name as its `speaker`,
 *  because that is the attribution a reader needs. Keying off the speaker alone would render
 *  the room's own words as that member's position, which is the exact confusion the whole
 *  surface exists to prevent. The backend distinguishes them by `role`, so so does this. */
export type RoomLineKind = 'human' | 'member' | 'room'

export interface RoomLine {
  kind: RoomLineKind
  /** The member this line is ABOUT — the author for `member`, the subject for `room`, `''`
   *  for the human. */
  speaker: string
  /** What to render as the attribution, already resolved against the roster. */
  label: string
  /** The member's role blurb, when it has one — its position is only legible next to the
   *  role it argues from. Empty for the human and for a room note. */
  blurb: string
  content: string
  ts: string
}

/** The `role` the backend writes for a line the room itself authored
 *  (`rooms.store.ROOM_NOTE_ROLE`). Mirrored rather than inferred: a note is distinguished by
 *  its role precisely because a speaker sentinel could collide with a legal agent name. */
const ROOM_NOTE_ROLE = 'system'

/** The human's `speaker` value (`rooms.store.HUMAN_SPEAKER`). */
const HUMAN_SPEAKER = ''

/** One transcript message, resolved into something renderable.
 *
 *  A member that has since LEFT the room still has its lines in the transcript — they were
 *  said — so an unknown speaker resolves to its bare name rather than being dropped or
 *  re-attributed. Deleting history because the roster changed would rewrite what happened.
 */
export function roomLine(room: RoomRecord, msg: RoomMessage): RoomLine {
  const speaker = msg.speaker ?? HUMAN_SPEAKER
  const content = String(msg.content ?? '')
  const ts = String(msg.ts ?? '')
  if (String(msg.role ?? '') === ROOM_NOTE_ROLE) {
    return { kind: 'room', speaker, label: 'Room', blurb: '', content, ts }
  }
  if (speaker === HUMAN_SPEAKER) {
    return { kind: 'human', speaker, label: 'You', blurb: '', content, ts }
  }
  const member = room.members.find((m) => m.name === speaker)
  return { kind: 'member', speaker, label: speaker, blurb: member?.role_blurb ?? '', content, ts }
}

/** The whole transcript as attributed lines, blank content dropped.
 *
 *  🔑 ONE LINE PER MESSAGE, AND NO MERGING. The session transcript collapses consecutive
 *  assistant messages into one turn (`hydrateTurns`); in a room, consecutive assistant
 *  messages are usually DIFFERENT members, so that collapse would print one member's words
 *  under another's name. The fix is not to make the merge speaker-aware — it is that a room
 *  has no merge at all, because a room's messages are authored turns and never fragments of
 *  one. AGENT-ROOMS names this as the likeliest room-specific UI defect; this is the shape
 *  that cannot have it, and `roomMeta.test.ts` pins it. */
export function roomLines(detail: RoomDetail): RoomLine[] {
  return detail.messages
    .filter((m) => String(m.content ?? '').trim() !== '')
    .map((m) => roomLine(detail.room, m))
}

// ── listen policy ───────────────────────────────────────────────────────────

export interface ListenPolicyMeta {
  key: RoomListenPolicy
  label: string
  /** What the policy MEANS, in the second person. Shown on the member row, because "mention"
   *  alone does not tell a reader that peers can summon this member and nobody else can. */
  hint: string
  icon: LucideIcon
}

/** The three policies, in the order the arbiter's reach widens: an observer answers only its
 *  human, a mention-policy member can also be summoned by a peer, an all-policy member speaks
 *  on every human message. Ordered rather than alphabetical so a picker reads as a ladder. */
export const LISTEN_POLICIES: ListenPolicyMeta[] = [
  {
    key: 'all',
    label: 'Everything',
    hint: 'Speaks on every message you send. Peers cannot summon it.',
    icon: Ear,
  },
  {
    key: 'mention',
    label: 'When named',
    hint: 'Speaks only when you or another member writes @its-name.',
    icon: AtSign,
  },
  {
    key: 'silent',
    label: 'Observer',
    hint: 'Reads everything and speaks only when you name it. Peers cannot summon it.',
    icon: Eye,
  },
]

export function listenPolicyMeta(policy?: string): ListenPolicyMeta {
  return LISTEN_POLICIES.find((p) => p.key === policy) ?? LISTEN_POLICIES[0]
}

// ── per-member status ───────────────────────────────────────────────────────

/** What a member's row reports about that member RIGHT NOW.
 *
 *  Status only — the listen policy is NOT in here, and that separation is the point. A policy is
 *  something the user chose and it belongs in the row's labelled facts beside the model and the
 *  tool reach; a status is something that happens to the member. Folding the two into one badge
 *  is what printed the policy twice on every healthy row, and it also made a pill that says
 *  "nothing is wrong" — noise competing with the three that mean something.
 *
 *  So `ok` renders NO badge, and `unknown` is the fallback rather than `ok`. A member with no
 *  posture row and no binding row is a member nothing is known about; saying so is the honest
 *  answer, and reading it as `ok` would present a member that may be unable to speak as fine. */
export type RoomMemberState = 'refused' | 'unconfigured' | 'owed' | 'unknown' | 'ok'

export interface MemberStateMeta {
  label: string
  tone: StatusPillTone
  icon: LucideIcon
}

/** Every state that earns a badge. `ok` is deliberately absent — see the union's own note. */
const MEMBER_STATE_META: Record<Exclude<RoomMemberState, 'ok'>, MemberStateMeta> = {
  refused: { label: 'Cannot speak', tone: 'danger', icon: ShieldAlert },
  unconfigured: { label: 'Binding missing', tone: 'warn', icon: Unplug },
  owed: { label: 'Owed a turn', tone: 'info', icon: Clock },
  // Neutral, not warn: nothing is known to be WRONG — something is simply not known. A warn tone
  // would assert a fault the read cannot support.
  unknown: { label: 'Status unknown', tone: 'neutral', icon: HelpCircle },
}

/** The badge for *state*, or `null` when the member has nothing to report. */
export function memberStateMeta(state: RoomMemberState): MemberStateMeta | null {
  return state === 'ok' ? null : MEMBER_STATE_META[state]
}

/** One member, everything a row needs, with the two resolutions joined onto the record. */
export interface RoomMemberView {
  member: RoomMemberRecord
  posture?: RoomMemberPosture
  binding?: RoomMemberBinding
  state: RoomMemberState
  /** True when this member is in the queue the room still owes — parked by a pause, or
   *  queued by the message just sent. It is a separate flag as well as a possible `state`
   *  because a member can be both owed a turn and unable to take it. */
  owed: boolean
  /** Its position in that queue, 1-based, or 0 when it is not in one. FIFO order is the
   *  product fact here: the parked members speak BEFORE what the new message asks for. */
  queuePosition: number
}

/** Join a room's roster against its resolved posture, its bindings and the queue it owes.
 *
 *  *owedNames* is the queue, in order. The caller supplies it rather than this function
 *  reading `room.pending_queue`, because there are two sources and they are both real: the
 *  PARKED queue a pause persisted, and the in-flight queue the last human message produced
 *  (`POST /api/rooms/{id}/messages` → `speaking`). Only the caller knows whether a round is
 *  still running, so only the caller can say which one applies.
 *
 *  Status precedence, and each step is a decision: a refused posture beats a missing binding
 *  (both stop the member, and the refusal is the one with a reason the user can act on), both
 *  beat "nothing is known" (a stated reason beats an absence), and all three beat "owed a turn"
 *  — a queue position is meaningless for a member that cannot take one. */
export function roomMemberViews(
  detail: RoomDetail,
  owedNames: readonly string[] = [],
): RoomMemberView[] {
  const postures = new Map(detail.member_posture.map((p) => [p.name, p]))
  const bindings = new Map(detail.member_bindings.map((b) => [b.name, b]))
  const queue = owedNames.filter((n) => detail.room.members.some((m) => m.name === n))
  return detail.room.members.map((member) => {
    const posture = postures.get(member.name)
    const binding = bindings.get(member.name)
    const position = queue.indexOf(member.name)
    const owed = position >= 0
    return {
      member,
      posture,
      binding,
      owed,
      queuePosition: owed ? position + 1 : 0,
      state: memberState(posture, binding, owed),
    }
  })
}

function memberState(
  posture: RoomMemberPosture | undefined,
  binding: RoomMemberBinding | undefined,
  owed: boolean,
): RoomMemberState {
  if (posture?.refused) return 'refused'
  if (binding && !binding.configured) return 'unconfigured'
  // Nothing is known about this member: no resolved posture AND no binding answer. Not `ok` —
  // see the union's own note.
  if (!posture && !binding) return 'unknown'
  return owed ? 'owed' : 'ok'
}

/** How a member's model reads on its row, or `''` when there is nothing truthful to say.
 *
 *  The three answers are genuinely different and are not collapsed: a binding that is GONE
 *  reports so (its model is unknown, and reporting the default's would be a fabrication), a
 *  binding that names NO model runs on the install's configured default for its use case
 *  (which is a real and correct state, not a gap), and a binding that names one reports it. */
export function memberModelLabel(binding?: RoomMemberBinding): string {
  if (!binding) return ''
  if (!binding.configured) return 'No agent by this name is configured'
  if (!binding.model) return 'Default model'
  return binding.model
}

/** The member's runtime, as a short chip, or `''` when the binding inherits the global default.
 *
 *  `acp:<cli>` is rendered as the CLI's own name because that is what the user picked in the
 *  Agents UI; `native` is spelled out rather than left blank so "in-process" and "inherits the
 *  default" stay distinguishable. */
export function memberRuntimeLabel(binding?: RoomMemberBinding): string {
  const provider = binding?.provider ?? ''
  if (!binding?.configured || !provider) return ''
  if (provider === 'native') return 'Native'
  return provider.startsWith('acp:') ? provider.slice('acp:'.length) : provider
}

/** The member's own tool reach, as a short phrase, or `''` when it has no resolved posture.
 *
 *  A read-only critic and a tool-bearing executor sharing one room is the feature; this is the
 *  one string that says which is which. `custom` is reported with its allowlist SIZE rather
 *  than the list, because the row has no space for a list and the count is what distinguishes
 *  "custom, and it may use two tools" from "custom, and it may use none". */
export function memberReachLabel(posture?: RoomMemberPosture): string {
  if (!posture || posture.refused) return ''
  const tier = posture.tool_grants ?? ''
  if (!tier) return ''
  if (tier === 'custom') {
    const n = posture.tool_allowlist?.length ?? 0
    return n === 0 ? 'No tools' : `${n} allowed tool${n === 1 ? '' : 's'}`
  }
  if (tier === 'read') return 'Read-only tools'
  if (tier === 'read_write') return 'Read and write tools'
  if (tier === 'none') return 'No tools'
  return tier
}

/** The member's own spend ceiling, or `''` when it declares none.
 *
 *  Zero in a dimension means "no ceiling of my own" (`Budget`'s convention), so it is omitted
 *  rather than rendered as a ceiling of nothing. */
export function memberBudgetLabel(posture?: RoomMemberPosture): string {
  const budget = posture?.budget
  if (!budget) return ''
  const parts: string[] = []
  if (budget.max_tokens > 0) parts.push(`${budget.max_tokens.toLocaleString()} tokens`)
  if (budget.max_dollars > 0) parts.push(`$${budget.max_dollars.toFixed(2)}`)
  return parts.length ? `Up to ${parts.join(' / ')}` : ''
}

// ── the room's own state ────────────────────────────────────────────────────

/** What a room row / header reports about the room itself, as opposed to a member. */
export type RoomState = 'paused' | 'archived' | 'active'

export interface RoomStateMeta {
  label: string
  tone: StatusPillTone
}

/** `paused` is INFO and not WARN, matching the `agent/room_paused` notification row's own
 *  tone. The room reached a ceiling the user configured: nothing failed, nothing is at risk,
 *  and their next message resumes it. Warn here would make a working feature read as a fault,
 *  and it would disagree with the inbox row for the same event. */
const ROOM_STATE_META: Record<RoomState, RoomStateMeta> = {
  paused: { label: 'Paused', tone: 'info' },
  archived: { label: 'Archived', tone: 'neutral' },
  active: { label: 'Active', tone: 'neutral' },
}

export function roomState(room: RoomRecord): RoomState {
  // Archived wins: an archived room refuses messages, so "paused waiting for you" would be an
  // invitation to do something the backend will refuse.
  if (room.archived) return 'archived'
  return room.paused ? 'paused' : 'active'
}

export function roomStateMeta(state: RoomState): RoomStateMeta {
  return ROOM_STATE_META[state]
}

/** `3 of 6 exchanges` — the budget, as the room reads it.
 *
 *  Always against `effective_round_budget`, never `round_budget`: the declared field is 0 when
 *  the room inherits the configured default, and "0 of 0" is the inert reading this atom
 *  exists to remove. */
export function roundBudgetLabel(room: RoomRecord): string {
  const used = Math.max(0, room.rounds_used)
  const budget = Math.max(0, room.effective_round_budget)
  return `${used} of ${budget} exchange${budget === 1 ? '' : 's'}`
}

/** Who the room still owes a turn, in order — the PARKED queue only.
 *
 *  Filtered to the current roster, the same rule the arbiter's `resume_queue` applies: a
 *  member removed while the room was paused does not speak, so it must not be listed as owed
 *  a turn either. Listing it would promise a turn the backend will skip. */
export function parkedQueue(room: RoomRecord): string[] {
  return room.pending_queue.filter((n) => room.members.some((m) => m.name === n))
}

/** The icon a room uses wherever one is needed. One import site, so the list row, the header
 *  and the empty state cannot drift into three different glyphs for one noun. */
export const ROOM_ICON = Bot
