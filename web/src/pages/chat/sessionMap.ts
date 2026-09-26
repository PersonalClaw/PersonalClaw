/** SESSION MAP — typed mark model + derivation (SEMANTIC-SESSION-MAP §A.2, atom SSM-1).
 *
 *  The Session Map is PC's in-session index (it replaced the Activity → Index tab). This module
 *  owns two things, one layered on the other:
 *
 *   · the TYPED INDEX — the `SessionMark` shape and the ONE pure derivation `sessionMapMarks`: one
 *     mark per turn plus one per typed sub-event inside it. It is the shape the durable endpoint
 *     (`dashboard/chat_session_map.py`, SSM-2) mirrors field for field, which is why its closed
 *     vocabulary stays here even though the rail no longer draws every kind.
 *   · the MAP — `SessionMapEntry` / `sessionMapEntries`: what the rail and the drawer actually
 *     show, which is ONE ENTRY PER USER MESSAGE (see `sessionMapEntries` for the owner's ruling).
 *     An entry is the typed index grouped by exchange, so the two can never disagree about which
 *     turns belong to which question.
 *
 *  No new backend and no new WS channel: everything a mark needs already exists on the hydrated
 *  `ChatTurn` / its `Segment[]` (built by `hydrateTurns`, the surface `deriveActivity` also reads)
 *  and on the live `SubagentCard[]` (the parallel `subagent_spawn/tool/done` stream).
 */
import type { ChatTurn, Segment, SubagentCard } from './chatTypes'
import { turnText, markCoordOf } from './chatTypes'
import { previewText } from '../../lib/previewText'

/** The CLOSED mark vocabulary (§A.2). A mark is EITHER a turn (`user` / `assistant`) or a
 *  typed sub-event that occurred inside an assistant turn (`tool` / `approval` / `error` /
 *  `subagent` / `activity`). Adding a kind is a contract change: every consumer switches
 *  on this union, so the set lives here as the single source of truth. */
export type SessionMarkKind = 'user' | 'assistant' | 'tool' | 'approval' | 'error' | 'subagent' | 'activity'

/** The vocabulary as a runtime array, so a consumer (or a test) can validate a mark's
 *  `kind` against the closed set without re-listing it. */
export const SESSION_MARK_KINDS: readonly SessionMarkKind[] = [
  'user', 'assistant', 'tool', 'approval', 'error', 'subagent', 'activity',
] as const

export interface SessionMark {
  /** 0-based position in the returned array — the mark's stable identity and render key. */
  markIndex: number
  /** The closed vocabulary above. */
  kind: SessionMarkKind
  /** The role of the OWNING turn. A sub-event mark inherits it (every sub-event lands in an
   *  assistant turn), so downstream tone / ARIA can treat user vs. agent uniformly. */
  role: 'user' | 'assistant'
  /** The JUMP coordinate — the owning turn's `visibleIndex` (backend `at_message_index`,
   *  what `jumpToTurn` already speaks). It is NOT `markIndex`: many marks share one turn.
   *  Falls back to the turn's array position when a live-built turn has no `visibleIndex`
   *  yet — the same fallback `deriveActivity` uses for its `turnIndex`. */
  visibleIndex: number
  /** The owning turn's timestamp; `''` when a turn carries none — never null/undefined. */
  ts: string
  /** One-line PLAIN-TEXT preview (markdown stripped via `previewText`) for the map row,
   *  the hover card, and the accessible name. Always a string. */
  preview: string
  /** Tool-mark ONLY: mirrors `ToolSegment.ok` — present and `false` on a FAILED tool call,
   *  `undefined` otherwise. Carried because §A.2 paints a failed tool mark `--color-danger`,
   *  and that outcome cannot be recovered from a mark once the raw segment is gone. */
  ok?: boolean
}

const PREVIEW_CAP = 140

/** SELF-SUPPRESSION THRESHOLD (§A.1) — a map of fewer than two entries indexes nothing worth
 *  navigating, so the map does not render at all. Defined here, with the contract, because BOTH
 *  forms apply it: the rail (SSM-4) renders nothing, and the coarse-pointer drawer (SSM-10)
 *  explains itself instead of showing a blank panel. Two forms, one threshold, one definition. */
export const SESSION_MAP_MIN_MARKS = 2

/** Compact one-line preview for a tool / approval mark: the STABLE tool name plus its
 *  refined one-liner (the command / file+range, else the raw input), markdown-stripped. */
function toolPreview(tool: string, detail?: string, input?: string): string {
  return previewText([tool, detail || input].filter(Boolean).join(' — '), PREVIEW_CAP)
}

/** Map a single segment to its sub-event mark payload, or `null` when the segment is not an
 *  index event. `text` / `thinking` never produce a mark — the turn mark already stands for
 *  the body. An `activity` segment is marked ONLY when it carries an `activityKind` (§A.2
 *  keys the mark on it): a bare coarse "Thinking…" activity line is not an index-worthy
 *  event and would only clutter the map. */
