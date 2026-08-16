import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TRIGGER_PRESETS } from './triggerPresets'

// ── The clause the atom turns on, asserted at the CALL SITE ───────────────────────────
//
// `triggerPresets.test.ts` proves the catalog is well-formed. That is not the same as the
// create flow being pre-filled: a catalog can be perfect while the page ignores it. So
// this mounts the real `TriggerCreatePage` at the URL the empty state navigates to and
// drives it the way a user does — open, look, press Create — then reads what went on the
// wire.
//
// Three things are held:
//
//   1. `?preset=morning-briefing` opens the form ALREADY holding the preset's name,
//      cadence and action, and pressing Create posts a schedule body with the derived
//      cron and the seeded action. "Pre-filled to a WORKING schedule trigger" is exactly
//      this body: a real cron plus an action whose required field is non-empty.
//   2. `#/triggers/new` with no `preset` is untouched — empty name, Create refuses,
//      nothing posted. This is the "expert blank-create path still works unchanged"
//      clause, asserted rather than eyeballed.
//   3. An unknown preset id behaves like case 2, not like case 1.

// `vi.mock`'s factory is hoisted above every top-level binding, so the spy and the
// fixtures it closes over have to be hoisted with it.
const { createSchedule, PROVIDERS } = vi.hoisted(() => ({
  createSchedule: vi.fn((_body: Record<string, unknown>) => Promise.resolve({ id: 'sched-1' })),
  PROVIDERS: [
    {
      name: 'invoke-agent', display_name: 'Invoke Agent', supports_blocking: false,
      settingsSchema: {
        type: 'object', required: ['task_template'],
        properties: {
          task_template: { type: 'string', 'x-meta': { label: 'Task' } },
          agent: { type: 'string', 'x-meta': { label: 'Agent' } },
          approval_mode: { type: 'string', enum: ['', 'auto'], default: '', 'x-meta': { label: 'Approval' } },
        },
      },
    },
    {
      name: 'notify', display_name: 'Notification', supports_blocking: false,
      settingsSchema: {
        type: 'object', required: ['title_template'],
        properties: {
          title_template: { type: 'string', 'x-meta': { label: 'Title' } },
          body_template: { type: 'string', 'x-meta': { label: 'Body' } },
          kind: { type: 'string', enum: ['info', 'success'], default: 'info', 'x-meta': { label: 'Kind' } },
        },
      },
    },
  ],
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
    createSchedule,
    createHook: vi.fn(),
    createEvent: vi.fn(),
  },
}))

// Imported after the mock so the page's `api` binding is the mocked one.
const { TriggerCreatePage } = await import('./TriggerCreatePage')

const mount = (query: Record<string, string>) => render(
  <TriggerCreatePage onBack={() => {}} onCreated={() => {}} query={query} setQuery={() => {}} />,
)

/** The one input whose label is "Name" — the trigger's own identity field. */
const nameInput = () => screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement

beforeEach(() => {
  createSchedule.mockClear()
  sessionStorage.clear()
})

