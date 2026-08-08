import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { LayoutGrid, GitBranch, Braces, Loader2 } from 'lucide-react'
import { Segmented } from '../../ui/Segmented'
import { DagView } from '../tasks/DagView'
import { fvs } from '../../design/fontWeight'
import { listItemEnter } from '../../design/motion'
import { reparseBuffer, type PlanDraft } from './planStream'
import { resolvePlanNames, type PlanNames, type NamedPlan } from './planNaming'
import { layoutPlanDag } from './planGraph'

/** The streaming multi-view plan review (UNIVERSAL-PLANNING UP-R7, WF2UNI-10).
 *
 *  The plan spec arrives in chunks; this renders it progressively across THREE synchronized
 *  views — plain-English proposal cards, a read-only dependency graph, and the raw JSON — all
 *  fed by ONE parse of the growing buffer so they can never disagree about which step exists.
 *  An in-flight step shimmers until the plan closes. Names come from the small-model naming call
 *  when present and a deterministic fallback otherwise, so a step never shows as a raw id and the
 *  plan never renders untitled.
 *
 *  The parse/layout/naming are all pure modules (planStream / planGraph / planNaming), each unit-
 *  tested; this component is the view chrome + the buffer→draft plumbing over them. */
export function PlanStreamReview({ buffer, complete, names, goal }: {
  /** The accumulated plan JSON so far (grown by the caller as chunks arrive). */
  buffer: string
  /** True once the final chunk has landed — stops the shimmer, marks the parse authoritative. */
  complete: boolean
  /** The streamed {title, description, labels} from the naming call (null until/if it arrives). */
  names?: PlanNames | null
  /** The loop's goal text — the deterministic title's best source when the plan carries none. */
  goal?: string
}) {
  const [view, setView] = useState<'cards' | 'graph' | 'json'>('cards')

  // ONE parse feeds every view. A half-arrived chunk keeps the last good draft (reparseBuffer),
  // so a view never blanks mid-stream.
  const draft: PlanDraft = useMemo(
    () => reparseBuffer(buffer, null, { complete }).draft,
    [buffer, complete],
  )
  const named: NamedPlan = useMemo(
    () => resolvePlanNames(draft, names, goal ?? ''),
    [draft, names, goal],
  )
  const dag = useMemo(
    () => layoutPlanDag(draft, (s) => named.labels[s.id] ?? s.id),
    [draft, named],
  )

  const streaming = !complete
  return (
    <div className="flex flex-col gap-m">
      {/* Title + description — from the naming call, or its deterministic fallback. */}
      <div className="flex items-start justify-between gap-m">
        <div className="min-w-0 flex flex-col gap-0.5">
          <h2 data-type="headline-s" className={`text-on-surface ${streaming && !names?.title ? 'text-shimmer' : ''}`}>{named.title}</h2>
          <p className="text-on-surface-var text-[0.8125rem]">{named.description}</p>
        </div>
        {streaming && (
          <span className="shrink-0 inline-flex items-center gap-1.5 text-on-surface-low text-[0.75rem]">
            <Loader2 size={13} className="animate-spin text-primary" /> Planning…
          </span>
        )}
      </div>

      {/* The three synchronized views — one parse behind all of them. */}
      <div className="flex items-center justify-between">
        <span className="text-on-surface-low text-[0.75rem] tabular-nums">
          {draft.steps.length} step{draft.steps.length === 1 ? '' : 's'}
        </span>
        <Segmented ariaLabel="Plan view" value={view} onChange={(v) => setView(v as typeof view)}
          size="sm"
          options={[
            { key: 'cards', label: 'Proposal', icon: LayoutGrid },
            { key: 'graph', label: 'Graph', icon: GitBranch },
            { key: 'json', label: 'JSON', icon: Braces },
          ]} />
      </div>

      {view === 'cards' ? (
        <ProposalCards draft={draft} labels={named.labels} />
      ) : view === 'graph' ? (
        dag.nodes.length > 0 ? (
          <div className="overflow-auto rounded-lg bg-surface-high p-s">
            <DagView nodes={dag.nodes} edges={dag.edges} width={dag.width} height={dag.height} />
          </div>
        ) : (
          <p className="text-on-surface-low text-[0.8125rem] px-m py-l">Waiting for the first step…</p>
        )
      ) : (
        // JSON is authoritative — show the raw growing buffer verbatim (not the reparse), so a
        // reviewer sees exactly what the planner emitted, malformed tail and all.
        <pre className="overflow-x-auto rounded-lg bg-surface-low px-m py-2 text-[0.8125rem] leading-relaxed font-mono text-on-surface-var whitespace-pre-wrap break-words">
          {buffer.trim() || '{ }'}
        </pre>
      )}
    </div>
  )
}

/** Plain-English per-step proposal cards. An in-flight (pending) step shimmers until the plan
 *  closes — the streaming cue the plan review owes so a still-arriving step doesn't read as done. */
function ProposalCards({ draft, labels }: { draft: PlanDraft; labels: Record<string, string> }) {
  if (draft.steps.length === 0) {
    return <p className="text-on-surface-low text-[0.8125rem] px-m py-l">Waiting for the first step…</p>
  }
  return (
    <div className="flex flex-col gap-1.5">
      {draft.steps.map((s, i) => (
        <motion.div key={s.id} variants={listItemEnter} initial="initial" animate="animate"
          className={`rounded-lg bg-surface-container px-m py-2.5 flex flex-col gap-0.5 ${s.pending ? 'ring-1 ring-primary/30' : ''}`}>
          <div className="flex items-center gap-s">
            <span className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.75rem] tabular-nums">{i + 1}</span>
            <span className={`flex-1 min-w-0 truncate text-on-surface text-[0.8125rem] ${s.pending ? 'text-shimmer' : ''}`} style={fvs(550)}>
              {labels[s.id] ?? s.id}
            </span>
            {s.role && <span className="shrink-0 text-on-surface-low text-[0.75rem]">{s.role}</span>}
          </div>
          {s.target && <span className="pl-7 text-on-surface-var text-[0.8125rem]">{s.target}</span>}
        </motion.div>
      ))}
    </div>
  )
}
