import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api, type CodeStage, type Loop } from '../../lib/api'
import { HELD_CHANGE_REASON } from '../../lib/staleWrite'
import type { LoopDraft } from './loopDraft'
import type { CodeDraft } from '../code/codeDraft'

// ── A loop's spec is written only over the copy the page read ──────────────────────────────────────
//
// Four surfaces write a loop's spec from the copy they read: the two Plan Reviews (Launch writes the
// plan, the capability lists and the screen's `kind_config` keys, then starts the loop), and the two
// design token editors (each override replaced `token_overrides` whole). A write that landed in
// between — a rename or the autopilot toggle in another tab, an override set by the planning preview —
// was put back without a word, and the review's failed write was even swallowed before the loop
// started on the OLD spec. The gateway now refuses a spec write whose base is stale (`409 stale_write`,
// `loop_routes.api_loop_update`); these pin each page's half.

vi.mock('./useRunStream', () => ({
  useRunStream: () => ({ connected: false }),
  RUN_LIFECYCLE: [] as string[],
}))
// The folder picker is its own surface; here it only has to hand back a folder.
vi.mock('../code/WorkspacePicker', () => ({
  WorkspacePicker: ({ onPick }: { onPick: (dir: string) => void }) => (
    <button type="button" onClick={() => onPick('/work/repo')}>Use /work/repo</button>
  ),
}))
vi.mock('../../ui/dialog', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../ui/dialog')>()),
  promptInput: vi.fn(() => Promise.resolve('12px')),
}))

import { LoopPlanReview } from './LoopPlanReview'
import { CodePlanReview } from '../code/CodePlanReview'
import { DesignCockpitPage } from './DesignCockpitPage'
import { DesignStepPreview } from './DesignStepPreview'

function staleWrite() {
  return Object.assign(new Error('This write replaces the loop, which changed after the copy it was built from was read.'), { status: 409, code: 'stale_write' })
}

const loop = (over: Record<string, unknown>) => ({
  id: 'L1', name: 'ZZ research', kind: 'research', task: 'research the landscape for on-device agents',
  status: 'review', granularity: 'balanced', attended: true, execution: 'solo',
  skill_ids: [], workflow_ids: [], plan: [], kind_config: { goal_type: 'open_ended' },
  ...over,
}) as unknown as Loop

afterEach(() => { cleanup(); vi.restoreAllMocks(); sessionStorage.clear() })

/** Where a Plan Review is reached from — `LoopsSection.draftFromLoop`, whose classification is the
 *  stored loop's and carries no suggestions of its own. No phase plan and no questions: the walk is
 *  overview · capabilities · launch. */
const draft: LoopDraft = {
  loopId: 'L1', classification: { kind: 'research', execution: 'solo', kind_config: {} } as LoopDraft['classification'],
  rigor: 'minimal', agent: 'a', model: 'm', granularity: 'balanced', attended: true,
}

/** The whole walk to the Launch step, as a user takes it, with `edit` made on the overview. */
async function walkToLaunch(onLaunched: (id: string) => void, edit?: () => void) {
  render(<LoopPlanReview draft={draft} onLaunched={onLaunched} onBack={() => {}} />)
  await waitFor(() => expect(screen.getByText('Step 1 / 3')).toBeTruthy())
  edit?.()
  fireEvent.click(screen.getByRole('button', { name: /Capabilities/ }))
  fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
}

const retitle = (to: string) => () => {
  fireEvent.click(screen.getByTitle('Edit title'))
  const field = screen.getByLabelText('Edit the plan title')
  fireEvent.change(field, { target: { value: to } })
  fireEvent.blur(field)
}

const addSubGoal = (text: string) => () => {
  const field = screen.getByLabelText('New sub-goal')
  fireEvent.change(field, { target: { value: text } })
  fireEvent.keyDown(field, { key: 'Enter' })
}

/** The refusal notice, once the stored copy it is about has been read back — until then nothing it
 *  offers can be judged. */
