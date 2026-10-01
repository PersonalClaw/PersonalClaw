import { CheckCircle2, HelpCircle, ListChecks, XCircle } from 'lucide-react'
import type { CodeProject, LoopVerdict, StageGateCriterion } from '../../lib/api'
import { withWeight } from '../../design/fontWeight'
import { ENDED_LOOP_STATUSES } from '../../lib/loopStatus'
import { phaseKey } from '../loops/loopPhases'

const SAID: Record<StageGateCriterion['verdict'], string> = {
  pass: 'Met',
  fail: 'Not met',
  cant_tell: 'Can’t tell from the record',
}

type GateView = Pick<CodeProject, 'verdicts' | 'stage_plan' | 'stage_status'> & { status?: CodeProject['status'] }

/** The stage now at work (the first not done) and its gate's last evaluation, or null when that
 *  stage has not been judged yet. A gate that passes moves the loop on, so the evaluation shown is
 *  the one holding the stage. */
export function heldAtGate(p: GateView): { title: string; gate: LoopVerdict & { criteria: StageGateCriterion[] } } | null {
  const stage = (p.stage_plan ?? []).find((s) => (p.stage_status?.[phaseKey(s)] ?? 'pending') !== 'done')
  if (!stage) return null
  const key = phaseKey(stage)
  const mine = (p.verdicts ?? []).filter((v) => v.gate === 'stage' && v.stage === key && Array.isArray(v.criteria))
  const gate = mine[mine.length - 1]
  if (!gate || gate.passed || !gate.criteria?.length) return null
  return { title: stage.title || key, gate: gate as LoopVerdict & { criteria: StageGateCriterion[] } }
}

/** What the stage's gate decided the last time it judged the stage: each exit criterion met, not
 *  met, or not answerable from the loop's records, with the judge's reason. A stage held at its
 *  gate, or Blocked by it, says here what to steer or relax. */
export function StageGateVerdict({ project }: { project: GateView }) {
  const held = heldAtGate(project)
  if (!held) return null
  const { title, gate } = held
  return (
    <section role="region" aria-label={`Exit criteria of “${title}”`} data-type="body-s" className="mb-s rounded-lg border border-outline-variant/40 p-s">
      <div className="mb-xs flex items-start gap-s text-on-surface" style={withWeight({}, 550)}>
        <ListChecks size={14} className="mt-xs shrink-0" aria-hidden />
        <span>
          “{title}” {ENDED_LOOP_STATUSES.has(project.status ?? '') ? 'did not clear its gate' : 'is held at its gate'}
          {typeof gate.cycle === 'number' && (
            <span data-type="caption" className="text-on-surface-low" style={withWeight({}, 400)}> · judged at cycle {gate.cycle}</span>
          )}
        </span>
      </div>
      <ul className="space-y-xs">
        {gate.criteria.map((c, i) => {
          const Icon = c.verdict === 'pass' ? CheckCircle2 : c.verdict === 'fail' ? XCircle : HelpCircle
          const tone = c.verdict === 'pass' ? 'text-ok' : c.verdict === 'fail' ? 'text-danger' : 'text-on-surface-low'
          return (
            <li key={`${i}-${c.criterion}`} className="flex items-start gap-s">
              <Icon size={13} className={`mt-xs shrink-0 ${tone}`} aria-hidden />
              <span className="text-on-surface-var">
                <span className="text-on-surface">{SAID[c.verdict] ?? SAID.cant_tell}:</span> {c.criterion}
                {c.reason && <span className="text-on-surface-low"> ({c.reason})</span>}
              </span>
            </li>
          )
        })}
      </ul>
      {gate.note && <p data-type="caption" className="mt-xs text-on-surface-low">{gate.note}</p>}
    </section>
  )
}
