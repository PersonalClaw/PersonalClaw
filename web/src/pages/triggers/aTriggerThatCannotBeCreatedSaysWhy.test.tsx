import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { ActionProvider } from '../../lib/api'

// ── A trigger that cannot be created yet says what it is waiting for, where it can be read ─────
//
// Measured: Triggers › New, action "Run workflow", no workflow picked. "Create trigger" was greyed,
// pressing it did nothing, and the only reason was the button's hover title — generic at that:
// "Complete the required settings". Now the first requirement outstanding is said beside the
// button, and a required action setting is named by its label.
//
// The action's schema is the bundled manifest READ FROM THE SOURCE: it is exactly what the gateway
// serves, so the label the sentence names is the one the form shows.

vi.mock('../prompts/promptWidgets', () => ({ usePromptWidgets: () => ({ prompts: [], widgets: {} }) }))

const MANIFEST = JSON.parse(readFileSync(
  join(process.cwd(), '..', 'src', 'personalclaw', 'apps', 'native', 'run-workflow-action', 'app.json'),
  'utf8',
))

const { createSchedule, PROVIDERS } = vi.hoisted(() => ({
  createSchedule: vi.fn((_body: Record<string, unknown>) => Promise.resolve({ id: 'sched-1' })),
  PROVIDERS: [] as ActionProvider[],
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    actionProviders: () => Promise.resolve(PROVIDERS),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: ['$NOW'], event: [] }),
    savedAgents: () => Promise.resolve([]),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve({}),
    prompts: () => Promise.resolve([]),
    workflowDefs: () => Promise.resolve({ defs: [{ name: 'deep-research', description: 'Research a question' }], total: 1 }),
    workflowDef: (name: string) => Promise.resolve({ definition: { name, root: {}, inputs: { question: { type: 'string', required: true } } } }),
    createSchedule,
    createHook: vi.fn(),
    createEvent: vi.fn(),
  },
}))

const { TriggerCreatePage } = await import('./TriggerCreatePage')

beforeEach(() => {
  sessionStorage.clear()
  createSchedule.mockClear()
  PROVIDERS.length = 0
  PROVIDERS.push({
    name: 'run-workflow', display_name: 'Run Workflow', internal: false, supports_blocking: false,
    settingsSchema: MANIFEST.provider.settingsSchema,
  })
})

const create = () => screen.getByRole('button', { name: /^Create trigger/ })

describe('Create trigger, while it cannot create yet', () => {
  it('says the first thing it waits for beside the button, and names a required setting', async () => {
    render(<TriggerCreatePage onBack={() => {}} onCreated={() => {}} query={{}} setQuery={() => {}} />)
    // Said on screen before anything is typed — not only in the button's title.
    expect(screen.getByText('Name the trigger first')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: /^Name/ }), { target: { value: 'Weekly research' } })
    expect(await screen.findByText('Pick the action it runs')).toBeInTheDocument()

    fireEvent.click(await screen.findByRole('button', { name: /Pick an action/ }))
    fireEvent.click(await screen.findByRole('option', { name: /Run Workflow/ }))
    const said = await screen.findByText('“Workflow” is required')
    expect(said.tagName, 'the reason is read on the page').toBe('P')
    expect(create()).toHaveAttribute('aria-disabled', 'true')
    expect(create().getAttribute('title')).toContain('“Workflow” is required')
    fireEvent.click(create())
    expect(createSchedule).not.toHaveBeenCalled()

    // Once the workflow is picked, it has nothing left to wait for and says nothing.
    fireEvent.click(await screen.findByRole('button', { name: /Pick a workflow/ }))
    fireEvent.click(await screen.findByRole('option', { name: /deep-research/ }))
    await waitFor(() => expect(screen.queryByText('“Workflow” is required')).toBeNull())
    expect(create()).not.toHaveAttribute('aria-disabled')
  })
})