async function refusalRead(what: RegExp) {
  const alert = (await screen.findByText(what)).closest('[role="alert"]') as HTMLElement
  await waitFor(() => expect(within(alert).getByRole('button', { name: 'Review the difference' }).getAttribute('aria-disabled')).toBeNull())
  return alert
}

// ── the goal/general Plan Review ──────────────────────────────────────────────────────────────────

describe('the Plan Review launch', () => {
  const PAINTED = loop({ revision: 'L1r1' })
  // Stored by the time Launch is pressed: the loop was renamed in another tab.
  const STORED = loop({ name: 'Agents on device', revision: 'L1r2' })
  let saveULoopSpec: ReturnType<typeof vi.spyOn>
  let uLoopAction: ReturnType<typeof vi.spyOn>
  let onLaunched: ReturnType<typeof vi.fn<(id: string) => void>>

  beforeEach(() => {
    vi.spyOn(api, 'uLoop').mockResolvedValueOnce(PAINTED).mockResolvedValue(STORED)
    vi.spyOn(api, 'savedAgents').mockResolvedValue([])
    vi.spyOn(api, 'skills').mockResolvedValue([])
    saveULoopSpec = vi.spyOn(api, 'saveULoopSpec').mockImplementation((_id: string, body: Record<string, unknown>, base: string) =>
      base === 'L1r2' ? Promise.resolve({ ...STORED, ...body, revision: 'L1r3' } as Loop) : Promise.reject(staleWrite()))
    uLoopAction = vi.spyOn(api, 'uLoopAction').mockResolvedValue(STORED)
    onLaunched = vi.fn<(id: string) => void>()
  })

  async function pressLaunch() {
    await walkToLaunch(onLaunched)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })
  }

  it('a launch from a stale copy is refused with the notice, and NOTHING starts', async () => {
    await pressLaunch()
    const alert = await screen.findByText(/This loop changed elsewhere/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    expect(saveULoopSpec).toHaveBeenCalledTimes(1)
    const [id, body, base] = saveULoopSpec.mock.calls[0]
    expect([id, base]).toEqual(['L1', 'L1r1'])
    expect(body).toMatchObject({ name: 'ZZ research' })
    expect(uLoopAction, 'a refused spec write must not launch the loop on the stored spec').not.toHaveBeenCalled()
    expect(onLaunched).not.toHaveBeenCalled()
  })

  it('Reload and reapply puts the added sub-goal on top of the rename made elsewhere, then launches', async () => {
    await walkToLaunch(onLaunched, addSubGoal('benchmark on a Pixel'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })
    const alert = (await screen.findByText(/This loop changed elsewhere/)).closest('[role="alert"]') as HTMLElement
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(2))
    const [, body, base] = saveULoopSpec.mock.calls[1]
    expect(base).toBe('L1r2')
    expect(body).toMatchObject({
      name: 'Agents on device',
      plan: [{ title: 'benchmark on a Pixel' }],
      kind_config: { sub_goals: ['benchmark on a Pixel'] },
    })
    await waitFor(() => expect(uLoopAction).toHaveBeenCalledWith('L1', 'start'))
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith('L1'))
  })

  it('a spec write that FAILS says so and starts nothing — it used to be swallowed', async () => {
    saveULoopSpec.mockRejectedValue(new Error('verify_command is not allowed'))
    await pressLaunch()
    expect(await screen.findByText(/Couldn.t save the plan, so the loop was not launched: verify_command is not allowed/)).toBeTruthy()
    expect(uLoopAction).not.toHaveBeenCalled()
  })

  it('a launch from a current copy names the painted revision, then starts', async () => {
    saveULoopSpec.mockResolvedValue(PAINTED)
    await pressLaunch()
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith('L1'))
    expect(saveULoopSpec.mock.calls[0][2]).toBe('L1r1')
    expect(uLoopAction).toHaveBeenCalledWith('L1', 'start')
  })

  it('while the refused launch is held, its title cannot be edited — and it can once the change is settled', async () => {
    await pressLaunch()
    const alert = await refusalRead(/This loop changed elsewhere/)
    // The one editor the walk still shows on its last step. Reachable, and saying why it is off.
    const titled = screen.getByRole('button', { name: 'ZZ research' })
    expect(titled.getAttribute('aria-disabled'), 'a rename typed now would be left out of what Reapply launches').toBe('true')
    expect(titled.getAttribute('title')).toBe(`Edit title — ${HELD_CHANGE_REASON}`)
    fireEvent.click(titled)
    expect(screen.queryByLabelText('Edit the plan title'), 'pressing it opens nothing').toBeNull()
    const discard = within(alert).getByRole('button', { name: 'Discard my change' })
    expect(discard.matches(':disabled'), 'the choice itself stays open').toBe(false)
    await act(async () => { fireEvent.click(discard) })
    await waitFor(() => expect(screen.getByTitle('Edit title').getAttribute('aria-disabled')).toBeNull())
  })
})