describe('#/triggers/new?preset=… — the seeded flow', () => {
  it('opens holding the preset\'s name, cadence and action', async () => {
    mount({ kind: 'schedule', preset: 'morning-briefing' })

    expect(nameInput().value).toBe('Morning briefing')
    // The cadence landed as the CRON mode with the derived expression — not as the
    // blank draft's default interval.
    const cron = await waitFor(() => screen.getByRole('textbox', { name: /Cron expression/i }) as HTMLInputElement)
    expect(cron.value).toBe('0 8 * * *')
    // The action provider is chosen and its required field is filled, so the form is
    // savable on arrival.
    await waitFor(() => expect(screen.getByText('Invoke Agent')).toBeInTheDocument())
    const task = await waitFor(() => screen.getByRole('textbox', { name: /^Task/ }) as HTMLTextAreaElement)
    expect(task.value).toContain('morning briefing')
    // And it says why the form is already full.
    expect(screen.getByText(/Filled in from the/)).toBeInTheDocument()
  })

  it('saves a working schedule trigger on the first press of Create', async () => {
    mount({ kind: 'schedule', preset: 'morning-briefing' })
    const create = await waitFor(() => {
      const b = screen.getByRole('button', { name: /^Create trigger/ })
      // Seeded ⇒ the required-field gate is already met. `Button` marks an unavailable
      // action with `aria-disabled` (keeping the tab stop) whenever it has a reason to
      // announce, so `toBeDisabled()` — which reads the native attribute — would pass
      // vacuously here.
      expect(b).not.toHaveAttribute('aria-disabled')
      return b
    })
    await userEvent.click(create)

    await waitFor(() => expect(createSchedule).toHaveBeenCalledTimes(1))
    const body = createSchedule.mock.calls[0][0]
    expect(body.name).toBe('Morning briefing')
    expect(body.cron).toBe('0 8 * * *')
    expect(body.every).toBeUndefined()
    expect(body.action).toEqual({
      provider: 'invoke-agent',
      // The provider's optional fields keep their SCHEMA defaults; only what the preset
      // declares is overridden.
      config: expect.objectContaining({
        task_template: expect.stringContaining('morning briefing'),
        agent: '',
        approval_mode: '',
      }),
    })
  })

  it('keeps a notify preset\'s declared value over the schema default', async () => {
    mount({ kind: 'schedule', preset: 'standup-reminder' })
    const create = await waitFor(() => {
      const b = screen.getByRole('button', { name: /^Create trigger/ })
      expect(b).not.toHaveAttribute('aria-disabled')
      return b
    })
    await userEvent.click(create)
    await waitFor(() => expect(createSchedule).toHaveBeenCalledTimes(1))
    const body = createSchedule.mock.calls[0][0]
    expect(body.cron).toBe('45 9 * * 1-5')
    expect(body.action).toEqual({
      provider: 'notify',
      config: {
        title_template: 'Standup in 15 minutes',
        body_template: 'Jot down what you finished yesterday and what you are picking up today.',
        kind: 'info',   // the schema default survived the merge
      },
    })
  })

  it('covers every preset in the catalog — each one arrives savable', async () => {
    for (const p of TRIGGER_PRESETS) {
      const view = mount({ kind: 'schedule', preset: p.id })
      await waitFor(() => expect(screen.getByRole('button', { name: /^Create trigger/ })).not.toHaveAttribute('aria-disabled'))
      view.unmount()
    }
  })
})

describe('#/triggers/new — the expert blank path, unchanged', () => {
  it('opens empty and refuses to create', async () => {
    mount({})
    expect(nameInput().value).toBe('')
    // No preset ⇒ no seed note, no action chosen, and the Create gate holds.
    expect(screen.queryByText(/Filled in from the/)).not.toBeInTheDocument()
    expect(screen.getByText('Pick an action…')).toBeInTheDocument()
    const create = await waitFor(() => {
      const b = screen.getByRole('button', { name: /^Create trigger/ })
      expect(b).toHaveAttribute('aria-disabled', 'true')
      return b
    })
    // …and it says WHY, in the order the form presents its requirements.
    expect(create.getAttribute('title')).toContain('Name the trigger first')
    await userEvent.click(create)
    expect(createSchedule).not.toHaveBeenCalled()
  })

  it('still defaults to the interval cadence, not a preset\'s cron', async () => {
    mount({})
    // `emptyDraft()` opens on `every` — the blank path's own default. A seed leaking in
    // would show the cron field instead.
    expect(screen.queryByRole('textbox', { name: /Cron expression/i })).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: /interval count/i })).toBeInTheDocument()
  })

  it('treats an unknown preset id as the blank path rather than guessing', async () => {
    mount({ kind: 'schedule', preset: 'not-a-preset' })
    expect(nameInput().value).toBe('')
    expect(screen.queryByText(/Filled in from the/)).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /^Create trigger/ })).toHaveAttribute('aria-disabled', 'true'))
  })
})
