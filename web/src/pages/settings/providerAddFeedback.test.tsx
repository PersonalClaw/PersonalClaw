import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

// ── A successful add that the surface denies (#3488) ─────────────────────────────────────────────
//
// Adding an Ollama model-provider instance through Settings → Providers WORKS, and nothing on
// screen says so: no toast, no new card, and the section the user acted in keeps reading
// "No remote model providers yet." Only a full reload reveals it.
//
// Two independent causes, and the fix needs both because either alone still lies:
//
//   1. `RemoteModelProviders.reload()` invalidated ONLY `settings:remote-model-providers` — the
//      one list whose `p.type !== 'ollama'` filter guarantees an Ollama instance is absent. The
//      section that DOES render it is the panel's Native (bundled) block, off
//      `settings:providers` + `settings:models-available`. Neither was invalidated.
//   2. The empty-state sentence is computed from the FILTERED list, so it goes on denying an
//      Ollama instance that exists even once the refresh is fixed.
//
// Why this is worse than an ordinary stale list: on a fresh install `Add instance`'s `Provider
// type` select has exactly ONE option (Ollama), and Ollama is the one type this section
// deliberately refuses to render. So the only thing the control can do is the one thing it cannot
// show — on the first-run path. And the write is not idempotent: a user shown no evidence clicks
// again and `api_provider_create` answers 409 "Provider 'host-ollama' already exists", on the same
// screen that says there are none.

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
  credential_status: 'ok',
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
  it('does not say "No remote model providers yet" while an Ollama instance exists', async () => {
    await mountRemote({ providers: [OLLAMA_INSTANCE] })

    // The control: the add button proves the section rendered, so a green below cannot be
    // "the component never mounted".
    await waitFor(() => expect(screen.getByRole('button', { name: /add instance/i })).toBeTruthy())

    expect(screen.queryByText(/No remote model providers yet/i)).toBeNull()
    // It has to say WHERE the instance went, or the user still has no evidence of the write.
    expect(screen.getByText(/Native \(bundled\)/i)).toBeTruthy()
    expect(screen.getByText(/host-ollama/)).toBeTruthy()
  })

  it('still says "No remote model providers yet" when there genuinely are none', async () => {
    // The vacuity floor for the assertion above: a sentence that never appears guards nothing.
    await mountRemote({ providers: [] })

    await waitFor(() => expect(screen.getByText(/No remote model providers yet/i)).toBeTruthy())
    expect(screen.queryByText(/Native \(bundled\)/i)).toBeNull()
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
