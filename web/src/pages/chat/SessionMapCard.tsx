import type { SessionMark } from './sessionMap'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import { rowSubject } from '../../lib/rowSubject'

/** SESSION MAP PREVIEW CARD — what a mark says when you point at it
 *  (SEMANTIC-SESSION-MAP §A.3, atom SSM-6).
 *
 *  A mark on the rail (SSM-4) is a 4px tick: it says WHERE a thing is in the session and
 *  nothing about WHAT it is. This card is the answer to "what is this one?", and §A.3 names
 *  the three things KiroCrew's equivalent lacks, each of which this owns:
 *
 *   · a ROLE LABEL and a TIMESTAMP — PC already persists both on every turn (`ChatTurn.role`
 *     / `.ts`, carried onto the mark by SSM-1), so the header is free; KiroCrew shows neither.
 *   · the REQUEST LINE in `--color-on-surface` — deliberately NOT `--color-on-surface-low`,
 *     which is the "very low contrast metadata" this card was flagged to beat. The dimmed
 *     ramp is for chrome (the clock time below), never for the sentence you came to read.
 *   · a RESPONSE EXCERPT under a hairline divider, in `--color-on-surface-var`.
 *
 *  🔑 THE CARD IS DERIVED FROM THE MARK LIST, NOT FROM THE TRANSCRIPT. A `SessionMark`
 *  carries ONE `preview` (SSM-1's contract), so "request line + response excerpt" cannot come
 *  from a single mark — it is a property of the EXCHANGE the mark sits in. `sessionMapCardContent`
 *  reads that off the ordered marks the rail already holds, which keeps the rail a pure function
 *  of `SessionMark[]` (SSM-4's own contract) instead of re-hydrating turns behind its back.
 *
 *  🪤 AND IT IS WHY EVERY MARK'S CARD IS DIFFERENT. A turn emits up to seven marks (turn + typed
 *  sub-events), all sharing one `visibleIndex` and one `ts`. A card built from the exchange alone
 *  would render SEVEN IDENTICAL cards along one turn's worth of rail — the map would answer "what
 *  is this one?" with "the same as its neighbour". So the response slot shows the MARK'S OWN
 *  preview whenever the mark is not the request itself: the tool line for a tool tick, the error
 *  text for an error tick, the reply text for the assistant turn.
 */

/** The role words the transcript already uses for the two sides of a turn (`ChatPage.tsx`
 *  renders exactly these in its own compact history preview), so the card does not invent a
 *  third vocabulary for a distinction the product has already named. */
const ROLE_LABEL: Record<SessionMark['role'], string> = { user: 'You', assistant: 'Assistant' }

/** How much of a mark's preview may become part of a control's accessible NAME. The 40-char
 *  budget is `lib/rowSubject`'s row-scoped one (`design/computedNames.test.tsx` measured the
 *  app-wide cap): a name must be DISTINGUISHING and BOUNDED, and a rail of ticks is the densest
 *  row list in the app. */
const NAME_SUBJECT_CAP = 40

/** Everything the card renders, derived from the ordered marks plus which one is pointed at. */
export interface SessionMapCardContent {
  /** "You" / "Assistant" — the owning turn's side of the conversation. */
  roleLabel: string
  /** The owning turn's timestamp, verbatim from the mark (`''` when the turn carries none). */
  ts: string
  /** The prompt that opened this exchange — the nearest `user` mark at or before this one.
   *  `''` when the session starts with agent output (a resumed or agent-initiated session). */
  request: string
  /** What came back: this mark's own preview, or — when the mark IS the request — the reply
   *  that followed it. `''` when a prompt has no answer yet (mid-stream). */
  response: string
  /** 1-based position of this mark's TURN among the turns the map indexes, and the total.
   *  Turn-scoped rather than mark-scoped because "turn 4 of 18" is the coordinate a reader
   *  can act on; seven ticks share one turn. */
  turnPosition: number
  turnTotal: number
}

/** The distinct jump coordinates the marks span, in first-appearance order — the map's turns. */
function turnCoordinates(marks: SessionMark[]): number[] {
  const seen: number[] = []
  for (const m of marks) if (!seen.includes(m.visibleIndex)) seen.push(m.visibleIndex)
  return seen
}

/** Derive the card for `marks[index]`. Pure, so the card's content is testable without a DOM
 *  and the rail stays a pure function of the marks it is handed. */