// ── what the user did not touch is nobody's change ────────────────────────────────────────────────
//
// The base a launch names is diffed against the launch to find the user's change. It used to be the
// stored loop as it came, while the launch DERIVES its write — the plan from the sub-goals, a
// non-verifiable goal's check as `null`, an empty phase plan as `null` — so fields nobody touched read
// as the user's change. Any write elsewhere to one of them then made the refusal "can't be re-applied
// on its own": measured in the two-tab drive, a title-only launch over a re-plan offered no Reapply,
// and its review listed `verify_command "" → null` and `plan [] → [measure the p95]` as "Your change".

describe('the Plan Review launch over a change made elsewhere', () => {
  // The loop as the drive found it: sub-goals with no plan rows yet, and an empty check and phase plan
  // on an open-ended goal — each of which a launch writes in its own shape.
  const PAINTED = loop({
    plan: [], kind_config: { goal_type: 'open_ended', sub_goals: ['measure the p95'], verify_command: '', execution_plan: [] },
    revision: 'L1r1',
  })
  // Stored by the time Launch is pressed: the planner re-planned it — a second sub-goal, and a phase.
  const PHASE = { role: 'profiler', agent_name: '', target: 'find where the time goes', min_cycles: 2, phase_exit: 'a flame graph', skill_ids: [], workflow_ids: [] }
  const REPLANNED = loop({
    plan: [{ title: 'measure the p95' }, { title: 'find the slow query' }],
    kind_config: { goal_type: 'open_ended', sub_goals: ['measure the p95', 'find the slow query'], verify_command: '', execution_plan: [PHASE] },
    revision: 'L1r2',
  })
  let saveULoopSpec: ReturnType<typeof vi.spyOn>
  let uLoopAction: ReturnType<typeof vi.spyOn>
  let onLaunched: ReturnType<typeof vi.fn<(id: string) => void>>

  beforeEach(() => {
    vi.spyOn(api, 'uLoop').mockResolvedValueOnce(PAINTED).mockResolvedValue(REPLANNED)
    vi.spyOn(api, 'savedAgents').mockResolvedValue([])
    vi.spyOn(api, 'skills').mockResolvedValue([])
    saveULoopSpec = vi.spyOn(api, 'saveULoopSpec').mockImplementation((_id: string, body: Record<string, unknown>, base: string) =>
      base === 'L1r2' ? Promise.resolve({ ...REPLANNED, ...body, revision: 'L1r3' } as Loop) : Promise.reject(staleWrite()))
    uLoopAction = vi.spyOn(api, 'uLoopAction').mockResolvedValue(REPLANNED)
    onLaunched = vi.fn<(id: string) => void>()
  })

  it('a title-only launch re-applies over a re-plan: the rename on top, the re-plan kept', async () => {
    await walkToLaunch(onLaunched, retitle('P95 hunt'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })
    const alert = await refusalRead(/This loop changed elsewhere/)
    expect(within(alert).queryByText(/can.t be re-applied on its own/), 'only the title was changed here').toBeNull()
    await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Reload and reapply' })) })

    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(2))
    const [, body, base] = saveULoopSpec.mock.calls[1]
    expect(base).toBe('L1r2')
    expect(body).toMatchObject({
      name: 'P95 hunt',
      plan: [{ title: 'measure the p95' }, { title: 'find the slow query' }],
      kind_config: { sub_goals: ['measure the p95', 'find the slow query'], execution_plan: [PHASE], verify_command: null },
    })
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith('L1'))
  })

  it('an untouched launch over a re-plan has nothing of its own to put back: it launches what is stored', async () => {
    await walkToLaunch(onLaunched)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })
    const alert = await refusalRead(/This loop changed elsewhere/)
    expect(within(alert).queryByText(/can.t be re-applied on its own/), 'nothing was changed here at all').toBeNull()
    await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Reload and reapply' })) })

    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith('L1'))
    expect(saveULoopSpec, 'the stored spec already is the launch — no second write over it').toHaveBeenCalledTimes(1)
    expect(uLoopAction).toHaveBeenCalledWith('L1', 'start')
  })

  it('the capabilities stored on the loop are the ones picked, and an untouched launch keeps them', async () => {
    // The composer threads the planner's picks onto the loop at create. This screen is reached from the
    // stored loop, whose draft carries no suggestions — so the picks started EMPTY, and every launch
    // wrote `skill_ids: []` over the stored ones.
    const PICKED = loop({ skill_ids: ['web-research'], revision: 'L1r1' })
    vi.spyOn(api, 'uLoop').mockReset().mockResolvedValue(PICKED)
    vi.spyOn(api, 'skills').mockResolvedValue([{ key: 'web-research', name: 'Web research', description: 'search the web' }] as never)
    saveULoopSpec.mockResolvedValue(PICKED)
    render(<LoopPlanReview draft={draft} onLaunched={onLaunched} onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText('Step 1 / 3')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Capabilities/ }))
    expect(await screen.findByText(/1 selected\./), 'the stored pick is shown picked').toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })

    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(1))
    expect(saveULoopSpec.mock.calls[0][1]).toMatchObject({ skill_ids: ['web-research'] })
  })
})

