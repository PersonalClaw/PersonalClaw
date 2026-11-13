import type { LoopKind, RunningRow } from './api'
import { WORK_PHASE_LOOKS, workPhaseOf, type PhaseTone } from './workPhase'

// ── A running list row, in words ─────────────────────────────────────────────────────────────────
//
// What every reader of the running list (`GET /api/running`) says of one row, so Mission Control's
// Working lane and the phone's Running section name a row the same way: its phase in the one phase
// table's words (`workPhase.ts`), what it is doing, and the kind of work it is.

/** What it says of work a restart cut off (`useCutOff`). */
export const INTERRUPTED_LINE = 'The gateway restarted while it ran.'

/** A row's kind in words, which a link names it by beside its name: it stays while the work runs,
 *  where its line changes with every phase. */
export const KIND_WORD: Record<RunningRow['kind'], string> = {
  chat: 'chat', loop: 'loop', workflow: 'workflow run', subagent: 'helper', trigger: 'automation', webhook: 'webhook',
}

/** What kind of work a row is, as a heading's words say it (sentence case): a loop by its kind. */
const LOOP_KIND_WORD: Record<LoopKind, string> = {
  code: 'Code session', goal: 'Goal loop', research: 'Research', design: 'Design', general: 'Loop',
}
const WORK_KIND_WORD: Record<RunningRow['kind'], string> = {
  chat: 'Chat', loop: 'Loop', workflow: 'Workflow run', subagent: 'Helper', trigger: 'Automation', webhook: 'Webhook',
}

/** The kind of work *row* is, in words: "Code session", "Goal loop", "Chat", "Workflow run",
 *  "Helper". A loop, or a run started as one, by its kind (`loop_kind`), else "Loop". */
export function workKindWord(row: Pick<RunningRow, 'kind' | 'loop_kind'>): string {
  if (row.kind === 'loop') return LOOP_KIND_WORD[row.loop_kind as LoopKind] ?? WORK_KIND_WORD.loop
  return WORK_KIND_WORD[row.kind] ?? ''
}

/** A row's phase: its word and its tone, or the wire's own word for one this build does not know. */
export function phaseOf(row: RunningRow): { label: string; tone: PhaseTone } {
  const phase = workPhaseOf(row.phase)
  return phase ? WORK_PHASE_LOOKS[phase] : { label: String(row.phase || ''), tone: 'neutral' }
}

/** A word and a line, the word once: `Thinking: Running`, but `Done in 12 seconds`, `Searching your
 *  knowledge` and `Stopped: the site refused`, whose line already starts with its word. */
export function wordAndLine(word: string, line: string): string {
  if (!word) return line
  if (!line || line === word) return word
  return line.startsWith(`${word}:`) || line.startsWith(`${word} `) ? line : `${word}: ${line}`
}

/** A row's phase and what it is doing, in one line, and `Blocked, …` for one held up. */
export function phaseLine(row: RunningRow): string {
  const said = wordAndLine(phaseOf(row).label, row.line)
  return row.blocked ? `Blocked, ${said}` : said
}
