/**
 * #411 — launching from Plan Review must not destroy the kind_config fields this screen never shows.
 *
 * `kind_config` is ONE json column holding every kind-specific field, and this screen renders the
 * five or six goal-shaped ones. It used to send exactly those, with the rest CONDITIONALLY OMITTED,
 * and the server wrote that object as the whole column — so a research loop reached Launch and lost
 * its subtopics, output template and manner, primary deliverable, breadth/depth budget and
 * granularity dial, at the one click a user cannot undo. Research routes through this goal-shaped
 * screen: `LoopsSection.tsx` excludes only `design`.
 *
 * The write is a PATCH the server merges over the stored config now, which turns omission into
 * "keep" — so the screen has to say what it OWNS out loud. Two rules, both pinned here:
 *
 *   1. the write carries no key that isn't the screen's own (it cannot invent or echo a research
 *      field), and
 *   2. an owned key is never silently absent — absence would now read as "keep this forever".
 *
 * 🪤 The screen CANNOT fix this by spreading its own copy of the loop back into the write, which is
 * the obvious shape and the one the report suggested. `store.get_redacted` passes kind_config through
 * `files._redact_value`, so the config this component holds is a REDACTED view; echoing it back would
 * persist redaction placeholders over the user's real text. The merge belongs on the server
 * (`loop_routes._merge_kind_config`); the browser's only job is to be honest about which keys are its
 * own.
 *
 * 🪤 `verify_command` is the clear with teeth: `instrument.py` resolves it as the reproduce anchor
 * without re-reading `goal_type`. Under merge semantics, omitting it while switching a goal away from
 * `verifiable` would leave the abandoned command running against every cycle — so the open-ended path
 * must send `verify_command: null`, not nothing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Loop } from '../../lib/api'
import type { LoopDraft } from './loopDraft'

/** The keys THIS screen authors. Nothing outside the set may appear in the launch write, and
 *  nothing inside it may be missing. */
const OWNED = ['goal_type', 'sub_goals', 'execution_plan', 'verify_command', 'grill_phases', 'phase_answers']

const loopWith = (kind_config: Record<string, unknown>) =>
  ({
    id: 'L1',
    name: 'ZZ research',
    kind: 'research',
    task: 'research the competitive landscape for on-device agents',
    status: 'review',
    granularity: 'balanced',
    attended: true,
    kind_config,
  }) as unknown as Loop

const draft: LoopDraft = {
  loopId: 'L1',
  // No execution plan and no clarifying questions → the walk is overview · capabilities · launch.
  classification: { kind: 'research', execution: 'solo', kind_config: {} },
  rigor: 'minimal',
  agent: 'a',
  model: 'm',
  granularity: 'balanced',
  attended: true,
}

/** Drive the REAL screen to its Launch step, press Launch, and return the PUT body. */
async function launchAndCaptureBody(storedConfig: Record<string, unknown>) {
  const updateULoop = vi.fn().mockResolvedValue(loopWith(storedConfig))
  vi.doMock('./useRunStream', () => ({
    useRunStream: () => ({ connected: false }),
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        uLoop: () => Promise.resolve(loopWith(storedConfig)),
        savedAgents: () => Promise.resolve([]),
        skills: () => Promise.resolve([]),
        updateULoop,
        uLoopAction: () => Promise.resolve(undefined),
      },
    }
  })
  const { LoopPlanReview } = await import('./LoopPlanReview')
  render(<LoopPlanReview draft={draft} onLaunched={() => {}} onBack={() => {}} />)
  // The overview only renders once the loop fetch resolves.
  await waitFor(() => expect(screen.getByText('Step 1 / 3')).toBeTruthy())
  // overview → capabilities → launch: the footer's forward button is labelled by destination.
  await userEvent.click(screen.getByRole('button', { name: /Capabilities/ }))
  await userEvent.click(screen.getByRole('button', { name: /Continue/ }))
  await userEvent.click(screen.getByRole('button', { name: /^Launch$/ }))
  await waitFor(() => expect(updateULoop).toHaveBeenCalled())
  return updateULoop.mock.calls[0][1] as { kind_config: Record<string, unknown> }
}

describe('the launch write declares exactly the keys this screen owns', () => {
  beforeEach(() => vi.resetModules())
  afterEach(() => { cleanup(); vi.doUnmock('./useRunStream'); vi.doUnmock('../../lib/api') })

  it('sends every owned key, so no omission can read as "keep this forever"', async () => {
    const body = await launchAndCaptureBody({
      goal_type: 'open_ended',
      subtopics: ['zzS1'],
      output_template: 'ZZ TEMPLATE',
      primary_deliverable: 'ZZREPORT.md',
    })
    expect(Object.keys(body.kind_config).sort()).toEqual([...OWNED].sort())
  })

  it('clears an owned key it no longer holds with an explicit null', async () => {
    // The goal is open-ended here, so the screen holds no verify command — and must SAY so.
    const body = await launchAndCaptureBody({ goal_type: 'open_ended', verify_command: 'make test' })
    expect(body.kind_config.verify_command).toBeNull()
    // Same for the phase plan and the two guided-decomposition keys: not held, so explicitly cleared.
    expect(body.kind_config.execution_plan).toBeNull()
    expect(body.kind_config.grill_phases).toBeNull()
    expect(body.kind_config.phase_answers).toBeNull()
  })

  it('never writes a field it does not render, so it cannot invent or echo one', async () => {
    // It holds a REDACTED view of these; writing them back is the corruption path, and writing
    // them not at all is what lets the server's merge preserve the real values.
    const body = await launchAndCaptureBody({
      goal_type: 'open_ended',
      subtopics: ['zzS1', 'zzS2'],
      output_manner: 'ZZ MANNER',
      breadth: 5,
      depth: 4,
      granularity: 'forever',
    })
    for (const foreign of ['subtopics', 'output_manner', 'breadth', 'depth', 'granularity']) {
      expect(foreign in body.kind_config, `${foreign} is not this screen's to write`).toBe(false)
    }
  })

  it('still carries the two keys the screen really does author', async () => {
    // The vacuity floor: a write that sent an EMPTY kind_config would satisfy the rule above.
    const body = await launchAndCaptureBody({ goal_type: 'open_ended', sub_goals: ['a', 'b'] })
    expect(body.kind_config.goal_type).toBe('open_ended')
    expect(body.kind_config.sub_goals).toEqual(['a', 'b'])
  })
})
