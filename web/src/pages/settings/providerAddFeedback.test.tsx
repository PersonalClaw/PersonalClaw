import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

// ── A successful add that the surface denies (#3488) ─────────────────────────────────────────────
//
// Adding an Ollama model-provider instance through Settings → Providers WORKED, and nothing on
// screen said so: no toast, no new card, and the section the user acted in kept reading
// "No remote model providers yet." Only a full reload revealed it.
//
// The root of it was a filter: this list — the ONLY one with Test, Edit and Remove — dropped every
// `type === 'ollama'` instance and left it to the panel's Native (bundled) block, which offered
// none of the three. So an Ollama endpoint that was wrong could be neither fixed nor removed from
// the page that displayed it. The filter is gone: an Ollama instance is an instance like any other
// and renders here, with its download card inside its own card.
//
// What is still pinned from #3488: a successful add says so, refreshes the PANEL's reads (not just
// this list's), and a failed one (the 409 a second click gets) claims nothing.

const notified: [string, string | undefined][] = []
function mockNotify() {
  notified.length = 0
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string, kind?: string) => { notified.push([msg, kind]) },
  }))
}

const OLLAMA_INSTANCE = {
  name: 'host-ollama',
  type: 'ollama',
  model: '',
  capabilities: ['chat', 'embedding'],
  connection: { state: 'checking' as const, detail: '', rejected_credential: false, checked_at: null },
}

const OLLAMA_TYPE = {
  type: 'ollama',
  label: 'Ollama',
  app: 'ollama-models',
  capabilities: ['chat'],
  multiInstance: true,
  settingsSchema: { properties: { endpoint: { type: 'string', default: 'http://localhost:11434' } }, required: [] },
}

/** Mount the remote section over a stubbed API. `providers` is what `/api/model-providers`
 *  answers; `create` stands in for the POST. */
async function mountRemote(opts: {
  providers?: typeof OLLAMA_INSTANCE[]
  create?: (body: unknown) => Promise<unknown>
  onChanged?: () => void
} = {}) {
  mockNotify()
  const create = opts.create ?? (() => Promise.resolve({}))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      modelProviders: () => Promise.resolve(opts.providers ?? []),
      modelsAvailable: () => Promise.resolve([]),
      modelProviderTypes: () => Promise.resolve([OLLAMA_TYPE]),
      createModelProvider: create,
    },
  }))
  const { RemoteModelProviders } = await import('./ModelBackends')
  const onChanged = opts.onChanged ?? (() => {})
  render(<RemoteModelProviders onChanged={onChanged} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the section the user acted in must not deny what it holds', () => {
  it('renders an Ollama instance as a card it can test, edit and remove', async () => {
    await mountRemote({ providers: [OLLAMA_INSTANCE] })

    // The control: the add button proves the section rendered, so a green below cannot be
    // "the component never mounted".
    await waitFor(() => expect(screen.getByRole('button', { name: /add instance/i })).toBeTruthy())

    expect(screen.queryByText(/No model provider instances yet/i)).toBeNull()
    for (const action of ['Test connection', 'Edit', 'Remove']) {
      expect(screen.getByRole('button', { name: `${action}: host-ollama` })).toBeTruthy()
    }
  })

  it('still says "No model provider instances yet" when there genuinely are none', async () => {
    // The vacuity floor for the assertion above: a sentence that never appears guards nothing.
    await mountRemote({ providers: [] })

    await waitFor(() => expect(screen.getByText(/No model provider instances yet/i)).toBeTruthy())
    expect(screen.queryByRole('button', { name: /^Remove:/ })).toBeNull()
  })
})

describe('a successful add refreshes the list that can actually show it', () => {
  async function addAnInstance(onChanged: () => void, create?: (b: unknown) => Promise<unknown>) {
    await mountRemote({ providers: [], onChanged, create })
    fireEvent.click(await screen.findByRole('button', { name: /add instance/i }))
    const nameField = await screen.findByLabelText('Instance name')
    fireEvent.change(nameField, { target: { value: 'host-ollama' } })
    // Two controls share the accessible name "Add instance" once the form is open (the
    // disclosure button is replaced by the form's submit), so submit by the last match.
    const submits = screen.getAllByRole('button', { name: /^Add instance$/i })
    // Awaited inside `act` so the POST's settle (resolve OR reject) is flushed here rather
    // than leaking a React state update out of the test body.
    await act(async () => { fireEvent.click(submits[submits.length - 1]) })
  }

  it('calls the panel-level refresh, not only its own filtered list', async () => {
    const onChanged = vi.fn()
    await addAnInstance(onChanged)

    // `onChanged` is the panel's `reload`: it invalidates `settings:providers` +
    // `settings:models-available` and refetches all three. Without it the ONLY surface that
    // renders an Ollama instance stays on its pre-write cache until a full page reload.
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('tells the user the instance was created', async () => {
    await addAnInstance(vi.fn())

    await waitFor(() => expect(notified.length).toBeGreaterThan(0))
    const [msg, kind] = notified[notified.length - 1]
    expect(kind).toBe('success')
    expect(msg).toMatch(/host-ollama/)
  })

  it('does not claim success when the write failed', async () => {
    // The other vacuity floor: an unconditional toast would satisfy the test above. A 409 —
    // exactly what a second click gets — must not be reported as an add, and must not refresh.
    const onChanged = vi.fn()
    await addAnInstance(onChanged, () => Promise.reject(new Error("Provider 'host-ollama' already exists")))

    await waitFor(() => expect(screen.getByText(/already exists/i)).toBeTruthy())
    expect(notified.filter(([, kind]) => kind === 'success')).toEqual([])
    expect(onChanged).not.toHaveBeenCalled()
  })
})
