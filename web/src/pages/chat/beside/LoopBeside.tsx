import { ArrowUpRight } from 'lucide-react'
import { api, type LoopReport } from '../../../lib/api'
import { loopActionSources, loopStatusLabel } from '../../../lib/loopStatus'
import { isRunBackedKind, loopModeLabel, loopModeMeaning } from '../../../lib/loopMode'
import { reportActionFailure } from '../../../app/reportingWrite'
import { TextLink } from '../../../ui/TextLink'
import { LoopProgressStrip, type StripAction } from '../../loops/LoopProgressStrip'
import { LoopMatrix, matrixSummary } from '../../loops/LoopMatrix'
import { RunProgress } from '../../loops/RunProgress'
import { agentCount, fmtE, loopAttention, loopView } from '../SdlcProgressCard'
import { useWatchedLoop } from '../loopWatch'

/** How many of its latest cycles the tile lists. */
const RECENT_CYCLES = 5

/** A loop the chat started, beside the conversation (#3824's detail tile): what it is and how it
 *  stands, its strip (the cycle it is on, its Start, Pause or Resume, its two-step Stop and how it
 *  reports, each offered exactly when its route accepts it), its phase matrix with every state in
 *  words, its steps and its latest cycles. Read the same way as its card in the thread
 *  (`useWatchedLoop`), so the two agree. Closing the tile, or the chat, stops nothing: only its
 *  own Stop does. */
export function LoopBeside({ id }: { id: string }) {
  const { entity, err, put } = useWatchedLoop(id)
  const view = loopView(entity, { kind: 'loop', id, created: false })
  if (!entity) {
    return <p data-type="body-s" className="px-l py-m text-on-surface-low">{err || 'Reading the loop…'}</p>
  }
  const { status, kind, meta, title, href, vm, matrix } = view
  const act = async (a: StripAction) => {
    const next = await api.uLoopAction(id, a).catch((e) => { reportActionFailure(`${a} ${title}`)(e); return null })
    if (next) put(next)
  }
  const changeReport = async (report: LoopReport) => {
    const next = await api.updateULoop(id, { report }).catch((e) => { reportActionFailure('change how this loop reports')(e); return null })
    if (next) put(next)
  }
  const attention = loopAttention(entity, status)
  const steps = (vm?.steps ?? []).filter((s) => s.state === 'untracked')
  const recent = (entity.findings ?? []).slice(-RECENT_CYCLES).reverse()
  const elapsed = entity.elapsed_seconds ?? 0
  return (
    <div tabIndex={0} role="group" aria-label="The loop, beside the conversation"
      className="flex min-h-0 flex-1 flex-col gap-m overflow-auto px-l py-m focus-visible:-outline-offset-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs" data-type="body-s">
        <dt className="text-on-surface-low">Kind</dt><dd className="text-on-surface-var">{meta.noun}</dd>
        <dt className="text-on-surface-low">State</dt><dd className={status === 'failed' ? 'text-danger' : 'text-on-surface-var'}>{loopStatusLabel(status)}</dd>
        <dt className="text-on-surface-low">Running for</dt><dd className="text-on-surface-var">{elapsed > 0 ? fmtE(elapsed) : 'Not started'}</dd>
        <dt className="text-on-surface-low">Agents</dt><dd className="text-on-surface-var">{agentCount(entity)}</dd>
      </dl>

      <LoopProgressStrip status={entity.status} totalCycles={entity.total_cycles} maxCycles={entity.max_cycles}
        onAction={act} sources={loopActionSources(entity)} mode={{ label: loopModeLabel(entity.attended), meaning: loopModeMeaning(entity.attended, kind) }}
        report={entity.report} onReport={isRunBackedKind(kind) || entity.run_id ? undefined : changeReport} />

      {attention && (
        <div data-type="body-s" className="flex flex-col gap-xs border-l-2 pl-s" style={{ borderColor: attention.tone === 'info' ? 'var(--color-info)' : 'var(--color-warn)' }}>
          <span className="text-on-surface">{attention.label}</span>
          {attention.text && <p className="whitespace-pre-wrap text-on-surface-var">{attention.text}</p>}
          {attention.sub && <p data-type="caption" className="whitespace-pre-wrap text-on-surface-low">{attention.sub}</p>}
        </div>
      )}

      {matrix && matrix.cells.length > 0 && (
        <div role="group" aria-label={matrix.unit === 'stages' ? 'Its stages' : 'Its cycles'} className="flex flex-col gap-xs">
          <div className="flex flex-wrap items-center gap-x-s gap-y-xs">
            <LoopMatrix model={matrix} />
            <span data-type="caption" className="text-on-surface-low">{matrixSummary(matrix)}</span>
          </div>
          {vm && <RunProgress vm={vm} />}
          {matrix.unit === 'stages' && (
            <ul className="flex flex-col gap-0.5" data-type="body-s">
              {matrix.cells.map((c) => (
                <li key={c.label} className="flex min-w-0 gap-s">
                  <span className="min-w-0 flex-1 truncate text-on-surface-var">{c.label}</span>
                  <span className="shrink-0 text-on-surface-low">{c.state === 'pending' ? 'to go' : c.state}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {steps.length > 0 && (
        <div role="group" aria-label={kind === 'goal' ? 'Its sub-goals' : 'Its stages'} className="flex flex-col gap-xs">
          <h3 data-type="label-s" className="text-on-surface-var">{kind === 'goal' ? 'Sub-goals' : 'Stages'}</h3>
          <ul className="flex list-disc flex-col gap-0.5 pl-l" data-type="body-s">
            {steps.map((s) => <li key={s.key} className="text-on-surface-var">{s.label}</li>)}
          </ul>
        </div>
      )}

      {recent.length > 0 && (
        <div role="group" aria-label="Its latest cycles" className="flex flex-col gap-xs">
          <h3 data-type="label-s" className="text-on-surface-var">Latest cycles</h3>
          <ol className="flex flex-col gap-xs" data-type="body-s">
            {recent.map((f) => (
              <li key={`${f.cycle}-${f.ts}`} className="flex min-w-0 gap-s">
                <span className="shrink-0 tabular-nums text-on-surface-low">{`Cycle ${f.cycle}`}</span>
                <span className="min-w-0 text-on-surface-var">{f.summary || f.key_insight || 'No summary'}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <TextLink href={href} size="xs" icon={ArrowUpRight} iconPosition="trailing" iconSize={12}>
        {`Open ${title}'s page`}
      </TextLink>
    </div>
  )
}