// ── the Code Plan Review ──────────────────────────────────────────────────────────────────────────

describe('the Code Plan Review launch', () => {
  const STAGE = { stage: 'implementation', title: 'Build it', objective: 'write the code', exit_criteria: [], deliverable: '', task_list_name: 'Build it' }
  const project = (over: Record<string, unknown>) => loop({
    id: 'C1', name: 'Tidy the parser', kind: 'code', task: 'tidy the parser module',
    plan: [STAGE], autopilot: true, kind_config: { project_kind: 'greenfield', entry_stage: 'implementation' },
    ...over,
  })
  const PAINTED = project({ revision: 'C1r1' })
  // Stored by the time Launch is pressed: the cockpit's autopilot toggle, in another tab.
  const STORED = project({ autopilot: false, revision: 'C1r2' })
  const draft = { projectId: 'C1', classification: {}, rigor: 'minimal', attended: true } as unknown as CodeDraft
  let saveULoopSpec: ReturnType<typeof vi.spyOn>
  let uLoopAction: ReturnType<typeof vi.spyOn>
  let onLaunched: ReturnType<typeof vi.fn<(id: string) => void>>

  beforeEach(() => {
    vi.spyOn(api, 'uLoop').mockResolvedValueOnce(PAINTED).mockResolvedValue(STORED)
    vi.spyOn(api, 'skills').mockResolvedValue([])
    vi.spyOn(api, 'uLoopPlanSession').mockResolvedValue(null)
    saveULoopSpec = vi.spyOn(api, 'saveULoopSpec').mockImplementation((_id: string, body: Record<string, unknown>, base: string) =>
      base === 'C1r2' ? Promise.resolve({ ...STORED, ...body, revision: 'C1r3' } as Loop) : Promise.reject(staleWrite()))
    uLoopAction = vi.spyOn(api, 'uLoopAction').mockResolvedValue(STORED)
    onLaunched = vi.fn<(id: string) => void>()
  })

  async function pressLaunch() {
    render(<CodePlanReview draft={draft} onBack={() => {}} onLaunched={onLaunched} />)
    await waitFor(() => expect(screen.getByText('tidy the parser module')).toBeTruthy())
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })
  }

  it('a launch from a stale copy is refused with the notice, and NOTHING starts', async () => {
    await pressLaunch()
    const alert = await screen.findByText(/This project changed elsewhere/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    const [id, body, base] = saveULoopSpec.mock.calls[0]
    expect([id, base]).toEqual(['C1', 'C1r1'])
    expect(body).toMatchObject({ autopilot: true, plan: [expect.objectContaining({ title: 'Build it' })] })
    expect(uLoopAction).not.toHaveBeenCalled()
  })

  it('Reload and reapply puts the edited objective on top of the drive mode chosen elsewhere, then launches', async () => {
    render(<CodePlanReview draft={draft} onBack={() => {}} onLaunched={onLaunched} />)
    await waitFor(() => expect(screen.getByText('tidy the parser module')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('What this stage accomplishes…'), { target: { value: 'write the code, with tests' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })
    const alert = (await screen.findByText(/This project changed elsewhere/)).closest('[role="alert"]') as HTMLElement
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(2))
    const [, body, base] = saveULoopSpec.mock.calls[1]
    expect(base).toBe('C1r2')
    expect(body).toMatchObject({ autopilot: false, plan: [expect.objectContaining({ objective: 'write the code, with tests' })] })
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith('C1'))
  })

  it('a brownfield launch saves the reviewed plan after binding the folder — it used to skip it', async () => {
    const brown = project({ kind_config: { project_kind: 'brownfield', entry_stage: 'implementation' }, workspace_dir: '', revision: 'B1' })
    // The bind is the page's own write: it moves the revision, and nothing the launch writes moved with it.
    const bound = { ...brown, workspace_dir: '/work/repo', revision: 'B2' } as Loop
    vi.spyOn(api, 'uLoop').mockReset().mockResolvedValue(brown)
    const updateULoop = vi.spyOn(api, 'updateULoop').mockResolvedValue(bound)
    saveULoopSpec.mockImplementation((_id: string, body: Record<string, unknown>, base: string) =>
      base === 'B2' ? Promise.resolve({ ...bound, ...body, revision: 'B3' } as Loop) : Promise.reject(staleWrite()))
    render(<CodePlanReview draft={draft} onBack={() => {}} onLaunched={onLaunched} />)
    await waitFor(() => expect(screen.getByText('tidy the parser module')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Choose workspace & launch/ }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Use /work/repo' })) })

    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith('C1'))
    expect(updateULoop).toHaveBeenCalledWith('C1', { workspace_dir: '/work/repo' })
    expect(saveULoopSpec).toHaveBeenCalledTimes(1)
    expect(saveULoopSpec.mock.calls[0][2], 'based on the bound project, since only the folder moved').toBe('B2')
    expect(saveULoopSpec.mock.calls[0][1]).toMatchObject({ plan: [expect.objectContaining({ title: 'Build it' })] })
    // Saved, THEN started.
    expect(saveULoopSpec.mock.invocationCallOrder[0]).toBeLessThan(uLoopAction.mock.invocationCallOrder[0])
  })

  it('a drive-mode-only launch re-applies over a re-plan: the stages it never touched are not its change', async () => {
    // The launch cleans the stages it writes (`tasks` defaulted, each TaskList named) and the base used to
    // be the plan as stored — so every stage read as edited here, and any re-plan made it a conflict.
    const CHECK = { stage: 'verification', title: 'Check it', objective: 'run the suite', exit_criteria: ['green'], deliverable: '', task_list_name: 'Check it' }
    const REPLANNED = project({ plan: [STAGE, CHECK], revision: 'C1r2' })
    vi.spyOn(api, 'uLoop').mockReset().mockResolvedValueOnce(PAINTED).mockResolvedValue(REPLANNED)
    saveULoopSpec.mockImplementation((_id: string, body: Record<string, unknown>, base: string) =>
      base === 'C1r2' ? Promise.resolve({ ...REPLANNED, ...body, revision: 'C1r3' } as Loop) : Promise.reject(staleWrite()))
    render(<CodePlanReview draft={draft} onBack={() => {}} onLaunched={onLaunched} />)
    await waitFor(() => expect(screen.getByText('tidy the parser module')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /One-by-one/ }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Launch$/ })) })
    const alert = await refusalRead(/This project changed elsewhere/)
    expect(within(alert).queryByText(/can.t be re-applied on its own/), 'only the drive mode was changed here').toBeNull()
    await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Reload and reapply' })) })

    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(2))
    const [, body, base] = saveULoopSpec.mock.calls[1]
    expect(base).toBe('C1r2')
    expect(body).toMatchObject({ autopilot: false })
    expect((body as { plan: CodeStage[] }).plan.map((s) => s.title), 'the re-plan is kept').toEqual(['Build it', 'Check it'])
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith('C1'))
  })

  it('while the refused launch is held, the plan it was made from cannot be edited — and it can once settled', async () => {
    vi.spyOn(api, 'skills').mockResolvedValue([{ key: 'web-research', name: 'Web research', description: 'search the web' }] as never)
    await pressLaunch()
    const alert = await refusalRead(/This project changed elsewhere/)
    const editors = [
      screen.getByPlaceholderText('Stage title'),
      screen.getByPlaceholderText('What this stage accomplishes…'),
      screen.getByRole('button', { name: /^Add stage/ }),
      screen.getByRole('button', { name: /^Web research/ }),
      screen.getByRole('button', { name: /^Autopilot/ }),
      screen.getByRole('button', { name: /^One-by-one/ }),
    ]
    for (const el of editors) {
      expect(el.matches(':disabled'), `${el.outerHTML.slice(0, 80)}… — an edit made now would be left out of what Reapply launches`).toBe(true)
    }
    const discard = within(alert).getByRole('button', { name: 'Discard my change' })
    expect(discard.matches(':disabled'), 'the choice itself stays open').toBe(false)
    await act(async () => { fireEvent.click(discard) })
    await waitFor(() => expect(screen.getByPlaceholderText('Stage title').matches(':disabled')).toBe(false))
    expect(screen.getByRole('button', { name: /^One-by-one/ }).matches(':disabled')).toBe(false)
  })
})

