/** Plan Review's text fields are the shared text field, and each one is named (#3821, #3806).
 *
 *  Moving Plan Review into the pane (#5146) left four raw inputs in it only because the shared
 *  `TextInput` took no `onBlur` (a condition or a title commits when the field loses focus) or
 *  `spellCheck` (a shell command must not be spell-checked). It takes both now, and those fields
 *  use it. The fields that stay raw (a task's two fill-less fields, which share one row's box) were
 *  named only by their placeholder, which is not a name; each now says which task and stage it is.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

const h = vi.hoisted(() => ({
  loops: {
    P1: {
      id: 'P1', name: 'Feed refetch fix', kind: 'code', status: 'review', task: 'fix the feed refetch',
      plan: [{ stage: 'build', title: 'Build', exit_criteria: [], tasks: [{ title: 'Wire the retry', description: '' }] }],
      kind_config: { project_kind: 'greenfield', entry_stage: 'build' },
    },
    L1: {
      id: 'L1', name: 'Pass the suite', kind: 'goal', status: 'review', task: 'make the suite pass',
      goal_type: 'verifiable', kind_config: { goal_type: 'verifiable' },
    },
  } as Record<string, Record<string, unknown>>,
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const api = new Proxy(real.api as unknown as Record<string, unknown>, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      if (prop === 'uLoop') return Promise.resolve(h.loops[args[0] as string])
      if (prop === 'skills' || prop === 'savedAgents') return Promise.resolve([])
      if (prop === 'uLoopPlanSession') return Promise.resolve(null)
      return Promise.resolve({})
    },
  })
  return { ...real, api }
})
vi.mock('../loops/useRunStream', () => ({ useRunStream: () => ({ connected: true }) }))

const { CodePlanReview } = await import('../code/CodePlanReview')
const { LoopPlanReview } = await import('../loops/LoopPlanReview')

afterEach(cleanup)

const codeDraft = { projectId: 'P1', classification: {}, rigor: 'minimal', attended: true } as never
const loopDraft = { loopId: 'L1', classification: { kind: 'goal', execution: 'solo', kind_config: {} }, rigor: 'minimal', agent: '', model: '', granularity: 'balanced', attended: true } as never

describe("a code project's Plan Review", () => {
  it('names the field that adds a condition, and adds what was typed when it loses focus', async () => {
    render(<CodePlanReview draft={codeDraft} onBack={() => {}} onLaunched={() => {}} />)
    const add = await screen.findByRole('textbox', { name: 'Add a condition to Build' })
    fireEvent.change(add, { target: { value: 'the retry is tested' } })
    fireEvent.blur(add)
    expect(await screen.findByText('the retry is tested')).toBeInTheDocument()
    expect(add).toHaveValue('')
  })

  it("names each task's fields by the task and the stage, and the field that adds one", async () => {
    render(<CodePlanReview draft={codeDraft} onBack={() => {}} onLaunched={() => {}} />)
    expect(await screen.findByRole('textbox', { name: 'Task 1 of Build' })).toHaveValue('Wire the retry')
    expect(screen.getByRole('textbox', { name: 'What task 1 of Build does' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Add a task to Build' })).toBeInTheDocument()
  })
})

describe("a goal loop's Plan Review", () => {
  it('names the verify command field and leaves it unchecked for spelling', async () => {
    render(<LoopPlanReview draft={loopDraft} onBack={() => {}} onLaunched={() => {}} />)
    const cmd = await screen.findByRole('textbox', { name: 'Verify command' })
    expect(cmd).toHaveAttribute('spellcheck', 'false')
  })

  it('ends a title edit when its field loses focus', async () => {
    render(<LoopPlanReview draft={loopDraft} onBack={() => {}} onLaunched={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Pass the suite' }))
    const field = screen.getByRole('textbox', { name: 'Edit the plan title' })
    fireEvent.change(field, { target: { value: 'Make the suite pass' } })
    fireEvent.blur(field)
    expect(screen.queryByRole('textbox', { name: 'Edit the plan title' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Make the suite pass' })).toBeInTheDocument()
  })
})
