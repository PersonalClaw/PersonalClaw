/** A Code loop held at a stage's gate says, criterion by criterion, what its gate decided and why.
 *
 *  A stage whose three tasks were all done went Blocked with nothing on the page naming the
 *  criterion that failed: the gate answered one word for the whole stage and recorded nothing. Each
 *  evaluation now records a verdict per exit criterion (pass, fail or can't tell, with the judge's
 *  reason) on the loop's ledger, and the loop's page shows the last one for the stage at work.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import type { CodeProject, LoopVerdict } from '../../lib/api'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, approvals: () => Promise.resolve([]) } }
})
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...actual, useChatSocket: () => {} }
})

const { StageGateVerdict, heldAtGate } = await import('./StageGateVerdict')
const { ProjectFooter } = await import('./CodeCockpitPage')

afterEach(cleanup)

const plan = [
  { stage: 'implementation', title: 'Implementation', exit_criteria: [] },
  { stage: 'verification', title: 'Verification', exit_criteria: [] },
]
const held: LoopVerdict = {
  cycle: 7, gate: 'stage', stage: 'implementation', passed: false, verdict: 'REJECT',
  done: false, marginal_value: 0, quality_score: 0, regressed: false,
  done_reason: 'Not met: “The test suite passes” (the last run shows 1 failed).',
  criteria: [
    { criterion: 'Every task of the stage is done', verdict: 'pass', reason: 'all three tasks are done' },
    { criterion: 'The test suite passes', verdict: 'fail', reason: 'the last run shows 1 failed' },
    { criterion: 'The task output quotes the failing assertion', verdict: 'cant_tell', reason: 'not in the record' },
  ],
}

function project(over: Partial<CodeProject> = {}): CodeProject {
  return {
    id: 'l1', name: 'Fix the digest', status: 'running', kind: 'code',
    stage_plan: plan, stage_status: { implementation: 'active' },
    verdicts: [held], total_cycles: 7, max_cycles: 30,
    ...over,
  } as unknown as CodeProject
}

describe('the page names what the gate decided', () => {
  it('🔴 each criterion: met, not met, or not in the record, with the reason', () => {
    render(<StageGateVerdict project={project()} />)
    const region = screen.getByRole('region', { name: 'Exit criteria of “Implementation”' })
    const rows = within(region).getAllByRole('listitem').map((li) => li.textContent)
    expect(rows).toEqual([
      'Met: Every task of the stage is done (all three tasks are done)',
      'Not met: The test suite passes (the last run shows 1 failed)',
      'Can’t tell from the record: The task output quotes the failing assertion (not in the record)',
    ])
    expect(region.textContent).toContain('“Implementation” is held at its gate')
    expect(region.textContent).toContain('judged at cycle 7')
  })

  it('🔴 is on the Code cockpit for a Blocked loop, beside the message that names the criterion', () => {
    const blocked = project({ status: 'blocked', error_message: held.done_reason })
    render(<ProjectFooter project={blocked} gateFail={null} stalled={null} onNudged={() => {}} />)
    expect(screen.getByRole('region', { name: 'Exit criteria of “Implementation”' })).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('Not met: “The test suite passes”')
  })

  it('a run that ended says the stage did not clear its gate', () => {
    render(<StageGateVerdict project={project({ status: 'complete' })} />)
    expect(screen.getByRole('region').textContent).toContain('“Implementation” did not clear its gate')
  })

  it('shows the latest evaluation of the stage at work, never an earlier stage’s', () => {
    const passedEarlier: LoopVerdict = { ...held, cycle: 9, passed: true, criteria: held.criteria!.map((c) => ({ ...c, verdict: 'pass' as const })) }
    const verification: LoopVerdict = { ...held, cycle: 10, stage: 'verification', criteria: [{ criterion: 'The full suite passes', verdict: 'fail', reason: '2 failed' }] }
    const moved = project({ stage_status: { implementation: 'done', verification: 'active' }, verdicts: [held, passedEarlier, verification] })
    expect(heldAtGate(moved)?.title).toBe('Verification')
    expect(heldAtGate(moved)?.gate.criteria).toEqual(verification.criteria)
  })

  it('🔴 says what the gate looked at, and which criterion is held from outside the stage', () => {
    const looked: LoopVerdict = {
      ...held,
      criteria: [
        { criterion: 'The fix is in', verdict: 'pass', reason: 'the diff shows it' },
        { criterion: 'ruff reports no findings', verdict: 'fail', outside: true, reason: '5 findings the change did not touch' },
      ],
      observed: ['the stage’s 2 tasks, all done', 'the changes to 2 files in the workspace: digest.py, test_digest.py'],
    }
    render(<StageGateVerdict project={project({ verdicts: [looked] })} />)
    const region = screen.getByRole('region', { name: 'Exit criteria of “Implementation”' })
    const rows = within(region).getAllByRole('listitem').map((li) => li.textContent)
    expect(rows[1]).toBe('Not met, for a reason outside this stage’s work: ruff reports no findings (5 findings the change did not touch)')
    expect(region.textContent).toContain(
      'It looked at the stage’s 2 tasks, all done; the changes to 2 files in the workspace: digest.py, test_digest.py.',
    )
  })

  it('a stage not judged yet, or whose last evaluation passed, shows nothing', () => {
    expect(heldAtGate(project({ verdicts: [] }))).toBeNull()
    expect(heldAtGate(project({ verdicts: [{ ...held, passed: true }] }))).toBeNull()
    const scored: LoopVerdict = { cycle: 7, stage: 'implementation', done: false, marginal_value: 0.4, quality_score: 3, regressed: false }
    expect(heldAtGate(project({ verdicts: [scored] })), 'a stage-metric verdict is not a gate evaluation').toBeNull()
    const { container } = render(<StageGateVerdict project={project({ status: 'complete', stage_status: { implementation: 'done', verification: 'done' } })} />)
    expect(container.innerHTML).toBe('')
  })
})