function subEventMark(seg: Segment): Pick<SessionMark, 'kind' | 'preview' | 'ok'> | null {
  switch (seg.kind) {
    case 'tool':
      return { kind: 'tool', preview: toolPreview(seg.tool, seg.detail, seg.input), ok: seg.ok }
    case 'approval':
      return { kind: 'approval', preview: toolPreview(seg.tool, undefined, seg.input) }
    case 'error':
      return { kind: 'error', preview: previewText(seg.text, PREVIEW_CAP) }
    case 'activity':
      if (!seg.activityKind) return null
      return { kind: 'activity', preview: previewText(seg.text, PREVIEW_CAP) }
    default:
      return null // text, thinking → no mark
  }
}

/** Derive the ordered Session Map marks from the hydrated transcript, plus the live
 *  subagent cards (which are NOT `ChatTurn` segments — they ride the parallel
 *  `subagent_spawn/tool/done` WS stream into `SubagentCard[]`, §A.2, so they enter here as
 *  an optional second argument rather than being invented as a segment kind).
 *
 *  Emits ONE mark per turn (kind = its role), then one typed sub-event mark for each
 *  `tool` / `approval` / `error` / meaningful-`activity` segment IN SEGMENT ORDER, each
 *  inheriting the owning turn's role, jump coordinate, and timestamp so a click on any of
 *  them lands on the turn that produced it. Subagent marks are appended after the
 *  turn-derived marks and jump to the most recent turn (where a fire-and-forget subagent is
 *  spawned), carrying the assistant role. */
export function sessionMapMarks(turns: ChatTurn[], subagents: SubagentCard[] = []): SessionMark[] {
  const marks: SessionMark[] = []
  const push = (m: Omit<SessionMark, 'markIndex'>) => { marks.push({ markIndex: marks.length, ...m }) }

  turns.forEach((turn, i) => {
    const visibleIndex = markCoordOf(turn, i)
    const ts = turn.ts ?? ''
    push({ kind: turn.role, role: turn.role, visibleIndex, ts, preview: previewText(turnText(turn), PREVIEW_CAP) })
    for (const seg of turn.segments) {
      const sub = subEventMark(seg)
      if (sub) push({ ...sub, role: turn.role, visibleIndex, ts })
    }
  })

  if (subagents.length) {
    const last = turns[turns.length - 1]
    const visibleIndex = last ? markCoordOf(last, turns.length - 1) : 0
    const ts = last?.ts ?? ''
    for (const s of subagents) {
      push({ kind: 'subagent', role: 'assistant', visibleIndex, ts, preview: previewText(s.task || s.agent || 'subagent', PREVIEW_CAP) })
    }
  }

  return marks
}

/** One ENTRY on the map: a user message, and the exchange it opened. */
export interface SessionMapEntry {
  /** 0-based position among the entries — the render key, and the N of "Message N of M". */
  markIndex: number
  /** The user message's jump coordinate (`markCoordOf`) — what `onJumpTo` receives. */
  visibleIndex: number
  /** EVERY turn coordinate in this exchange, in order: the user message first, then each reply
   *  turn up to the next user message. The current-region observer watches all of them, which is
   *  what keeps a question lit while its long answer is being read after the question itself has
   *  scrolled away. */
  coords: number[]
  /** The user message's timestamp; `''` when the turn carries none. */
  ts: string
  /** The user message, one line of plain text. */
  preview: string
  /** The opening of the reply, one line of plain text — `''` until reply text has arrived. */
  response: string
}

/** The map's entries: ONE PER USER MESSAGE.
 *
 *  🔑 THE OWNER'S RULING (2026-09-25), and why the typed index is not what the rail draws any more.
 *  Asked for a map of the conversation, the rail had been drawing every assistant reply and every
 *  tool call, approval and stats line as a mark of its own. The owner: the map should be "only a map
 *  of user messages", because an assistant response is too long to show in any case, and its
 *  beginning is already visible at the bottom of the user message's hover card. So an entry is a
 *  QUESTION: its card previews the start of the answer, and the answer's turns belong to it.
 *
 *  Built by GROUPING the typed index rather than by re-walking the turns, so the entry and the
 *  durable endpoint's marks agree about coordinates and previews by construction. Agent output that
 *  precedes the first user message (an agent-initiated or resumed session) opens no entry: there is
 *  no question to file it under, and the map indexes the user's messages only. */
export function sessionMapEntries(turns: ChatTurn[]): SessionMapEntry[] {
  const entries: SessionMapEntry[] = []
  for (const mark of sessionMapMarks(turns)) {
    if (mark.kind === 'user') {
      entries.push({
        markIndex: entries.length,
        visibleIndex: mark.visibleIndex,
        coords: [mark.visibleIndex],
        ts: mark.ts,
        preview: mark.preview,
        response: '',
      })
      continue
    }
    const entry = entries[entries.length - 1]
    if (!entry) continue
    if (!entry.coords.includes(mark.visibleIndex)) entry.coords.push(mark.visibleIndex)
    if (!entry.response && mark.kind === 'assistant') entry.response = mark.preview
  }
  return entries
}