// ── the design token editors ──────────────────────────────────────────────────────────────────────

const TOKENS = {
  resolved: {
    radius: { lg: '0.75rem' },
    typography: { family: { sans: 'Inter, sans-serif' }, size: {}, weight: {} },
    spacing: {}, shadow: {}, color: { semantic: {}, primitive: {} },
  },
}
const design = (overrides: Record<string, unknown>, revision: string) => loop({
  id: 'd1', kind: 'design', name: 'Northwind design system', task: 'Build a design system', status: 'planning',
  // `targets` is a key this editor never renders — and the REDACTED view of it is what the old write
  // echoed back beside the overrides.
  kind_config: { token_overrides: overrides, targets: ['web'] }, revision,
})
// Stored by the time the override is saved: the planning preview set a brand ramp.
const BRAND = { color: { primitive: { brand: { 500: '#3355ff' } } } }

describe('the design cockpit token editor', () => {
  let saveULoopSpec: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    vi.spyOn(api, 'uLoop').mockResolvedValueOnce(design({}, 'd1r1')).mockResolvedValue(design(BRAND, 'd1r2'))
    vi.spyOn(api, 'uLoopDesignTokens').mockResolvedValue(TOKENS as never)
    vi.spyOn(api, 'artifacts').mockResolvedValue([])
    saveULoopSpec = vi.spyOn(api, 'saveULoopSpec').mockImplementation((_id: string, _body: Record<string, unknown>, base: string) =>
      base === 'd1r2' ? Promise.resolve(design(BRAND, 'd1r3')) : Promise.reject(staleWrite()))
  })

  async function overrideRadius() {
    render(<DesignCockpitPage id="d1" onBack={() => {}} />)
    const tile = await screen.findByTitle('Override radius.lg (now 0.75rem)')
    await act(async () => { fireEvent.click(tile) })
  }

  it('an override from a stale copy is refused with the notice, and sends only the overrides', async () => {
    await overrideRadius()
    const alert = await screen.findByText(/This design system changed elsewhere/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    const [id, body, base] = saveULoopSpec.mock.calls[0]
    expect([id, base]).toEqual(['d1', 'd1r1'])
    // The write carries `token_overrides` alone — never the read's `kind_config` echoed back.
    expect(body).toEqual({ kind_config: { token_overrides: { radius: { lg: '12px' } } } })
    // While it is held, a second override cannot start and drop it unseen.
    expect(screen.getByTitle(/Override radius\.lg/).getAttribute('aria-disabled')).toBe('true')
  })

  it('Reload and reapply puts the same override onto what is stored, keeping the brand ramp', async () => {
    await overrideRadius()
    const alert = (await screen.findByText(/This design system changed elsewhere/)).closest('[role="alert"]') as HTMLElement
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(2))
    const [, body, base] = saveULoopSpec.mock.calls[1]
    expect(base).toBe('d1r2')
    expect(body).toEqual({ kind_config: { token_overrides: { ...BRAND, radius: { lg: '12px' } } } })
    await waitFor(() => expect(screen.queryByText(/changed elsewhere/)).toBeNull())
  })
})