export function sessionMapCardContent(marks: SessionMark[], index: number): SessionMapCardContent {
  const mark = marks[index]
  const turns = turnCoordinates(marks)
  // The request is the nearest prompt AT OR BEFORE this mark: a sub-event mark inherits the
  // prompt of the exchange it happened in, and a user mark is its own request.
  let request = ''
  for (let i = index; i >= 0; i--) {
    if (marks[i].kind === 'user') { request = marks[i].preview; break }
  }
  // A user mark's answer is the next assistant-turn mark; any other mark IS the answer (see the
  // "seven identical cards" trap above).
  let response = mark.preview
  if (mark.kind === 'user') {
    response = ''
    for (let i = index + 1; i < marks.length; i++) {
      if (marks[i].kind === 'assistant') { response = marks[i].preview; break }
    }
  }
  return {
    roleLabel: ROLE_LABEL[mark.role],
    ts: mark.ts,
    request,
    response,
    turnPosition: turns.indexOf(mark.visibleIndex) + 1,
    turnTotal: turns.length,
  }
}

/** The accessible NAME of a mark's trigger: its turn coordinate plus a bounded piece of its own
 *  preview (§A.6 — "Turn 4 of 18: {truncated request}", never the full turn text).
 *
 *  🔑 THE MARK'S OWN PREVIEW, NOT THE EXCHANGE'S REQUEST, for the same reason the card's response
 *  slot uses it: naming from the exchange would give one turn's seven ticks ONE name, which is the
 *  duplicate-name defect `computedNames.test.tsx` measured at ×83 on `#/notifications`. */
export function sessionMapMarkName(marks: SessionMark[], index: number): string {
  const { turnPosition, turnTotal } = sessionMapCardContent(marks, index)
  return `Turn ${turnPosition} of ${turnTotal}: ${rowSubject([marks[index].preview], NAME_SUBJECT_CAP)}`
}

export interface SessionMapCardProps {
  /** The rail's full ordered mark list — the card reads its exchange off it. */
  marks: SessionMark[]
  /** Which mark is being previewed. */
  index: number
  /** The pointer arrived on the card itself: cancel the close the trigger's mouse-leave armed,
   *  so the pointer can cross the gap and read (or select) the excerpt. */
  onMouseEnter: () => void
  /** The pointer left the card: dismiss it. */
  onMouseLeave: () => void
}

/** The card body. Rendered INSIDE `ui/Popover`'s flyout, so the frosted `.glass` material, the
 *  `--radius-lgi` corner, the `overlayEnter` spring, the viewport clamp, `z-[var(--z-menu)]`,
 *  single-layer Escape and focus-restore-on-dismiss all come from the shared primitive (§A.3) —
 *  this owns only what is inside it.
 *
 *  NOT `aria-hidden`. The card carries strictly MORE than its trigger's accessible name (a
 *  timestamp and the response excerpt), and hiding real content from assistive tech to dodge a
 *  double-read would be trading a whole surface for a duplicated phrase. §A.6 puts the
 *  announcement policy on an `aria-live` region that speaks only while focused, which is where
 *  the keyboard layer (SSM-7) resolves double-speak — the card does not pre-empt it here.
 */
export function SessionMapCard({ marks, index, onMouseEnter, onMouseLeave }: SessionMapCardProps) {
  const { roleLabel, ts, request, response } = sessionMapCardContent(marks, index)
  const iso = isoStamp(ts)
  return (
    <div data-session-map-card onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
      <div className="flex items-baseline justify-between gap-s">
        <span data-type="label-s" data-session-map-role className="text-on-surface-var">{roleLabel}</span>
        {/* An unreadable stamp renders NOTHING rather than a placeholder — `lib/epoch`'s contract,
            inherited here so a turn with no `ts` (every live-built assistant turn today, see the
            plan's SSM-2 DEVIATION) shows a card with no clock instead of "Invalid Date". */}
        {iso ? (
          <time
            data-type="caption"
            dateTime={iso}
            title={fullStamp(ts)}
            className="shrink-0 tabular-nums text-on-surface-low"
          >
            {clockTime(ts)}
          </time>
        ) : null}
      </div>
      {request ? (
        <p data-type="body-s" data-session-map-request className="mt-s line-clamp-2 text-on-surface">{request}</p>
      ) : null}
      {response ? (
        <p
          data-type="caption"
          data-session-map-response
          className="mt-s line-clamp-3 border-t border-outline-variant pt-s text-on-surface-var"
        >
          {response}
        </p>
      ) : null}
    </div>
  )
}
