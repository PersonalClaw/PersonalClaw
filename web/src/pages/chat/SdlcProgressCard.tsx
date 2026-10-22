import { fvs, withWeight } from '../../design/fontWeight'
import { motion } from 'framer-motion'
import { Loader2, ArrowUpRight, AlertTriangle, HelpCircle, PanelRight } from 'lucide-react'
import type { Loop } from '../../lib/api'
import { loopStatusLabel, loopStatusTone, effectiveLoopStatus, shownCycle } from '../../lib/loopStatus'
import { loopKindMeta } from '../../lib/loopKind'
import { foldRunSnapshot } from '../loops/runFold'
import { LoopMatrix, loopMatrix, matrixSummary } from '../loops/LoopMatrix'
import { cycleLabel } from '../loops/LoopProgressStrip'
import { messageEnter } from '../../design/motion'
import { TextLink } from '../../ui/TextLink'
import { useOpenBeside } from '../../ui/OpenBeside'
import type { ChatTurn, Segment, ToolSegment } from './chatTypes'
import { useWatchedLoop } from './loopWatch'

/** Live in-chat card for a Code project / Goal Loop the agent created or started from chat (see
 *  sdlc_tools.py). Detected from the tool segment by tool name + the `/#/code/<id>` or
 *  `/#/loops/<id>` deep link the tool returns, then this card reads the loop directly. Replaces the
 *  bare ToolCard for these tools so a created/launched entity reads as a living thing, not a log
 *  line.
 *
 *  Compact, as #3824's amendment asks: its name and status, then its kind, cycle, elapsed time and
 *  agents on one line, its phase matrix with every cell's state in words (`LoopMatrix`), what it
 *  needs from you when it waits, and a Details that opens the loop's detail tile beside the
 *  conversation (`loop:<id>`, `beside/LoopBeside`), where its steps, cycles and controls are. One
 *  card per loop in a chat, where the chat first names it (`liveLoopCards`).
 *
 *  It is a status mirror: it pauses, stops and deletes nothing. Those live on the loop's own
 *  page, where a Code project's Delete says what it removes: its branches, and the work its run
 *  kept. The card once carried a second copy of them, a Delete with no word of that, for a
 *  Projects hub that no longer renders it; nothing turned that copy on, so it is gone. */

const SDLC_TOOLS = new Set([
  // Unified project-run tools (code/goal/general/design/research kinds). A status
  // check also renders the live card (its output carries the cockpit deep-link);
  // `created` stays false since it doesn't end with `_create`.
  'project_run_create', 'project_run_start', 'project_run_status',
])

export interface SdlcRef { kind: 'code' | 'loop'; id: string; created: boolean }

/** Recognize an SDLC create/start tool segment and pull the entity ref out of
 *  its output. Returns null for any other tool. */
