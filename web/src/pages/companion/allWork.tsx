import type { RunningRow, WaitingItem, WorkKind } from '../../lib/api'
import { sentenceStart } from '../../lib/attentionLanes'
import { BUDDY_LINES, lineFor } from '../../lib/buddy/copy'
import { workOf } from '../../lib/buddy/host'
import { lineOf } from '../../lib/buddy/lines'
import { WAITING_KIND_LABEL, waitingSource, waitingText } from '../../lib/waitingList'
import { BuddyOrb } from '../../ui/BuddyOrb'
import { Morph } from '../../ui/motion/Morph'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { CircleDot } from 'lucide-react'
import { KIND_GLYPH } from '../../ui/BuddyCard'
import type { BuddyState } from '../../ui/buddy/orbPoses'

// ── View one, everything at once (#3898) ───────────────────────────────────────────────────────
//
// Every piece of work as the desktop buddy shows it: its orb at the row's size, its kind's glyph,
// and one line from the buddy's own templates, at most the phone's 48 characters (copy.md, the
// "phone, all work" surface), with the work's name cut first. The orbs are told apart by their
// state (density and badge) and the glyph, never by a colour each. Rows hold their orbs still: a
// list of moving orbs on a phone is motion for its own sake.

const WORK_KINDS: readonly WorkKind[] = ['chat', 'loop', 'workflow', 'subagent', 'trigger', 'webhook']

/** The kind of work a `<kind>:<id>` names, or null for an item that belongs to no work. */
export function workKindOf(work: string): WorkKind | null {
  const kind = work.split(':')[0] as WorkKind
  return WORK_KINDS.includes(kind) ? kind : null
}

/** The orb a waiting item shows: an approval's waiting still with the "!", any other ask's with
 *  the "?". */
export function waitingOrbState(item: WaitingItem): 'approval' | 'input' {
  return item.kind === 'approval' ? 'approval' : 'input'
}

/** The work a waiting item's line names: the running row's own name, the chat's title, else the
 *  item's source in its own words, else its kind. */
export function waitingWorkName(item: WaitingItem, rows: ReadonlyMap<string, RunningRow>): string {
  const title = (item.brief as { session_title?: unknown }).session_title
  return rows.get(item.work)?.name || (typeof title === 'string' && title.trim()) || sentenceStart(waitingSource(item)) || WAITING_KIND_LABEL[item.kind]
}

/** A waiting item's one line: "<work> needs your OK: <tool>", or "<work> asks: <what it asks>". */
export function waitingLine(item: WaitingItem, work: string): string {
  const tool = (item.brief as { tool?: unknown }).tool
  const parts = item.kind === 'approval'
    ? BUDDY_LINES.approval(work, { tool: typeof tool === 'string' ? tool : '' })
    : BUDDY_LINES.question(work, waitingText(item))
  return lineFor('phoneAllWork', parts)
}

/** A running row's one line, in its phase's own template. */
export function runningLine(row: RunningRow, now: number): string {
  return lineOf({ kind: 'phase', phase: row.phase, work: workOf(row, now) }, 'phoneAllWork')
}

/** A kind of work's glyph, as the buddy's card draws it. */
function KindGlyph({ kind }: { kind: WorkKind }) {
  const Icon = KIND_GLYPH[kind] ?? CircleDot
  return <Icon size={14} aria-hidden className="shrink-0 text-on-surface-var" data-kind-glyph={kind} />
}

/** The shared element's id for one piece of work or one ask: its view-one row's orb and view
 *  two's are the same object, so switching views moves it rather than cutting it. */
export const orbIdOf = (key: string) => `companion-orb-${key}`

/** A row's head: the orb, the kind's glyph and the one line, wrapping rather than cut by a style
 *  (the template already cut it). With `onOpen` the head is the row's tap target: it opens view
 *  two on the row, a 44 px target or more. */
export function WorkHead({ id, state, kind, line, onOpen }: {
  /** What the row is (a waiting item's id, a running row's work): its orb's shared element. */
  id: string
  state: BuddyState
  kind: WorkKind | null
  line: string
  onOpen?: () => void
}) {
  const inner = (
    <>
      <Morph id={orbIdOf(id)} className="shrink-0"><BuddyOrb state={state} size={24} line="" still /></Morph>
      {kind ? <span className="mt-0.5"><KindGlyph kind={kind} /></span> : null}
      <p data-type="body-m" className="min-w-0 break-words text-on-surface">{line}</p>
    </>
  )
  // The row-wide hit target (`RowHitTarget`), a sibling of the head's content, with the focus ring
  // drawn on the head, as Home's rows open their detail.
  return onOpen ? (
    <div tabIndex={-1} onClick={onOpen} data-work-head=""
      className="relative flex min-h-11 min-w-0 cursor-pointer items-start gap-s rounded-md has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary">
      <RowHitTarget label={`Show ${line}`} />
      {inner}
    </div>
  ) : (
    <div className="flex min-w-0 items-start gap-s" data-work-head="">{inner}</div>
  )
}
