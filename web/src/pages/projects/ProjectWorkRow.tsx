import { Code2, MessageSquare, OctagonAlert, CircleStop, Repeat, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { LoopKind, RunningRow, WaitingItem, WorkOutcome, WorkRow, WorkState } from '../../lib/api'
import { clipWords } from '../../lib/clipWords'
import { loopRoute } from '../../lib/loopKind'
import { loopStatusColor, loopStatusLabel } from '../../lib/loopStatus'
import { waitingSubtitle, waitingTitle } from '../../lib/needsYou'
import { previewLine } from '../../lib/previewText'
import { phaseOf, workKindWord } from '../../lib/runningRows'
import type { Thread } from '../../lib/useThreads'
import { phaseColor, workPhaseOf } from '../../lib/workPhase'
import { MarkdownLine } from '../../ui/MarkdownLine'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { approvalInputText } from '../chat/approvalSegment'
import { statusMeta } from '../tasks/taskMeta'
import { runLook } from '../workflows/workflowMeta'

/** ── One piece of a project's work, as one row ───────────────────────────────────────────────
 *
 *  The row the Projects list draws under each project for what needs you and what is running, and
 *  for what a search matched. Each row draws only what its own fields carry, in Home's words:
 *
 *  · **A waiting row** (an item of `GET /api/waiting`): "Waiting on you", the item's title as Home
 *    writes it (`waitingTitle`), except an approval, which is its call as its card writes it,
 *    `tool(args)` cut at a word at 60 characters, in mono; the item's line (`waitingSubtitle`);
 *    its work's kind; its age.
 *  · **A running row** (a row of `GET /api/running`): its phase word, its work's title, its own
 *    line, its kind and when it last changed.
 *  · **A work row** (a thread a search matched): its own title. While its work has an item in Needs
 *    you, its state is "Waiting on you" and its line is that item's title, so the work and its
 *    item read the same words.
 *
 *  On `RowHitTarget`, so the row opens its work with one click and holds no nested control. Named
 *  for a screen reader by its kind, its title and its state, then "pinned" and "unread" when they
 *  are true: "Code session: Fix the irrigation sensor tests, testing".
 *
 *  From 1024 px it is one 36 px line in three columns, the state in a fixed first one, so every
 *  title starts at one x; then the title, kept whole before its line is cut; then the meta. In a
 *  card under 48rem (two to a row) the line is left out, as on a phone. Below 1024 px it is at
 *  least 44 px and stacks: the state, the title on up to two lines, the meta, with no line.
 *
 *  Each state's mark is a dot in its state's tone, ringed in the same tone so a forced-colours
 *  display keeps it: waiting on you in the waiting tone, work under way in the active one, work
 *  held up (a slot, a busy model, a retry) as a ring in the quiet one, and work that failed in the
 *  blocked one, the danger tone. A search match with no state says its kind, by its glyph and word,
 *  where a state goes, and its date as its meta. */

export type ProjectWork =
  | { kind: 'waiting'; item: WaitingItem }
  | { kind: 'running'; row: RunningRow }
  | { kind: 'thread'; thread: Thread; waiting?: WaitingItem }

/** How much of an approval's arguments its row shows: its card's compact line's cut. */
const ARGS_CUT = 60

const LOOP_KIND_WORD: Record<LoopKind, string> = {
  code: 'Code session', goal: 'Goal loop', research: 'Research', design: 'Design', general: 'Loop',
}

/** A waiting item's work, in words: a loop by its kind, a chat as Chat. */
function waitingKindWord(item: WaitingItem): string {
  if (item.loop_kind) return LOOP_KIND_WORD[item.loop_kind] ?? 'Loop'
  if (item.work.startsWith('chat:')) return 'Chat'
  if (item.work.startsWith('workflow:')) return 'Workflow run'
  if (item.work.startsWith('subagent:')) return 'Helper'
  if (item.work.startsWith('loop:')) return 'Loop'
  return ''
}

/** A thread's kind, in the sidebar's words: a loop the read calls only "loop" is a Goal loop. */
const THREAD_KIND_WORD: Record<Thread['kind'], string> = { chat: 'Chat', loop: 'Goal loop', code: 'Code session' }

/** Coarse age, as a row's meta says it: "<1 min", "6 min", "1 h", "3 d". */
export function ageWords(secs: number | null | undefined): string {
  if (secs == null || !Number.isFinite(secs) || secs < 0) return ''
  if (secs < 60) return '<1 min'
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins} min`
  const hours = Math.round(mins / 60)
  if (hours < 48) return `${hours} h`
  return `${Math.round(hours / 24)} d`
}

function sinceSecs(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null
  const at = Date.parse(iso)
  return Number.isFinite(at) ? Math.max(0, (now - at) / 1000) : null
}

/** An approval's call as its card writes it: `tool(args)`, the arguments cut at a word. */
export function approvalCall(item: WaitingItem): string | null {
  if (item.kind !== 'approval') return null
  const b = item.brief as Record<string, unknown>
  const tool = typeof b.tool === 'string' && b.tool.trim() ? b.tool.trim() : waitingTitle(item)
  const whole = approvalInputText(b.tool_input).split(/\s+/).filter(Boolean).join(' ')
  const line = clipWords(whole, ARGS_CUT)
  return `${tool}${line ? `(${line})` : ''}`
}

/** The tones a row's state mark is drawn in: the state families' own tokens (`tokens.css`). */
const MARK_TONE = {
  waiting: 'var(--color-state-waiting)',
  active: 'var(--color-state-active)',
  quiet: 'var(--color-state-quiet)',
  blocked: 'var(--color-state-blocked)',
} as const

/** A thread's kind by its glyph, for a search match with no state: the sidebar's marks. */
const THREAD_KIND_GLYPH: Record<Thread['kind'], LucideIcon> = { chat: MessageSquare, loop: Repeat, code: Code2 }

interface RowFields {
  state: string
  /** The mark's tone, or `''` for a row with no state. */
  dot: string
  /** The mark is a ring, not a dot: work held up. */
  hollow: boolean
  /** A search match with no state: its kind's glyph where the state's mark goes. */
  glyph?: LucideIcon
  title: string
  /** The title is an approval's call, drawn as written, in mono. */
  mono: boolean
  line: string
  /** The line is an approval's call, drawn as written, in mono. */
  lineMono: boolean
  kind: string
  when: string
  route: string
  pinned: boolean
  unread: boolean
}

/** What one row draws, field by field. Exported for its rail. */
export function workRowFields(work: ProjectWork, now: number = Date.now()): RowFields {
  if (work.kind === 'waiting') {
    const call = approvalCall(work.item)
    return {
      state: 'Waiting on you', dot: MARK_TONE.waiting, hollow: false,
      title: call ?? waitingTitle(work.item), mono: call !== null,
      line: waitingSubtitle(work.item), lineMono: false, kind: waitingKindWord(work.item),
      when: ageWords(work.item.age_secs), route: work.item.route, pinned: false, unread: false,
    }
  }
  if (work.kind === 'running') {
    const look = phaseOf(work.row)
    const phase = workPhaseOf(work.row.phase)
    return {
      state: work.row.blocked ? `Blocked, ${look.label.toLowerCase()}` : look.label,
      dot: phase === 'error' ? MARK_TONE.blocked : work.row.blocked ? MARK_TONE.quiet : MARK_TONE.active,
      hollow: work.row.blocked && phase !== 'error',
      title: work.row.name, mono: false,
      line: work.row.line && work.row.line !== look.label ? work.row.line : '', lineMono: false,
      kind: workKindWord(work.row), when: ageWords(sinceSecs(work.row.since, now)),
      route: work.row.route, pinned: false, unread: false,
    }
  }
  const { thread, waiting } = work
  const call = waiting ? approvalCall(waiting) : null
  const asked = waiting ? call ?? waitingTitle(waiting) : ''
  const state = waiting ? 'Waiting on you'
    : thread.pip === 'waiting' ? 'Waiting on you'
      : thread.pip === 'running' ? 'Running'
        : thread.pip === 'failed' ? 'Failed' : ''
  const kind = THREAD_KIND_WORD[thread.kind]
  return {
    // With no state, the kind takes the state's place (its glyph and word) and the meta is the date.
    state: state || kind,
    dot: state === 'Waiting on you' ? MARK_TONE.waiting : state === 'Running' ? MARK_TONE.active : state === 'Failed' ? MARK_TONE.blocked : '',
    hollow: false, glyph: state ? undefined : THREAD_KIND_GLYPH[thread.kind],
    title: thread.title, mono: false, line: asked, lineMono: call !== null, kind: state ? kind : '',
    when: !thread.activeAt ? '' : state ? ageWords((now - thread.activeAt) / 1000) : dateWords(thread.activeAt, now),
    route: thread.route, pinned: thread.pinned, unread: thread.pip === 'unread',
  }
}

/** When a search match last moved, as a date: its age within two days, then "Nov 5". */
function dateWords(ms: number, now: number): string {
  if (now - ms < 48 * 3600_000) return ageWords((now - ms) / 1000)
  return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** The row's name for a screen reader: kind, title and state, then pinned and unread. A match with
 *  no state says its kind once, where its state would be. */
export function workRowName(f: Pick<RowFields, 'kind' | 'title' | 'state' | 'pinned' | 'unread' | 'glyph'>): string {
  const kind = f.glyph ? f.state : f.kind
  const head = kind ? `${kind}: ${previewLine(f.title)}` : previewLine(f.title)
  return [head, f.glyph ? '' : f.state.toLowerCase(), f.pinned ? 'pinned' : '', f.unread ? 'unread' : ''].filter(Boolean).join(', ')
}

export function ProjectWorkRow({ work, onOpen, mark }: {
  work: ProjectWork
  onOpen: (route: string) => void
  /** Marks the words a search matched in the title. */
  mark?: (text: string) => React.ReactNode
}) {
  const f = workRowFields(work)
  const meta = [f.kind, f.when].filter(Boolean).join(' · ')
  return (
    <li tabIndex={-1} onClick={f.route ? () => onOpen(f.route) : undefined} data-work-row={work.kind}
      className="group relative grid min-w-0 cursor-pointer grid-cols-1 items-start gap-y-0.5 rounded-md px-s py-xs text-on-surface-var hover:bg-surface-high max-lg:min-h-11 max-lg:gap-y-xs max-lg:py-s lg:min-h-9 lg:grid-cols-[150px_minmax(0,1fr)_auto] lg:items-center lg:gap-x-m lg:gap-y-0 has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary">
      <RowHitTarget label={workRowName(f)} />
      <span data-type="label-s" data-row-state className={`inline-flex min-w-0 items-center gap-xs text-on-surface ${f.state ? '' : 'max-lg:hidden'}`}>
        {f.dot && <span aria-hidden data-mark="dot" className="size-2 shrink-0 rounded-pill border-[1.5px]" style={{ background: f.hollow ? 'transparent' : f.dot, borderColor: f.dot }} />}
        {f.glyph && <f.glyph aria-hidden size={14} data-row-glyph className="shrink-0 text-on-surface-var" />}
        {f.state}
      </span>
      <span className="flex w-full min-w-0 items-baseline gap-s">
        <span data-type="body-s" data-row-title title={previewLine(f.title)}
          className={`min-w-0 text-on-surface lg:max-w-full lg:shrink-0 lg:truncate max-lg:line-clamp-2 max-lg:whitespace-normal ${f.mono ? 'font-mono' : ''}`}>
          {f.mono ? f.title : mark ? mark(previewLine(f.title)) : <MarkdownLine text={f.title} />}
        </span>
        {f.line && <span data-type="body-s" data-row-line className={`min-w-0 flex-1 basis-0 truncate text-on-surface-low max-lg:hidden @max-3xl/card:hidden ${f.lineMono ? 'font-mono' : ''}`}>{f.lineMono ? f.line : previewLine(f.line)}</span>}
      </span>
      {meta && <span data-type="caption" className="shrink-0 text-on-surface-low">{meta}</span>}
    </li>
  )
}

// ── The Work board's vocabulary, moved here from the project page ──────────────────────────────

/** The needs-you group's dot: what waits in it waits on her answer, input's tone (#3833). */
export const NEEDS_YOU_DOT = phaseColor('input')

/** The board's state groups, in server (BOARD_ORDER) order, as human labels. Needs-input
 *  is pinned first by the server; these are only display names.
 *
 *  🪤 `needs_input` IS NOT HAND-TYPED HERE, AND THAT IS THE WHOLE POINT (#3471). It read
 *  `'Needs input'` while `lib/loopStatus` — the ONE registry for this vocabulary — ships
 *  `'Needs you'` for the same wire value. That registry's own header names `"Needs you" vs
 *  "Needs input"` as drift it retired; this map was a fifth copy its rail could not see, so
 *  the drift it declared fixed was live on the project page. Sourced from the registry now,
 *  which is also what makes the divergence unrepeatable rather than merely corrected.
 *
 *  Exported so the coherence rail can compare it to the registry directly. */
export const WORK_STATE_LABEL: Record<WorkState, string> = {
  needs_input: loopStatusLabel('needs_input'),
  working: 'Working',
  queued: 'Queued',
  suspended: 'Suspended',
  review: 'Review',
  // 🔴 NOT "Done". The server's `done` group is "nothing left for you to do", and a cancelled
  // task, a skipped one and a failed run all belong in it — measured on a project page, a
  // cancelled task sat under "DONE · 2" with the completed task's glyph, so the board said the
  // press kit had shipped. The group is named for what it holds; each row says how it ended.
  done: 'Closed',
}

/** How a Closed row ended — the icon, word and tone each row carries (`containers.BoardOutcome`).
 *
 *  The WORDS are the owning registries', by reference: a task's ending reads exactly as it does
 *  on `#/tasks` (`taskMeta.statusMeta`) and a loop's as it does in the loop list
 *  (`lib/loopStatus`), so no second vocabulary is minted here. The record is exhaustive over the
 *  closed union, so a new outcome is a typecheck failure rather than an unlabelled row. */
export const WORK_OUTCOME_LOOK: Record<WorkOutcome, { label: string; icon: LucideIcon; tone: string }> = {
  completed: statusMeta('done'),
  cancelled: statusMeta('cancelled'),
  skipped: statusMeta('skipped'),
  failed: { label: loopStatusLabel('failed'), icon: OctagonAlert, tone: loopStatusColor('failed') },
  stopped: { label: loopStatusLabel('stopped'), icon: CircleStop, tone: loopStatusColor('stopped') },
  ended_early: { label: loopStatusLabel('ended_early'), icon: TriangleAlert, tone: loopStatusColor('ended_early') },
  // A run a person declined: the run registry's word and glyph (`workflowMeta.runLook`), in the
  // same informational tone a cancelled row takes — a decision someone made, not a fault.
  declined: { label: runLook('declined').label, icon: runLook('declined').icon, tone: 'var(--color-on-surface-low)' },
}

/** Where a Work board row opens: a loop on its kind's page, a run on its run page, a task in
 *  Tasks. Exported for the board's rail. */
export function workRoute(row: Pick<WorkRow, 'source' | 'run_id' | 'kind'>): string {
  if (row.source === 'loop') return loopRoute({ id: row.run_id, kind: row.kind })
  if (row.source === 'task') return `tasks?open=${encodeURIComponent(row.run_id)}`
  return `workflows/runs/${row.run_id}`
}
