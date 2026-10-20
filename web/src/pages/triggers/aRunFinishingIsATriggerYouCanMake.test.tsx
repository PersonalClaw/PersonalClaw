import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ActionProvider } from '../../lib/api'

// ── "When that research run finishes, …" is a trigger the Triggers page can make ────────────────
//
// Triggers › New offered Schedule, Lifecycle event and Data event: the gateway's `run_completed`
// kind had no way in from the page, so "when this run finishes, post its summary" could be made
// nowhere. "Run finishes" now waits on a run going now, or on any run of a workflow.

vi.mock('../prompts/promptWidgets', () => ({ usePromptWidgets: () => ({ prompts: [], widgets: {} }) }))

const { createRunCompleted, PROVIDERS } = vi.hoisted(() => ({
  createRunCompleted: vi.fn((_body: Record<string, unknown>) => Promise.resolve({ ok: true })),
  PROVIDERS: [] as ActionProvider[],
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    actionProviders: () => Promise.resolve(PROVIDERS),
    triggerVariables: () => Promise.resolve({
      lifecycle: [], schedule: ['$NOW'], event: [], app_sources: [],
      run_completed: ['$EVENT', '$source_run_id', '$summary'],
    }),
    workflowRuns: () => Promise.resolve({
      runs: [
        { id: '9c2c10ab', workflow_name: 'deep-research', status: 'running', spec_version: 1, created_at: '' },
        { id: '0d0e0f01', workflow_name: 'deep-research', status: 'complete', spec_version: 1, created_at: '' },
      ],
      total: 2, limit: 100, offset: 0,
    }),
    workflowDefs: () => Promise.resolve({ defs: [{ name: 'deep-research', description: 'Research a question' }], total: 1 }),
    savedAgents: () => Promise.resolve([]),
    agentsInstalled: () => Promise.resolve([]),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve({}),
    prompts: () => Promise.resolve([]),
    createRunCompleted,
    createSchedule: vi.fn(),
    createHook: vi.fn(),
    createEvent: vi.fn(),
  },
}))

const { TriggerCreatePage } = await import('./TriggerCreatePage')

beforeEach(() => {
  sessionStorage.clear()
  createRunCompleted.mockClear()
  PROVIDERS.length = 0
  PROVIDERS.push({
    name: 'notify', display_name: 'Notify', internal: false, supports_blocking: false,
    settingsSchema: { type: 'object', properties: {} },
  } as unknown as ActionProvider)
})

const create = () => screen.getByRole('button', { name: /^Create trigger/ })

describe('A trigger that runs when a run finishes', () => {
  it('is offered as a trigger type', () => {
    render(<TriggerCreatePage onBack={() => {}} onCreated={() => {}} query={{}} setQuery={() => {}} />)
    expect(screen.getByRole('radio', { name: /Run finishes/ })).toBeInTheDocument()
  })

  it('waits on the run picked, of the runs still going, and says so until one is', async () => {
    const onCreated = vi.fn()
    render(<TriggerCreatePage onBack={() => {}} onCreated={onCreated} query={{ kind: 'run_completed' }} setQuery={() => {}} />)
    fireEvent.change(screen.getByRole('textbox', { name: /^Name/ }), { target: { value: 'Post the summary' } })
    fireEvent.click(await screen.findByRole('button', { name: /Pick an action/ }))
    fireEvent.click(await screen.findByRole('option', { name: /Notify/ }))
    expect(await screen.findByText('Pick the run it runs after')).toBeInTheDocument()

    fireEvent.click(await screen.findByRole('button', { name: /Pick a run/ }))
    expect(screen.queryByRole('option', { name: /0d0e0f01/ }), 'a run that ended is nothing to wait on').toBeNull()
    fireEvent.click(await screen.findByRole('option', { name: /9c2c10ab/ }))
    fireEvent.click(create())

    await waitFor(() => expect(createRunCompleted).toHaveBeenCalledTimes(1))
    expect(createRunCompleted.mock.calls[0][0]).toEqual({
      name: 'Post the summary', source_run: '9c2c10ab', action: { provider: 'notify', config: {} },
    })
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
  })

  it('waits on any run of a workflow picked instead', async () => {
    render(<TriggerCreatePage onBack={() => {}} onCreated={() => {}} query={{ kind: 'run_completed' }} setQuery={() => {}} />)
    fireEvent.change(screen.getByRole('textbox', { name: /^Name/ }), { target: { value: 'After research' } })
    fireEvent.click(await screen.findByRole('button', { name: /Pick an action/ }))
    fireEvent.click(await screen.findByRole('option', { name: /Notify/ }))
    fireEvent.click(screen.getByRole('radio', { name: /Any run of a workflow/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Pick a workflow/ }))
    fireEvent.click(await screen.findByRole('option', { name: /deep-research/ }))
    fireEvent.click(create())

    await waitFor(() => expect(createRunCompleted).toHaveBeenCalledTimes(1))
    expect(createRunCompleted.mock.calls[0][0]).toMatchObject({ source_def: 'deep-research' })
  })
})