describe('the planning walkthrough token preview', () => {
  let saveULoopSpec: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    vi.spyOn(api, 'uLoopDesignTokens').mockResolvedValue(TOKENS as never)
    // The read the edit is built on, then — after the write it raced lost — the stored copy.
    vi.spyOn(api, 'uLoop').mockResolvedValueOnce(design({}, 'd1r1')).mockResolvedValue(design(BRAND, 'd1r2'))
    saveULoopSpec = vi.spyOn(api, 'saveULoopSpec').mockImplementation((_id: string, _body: Record<string, unknown>, base: string) =>
      base === 'd1r2' ? Promise.resolve(design(BRAND, 'd1r3')) : Promise.reject(staleWrite()))
  })

  it('an override that loses a race is refused with the notice, and the reapply keeps the other write', async () => {
    render(<DesignStepPreview loopId="d1" stepKind="palette" overrides={{}} />)
    const tile = await screen.findByTitle('Override radius.lg (now 0.75rem)')
    await act(async () => { fireEvent.click(tile) })
    const alert = (await screen.findByText(/This design system changed elsewhere/)).closest('[role="alert"]') as HTMLElement
    expect(saveULoopSpec.mock.calls[0]).toEqual(['d1', { kind_config: { token_overrides: { radius: { lg: '12px' } } } }, 'd1r1'])

    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(2))
    expect(saveULoopSpec.mock.calls[1]).toEqual(['d1', { kind_config: { token_overrides: { ...BRAND, radius: { lg: '12px' } } } }, 'd1r2'])
  })

  it('an override from a current copy lands over the revision it was built on', async () => {
    saveULoopSpec.mockResolvedValue(design({ radius: { lg: '12px' } }, 'd1r5'))
    render(<DesignStepPreview loopId="d1" stepKind="palette" overrides={{}} />)
    const tile = await screen.findByTitle('Override radius.lg (now 0.75rem)')
    await act(async () => { fireEvent.click(tile) })
    await waitFor(() => expect(saveULoopSpec).toHaveBeenCalledTimes(1))
    expect(saveULoopSpec.mock.calls[0][2]).toBe('d1r1')
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })
})