export function sdlcRefFromTool(toolName: string | undefined, output: string | undefined): SdlcRef | null {
  if (!toolName || !SDLC_TOOLS.has(toolName) || !output) return null
  const m = output.match(/\/#\/(code|loops)\/([0-9a-f]{6,})/i)
  if (!m) return null
  return { kind: m[1].toLowerCase() === 'code' ? 'code' : 'loop', id: m[2], created: toolName.endsWith('_create') }
}

/** Compact elapsed — "3m" / "1h 4m" / "2d". */
export function fmtE(sec: number): string {
  if (!sec || sec < 60) return `${Math.max(0, Math.floor(sec))}s`
  const m = Math.floor(sec / 60); if (m < 60) return `${m}m`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

/** How many agents a loop works with: its roster's, or its one agent. */
export const agentCount = (l: Pick<Loop, 'execution' | 'roster'>): number =>
  l.execution === 'multi_agent' && l.roster?.length ? l.roster.length : 1

/** The tool segments that become a loop's card: the first in the chat to name each loop. Every call
 *  after it about the same loop (its start, a status read) keeps its plain tool card, so a loop the
 *  agent created, started and checked on is one card, not three. */
export function liveLoopCards(turns: ChatTurn[]): Set<Segment> {
  const shown = new Set<string>()
  const cards = new Set<Segment>()
  for (const turn of turns) {
    for (const seg of turn.segments) {
      if (seg.kind !== 'tool' || !(seg as ToolSegment).done) continue
      const ref = sdlcRefFromTool((seg as ToolSegment).tool, (seg as ToolSegment).output)
      if (!ref || shown.has(ref.id)) continue
      shown.add(ref.id)
      cards.add(seg)
    }
  }
  return cards
}

/** What the loop needs from its owner while it is parked on them, or why it stopped. */
export function loopAttention(entity: Loop, status: string): { tone: 'info' | 'warn'; label: string; text: string; sub: string } | null {
  if (status === 'needs_input') {
    const pq = entity.pending_question
    const q = (typeof pq === 'string' ? pq : pq?.question || '').trim()
    const why = (typeof pq === 'string' ? '' : pq?.why || '').trim()
    // A spend cap's pause asks nothing: its text is the cap's own sentence.
    if (typeof pq !== 'string' && pq?.spend_cap) return { tone: 'warn', label: 'Paused by a spend cap', text: q, sub: why }
    if (typeof pq !== 'string' && pq?.no_tools) return { tone: 'warn', label: 'Paused: its model can’t use tools', text: q, sub: why }
    return { tone: 'info', label: 'Needs your input', text: q || 'Waiting on your answer.', sub: why }
  }
  if (status === 'blocked' || status === 'failed' || status === 'stagnant') {
    const reason = (entity.error_message || '').trim()
    if (reason) return { tone: 'warn', label: loopStatusLabel(status), text: reason, sub: '' }
  }
  return null
}

/** The loop as the card and the tile both read it: its effective status, its real kind, its title,
 *  its address and its matrix. */
export function loopView(entity: Loop | null, ref: SdlcRef) {
  // The EFFECTIVE status, so a budget-exhausted finish reads "Ended early" here as on its page.
  const status = entity ? effectiveLoopStatus(entity.status, entity.stop_reason, entity.held) : (ref.created ? 'ready' : '…')
  // The REAL kind from the fetched loop, not the coarse ref kind, or a general or design loop
  // would read as a goal loop.
  const kind = entity?.kind || (ref.kind === 'code' ? 'code' : 'goal')
  const meta = loopKindMeta(kind)
  const title = entity?.name || `${meta.noun}${kind === 'code' ? ' project' : ''}`
  // Code keeps its own section/cockpit route; every other kind resolves under #/loops/<id>.
  const href = `#/${kind === 'code' ? 'code' : 'loops'}/${ref.id}`
  const vm = entity ? foldRunSnapshot({ ...entity, kind, status }) : null
  const matrix = entity ? loopMatrix(entity, status, vm) : null
  return { status, kind, meta, title, href, vm, matrix }
}

export function SdlcProgressCard({ refObj, inPane = false }: { refObj: SdlcRef; inPane?: boolean }) {
  const { id } = refObj
  const { entity, err } = useWatchedLoop(id)
  const place = useOpenBeside()
  const { status, kind, meta, title, href, vm, matrix } = loopView(entity, refObj)
  const Icon = meta.icon
  const shown = entity ? shownCycle(entity.status, entity.total_cycles) : 0
  const elapsed = entity?.elapsed_seconds ?? 0
  const facts = [
    meta.noun,
    matrix?.unit === 'cycles' ? vm?.progressLabel : '',
    entity && shown > 0 ? cycleLabel(entity.status, entity.total_cycles, entity.max_cycles) : '',
    elapsed > 0 ? fmtE(elapsed) : '',
    entity ? `${agentCount(entity)} agent${agentCount(entity) === 1 ? '' : 's'}` : '',
  ].filter(Boolean)
  const attention = entity ? loopAttention(entity, status) : null
  const isPolling = !entity && !err

  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate" role="group" aria-label={`${title}, ${meta.noun}`}
      className="my-1.5 flex flex-col gap-1.5 border border-outline-variant/40 bg-surface-low/40 px-3 py-2" style={{ borderRadius: 'var(--radius-md)' }}>
      <div className="flex min-w-0 items-center gap-2">
        <Icon size={15} aria-hidden className="shrink-0 text-primary" />
        <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(600)}>{title}</span>
        <span data-type="caption" className="shrink-0 rounded-pill px-2 py-0.5" style={loopStatusTone(status)}>
          {isPolling ? <Loader2 size={10} aria-label="Reading the loop" className="inline animate-spin" /> : loopStatusLabel(status)}
        </span>
      </div>
      <p data-type="caption" className="truncate text-on-surface-low">{facts.join(' · ')}</p>

      {matrix && matrix.cells.length > 0 && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <LoopMatrix model={matrix} />
          <span data-type="caption" className="text-on-surface-low">{matrixSummary(matrix)}</span>
        </div>
      )}

      {err && (
        <p data-type="caption" className="flex items-center gap-1.5 text-on-surface-low">
          <AlertTriangle size={11} aria-hidden className="text-warn" /> {err}
        </p>
      )}

      {/* What the run needs from you, so you don't have to open it to learn why it parked. */}
      {attention && (
        <div data-type="caption" className="rounded-sm px-2 py-1.5"
          style={{ background: attention.tone === 'info'
            ? 'color-mix(in srgb, var(--color-info) 9%, transparent)'
            : 'color-mix(in srgb, var(--color-warn) 9%, transparent)' }}>
          <span className="inline-flex items-center gap-1.5"
            style={withWeight({ color: attention.tone === 'info' ? 'var(--color-info)' : 'var(--color-warn)' }, 600)}>
            {attention.tone === 'info' ? <HelpCircle size={11} aria-hidden /> : <AlertTriangle size={11} aria-hidden />} {attention.label}
          </span>
          <p className="whitespace-pre-wrap text-on-surface-var line-clamp-2">{attention.text}</p>
        </div>
      )}

      <div className="flex items-center gap-m">
        {place && !inPane && (
          <TextLink onClick={() => place.open(`loop:${id}`)} size="xs" icon={PanelRight} iconSize={12}
            aria-label={`Open ${title}'s details beside the conversation`}>
            Details
          </TextLink>
        )}
        <TextLink href={href} size="xs" icon={ArrowUpRight} iconPosition="trailing" iconSize={12}>
          {`Open ${kind === 'code' ? 'in Code' : `in ${meta.noun}`}`}
        </TextLink>
      </div>
    </motion.div>
  )
}
