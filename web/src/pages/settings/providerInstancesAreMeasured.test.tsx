import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

// ── A model-provider instance card says what was MEASURED, and every setting can be edited ───────
//
// Measured on a live gateway before this change:
//   • a keyless OpenAI instance read "✓ Configured" beside its own Test saying "No API key or
//     endpoint configured" — the badge was the credential's PRESENCE, not its connection;
//   • the Edit form offered an endpoint, a context window and a model: a key could be neither
//     added to a keyless instance nor replaced after the vendor rejected it;
//   • an Ollama nothing was listening at read "No downloadable models listed."
//
// The card now renders the instance's measured `connection`, keeps a failure on screen without a
// click, edits every setting of its TYPE (the same settingsSchema the Add form renders — the API
// key included), and an instance whose listing failed says why instead of listing nothing.

type Conn = { state: 'checking' | 'connected' | 'failed' | 'untestable'; detail: string; rejected_credential: boolean; checked_at: number | null }
const conn = (state: Conn['state'], detail = '', rejected_credential = false): Conn =>
  ({ state, detail, rejected_credential, checked_at: state === 'checking' ? null : 1 })

const OPENAI_TYPE = {
  type: 'openai', label: 'OpenAI', app: 'openai-models', capabilities: ['chat'], multiInstance: true,
  settingsSchema: {
    properties: {
      api_key: { type: 'string', 'x-meta': { label: 'OpenAI API Key', sensitive: true } },
      default_model: { type: 'string', 'x-meta': { label: 'Default Model' } },
      endpoint: { type: 'string', 'x-meta': { label: 'Endpoint' } },
    },
    required: [],
  },
}

function instance(over: Record<string, unknown> = {}) {
  return {
    name: 'my-openai', type: 'openai', declared_type: 'openai', model: '', capabilities: ['chat'],
    connection: conn('connected', 'Connected — 3 model(s) available'),
    options: { endpoint: 'https://api.openai.com/v1', default_model: 'gpt-x' },
    secret_set: [] as string[], key_in_store: false,
    ...over,
  }
}

const calls: { update: [string, unknown][]; test: string[] } = { update: [], test: [] }

async function mount(opts: {
  providers: ReturnType<typeof instance>[]; available?: unknown[]; onChanged?: () => void; test?: unknown
  update?: () => Promise<unknown>; types?: unknown[]
}) {
  calls.update = []; calls.test = []
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      modelProviders: () => Promise.resolve(opts.providers),
      modelsAvailable: () => Promise.resolve(opts.available ?? []),
      modelProviderTypes: () => Promise.resolve(opts.types ?? [OPENAI_TYPE]),
      testModelProvider: (name: string) => { calls.test.push(name); return Promise.resolve(opts.test) },
      updateModelProvider: (name: string, body: unknown) => {
        calls.update.push([name, body]); return opts.update ? opts.update() : Promise.resolve({ ok: true })
      },
      searchLocalModels: () => Promise.resolve([]),
      modelDownloads: () => Promise.resolve([]),
    },
  }))
  const { RemoteModelProviders } = await import('./ModelBackends')
  render(<RemoteModelProviders onChanged={opts.onChanged ?? (() => {})} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the badge is a measurement', () => {
  it('a keyless instance whose test failed never reads "Configured"', async () => {
    await mount({ providers: [instance({ connection: conn('failed', 'No API key or endpoint configured for this provider — set its endpoint (and key, if the endpoint needs one) in Settings → Providers.') })] })

    await screen.findByText('my-openai')
    expect(screen.queryByText('Configured'), 'the presence badge').toBeNull()
    expect(screen.getByText('Not answering')).toBeTruthy()
    // The failure is on the card without a click: it is the first thing this card has to say.
    expect(screen.getByText(/No API key or endpoint configured for this provider/)).toBeTruthy()
  })

  it('a rejected key reads as a rejected key', async () => {
    await mount({ providers: [instance({ connection: conn('failed', 'https://api.openai.com/v1/models rejected the credential (HTTP 401) — re-enter this provider\'s API key.', true) })] })
    expect(await screen.findByText('Key rejected')).toBeTruthy()
    expect(screen.getByText(/rejected the credential \(HTTP 401\)/)).toBeTruthy()
  })

  it('a connected instance says Connected, and an unmeasured one says it is checking', async () => {
    // The floor under the two above: a badge that was always red would pass them.
    await mount({ providers: [instance(), instance({ name: 'fresh', connection: conn('checking') })] })
    const connected = (await screen.findByText('my-openai')).closest('div.rounded-lg') as HTMLElement
    expect(within(connected).getByText('Connected')).toBeTruthy()
    const fresh = screen.getByText('fresh').closest('div.rounded-lg') as HTMLElement
    expect(within(fresh).getByText('Checking…')).toBeTruthy()
  })

  it("Test shows the test's own sentence and refreshes the card from the recorded answer", async () => {
    const onChanged = vi.fn()
    await mount({
      providers: [instance()], onChanged,
      test: { ok: false, status: 'error', message: 'Could not reach https://api.openai.com/v1/models — check the endpoint host.' },
    })
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Test connection: my-openai' })) })
    expect(calls.test).toEqual(['my-openai'])
    expect(await screen.findByText(/Could not reach https:\/\/api\.openai\.com\/v1\/models/)).toBeTruthy()
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })
})

describe('the edit form is the type\'s own settings form', () => {
  async function openEdit(over: Record<string, unknown> = {}) {
    await mount({ providers: [instance(over)] })
    fireEvent.click(await screen.findByRole('button', { name: 'Edit: my-openai' }))
    return await screen.findByLabelText('OpenAI API Key')
  }

  it('adds a key to a keyless instance, written to options.api_key', async () => {
    const key = await openEdit()
    fireEvent.change(key, { target: { value: 'sk-new-NOT-A-REAL-KEY' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(calls.update).toEqual([['my-openai', { options: { api_key: 'sk-new-NOT-A-REAL-KEY' } }]])
  })

  it('a saved key is never handed to the form, and a blank key is kept', async () => {
    const key = await openEdit({ options: { api_key: '••••••••', endpoint: 'https://api.openai.com/v1' }, secret_set: ['api_key'] })
    expect((key as HTMLInputElement).value).toBe('')
    expect((key as HTMLInputElement).placeholder).toBe('saved — leave blank to keep')
    // Change something else; the key must stay out of the body entirely.
    fireEvent.change(screen.getByLabelText('Endpoint'), { target: { value: 'https://proxy.example/v1' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(calls.update).toEqual([['my-openai', { options: { endpoint: 'https://proxy.example/v1' } }]])
  })

  it("a saved edit clears the last Test's answer, which described the replaced settings", async () => {
    // Driven live: after Test said "Could not reach …:1" and the endpoint was fixed, the card read
    // "Connected" with the old refusal still printed beneath it.
    await mount({
      providers: [instance()],
      test: { ok: false, status: 'error', message: 'Could not reach http://127.0.0.1:1 — the connection was refused.' },
    })
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Test connection: my-openai' })) })
    expect(await screen.findByText(/Could not reach http:\/\/127\.0\.0\.1:1/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Edit: my-openai' }))
    fireEvent.change(await screen.findByLabelText('Endpoint'), { target: { value: 'https://proxy.example/v1' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    await waitFor(() => expect(screen.queryByText(/Could not reach http:\/\/127\.0\.0\.1:1/)).toBeNull())
  })

  it('a saved key can be removed, not only replaced', async () => {
    await openEdit({ options: { api_key: '••••••••' }, secret_set: ['api_key'] })
    fireEvent.click(screen.getByRole('button', { name: 'Remove the saved value' }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(calls.update).toEqual([['my-openai', { options: { api_key: null } }]])
  })

  it('a switch and a number field save as a boolean and a number, as the Add form saves them', async () => {
    // The Add form saves each setting in its declared type (#3601): a factory reading
    // `isinstance(v, int)` drops "300". An Edit of the same schema must not undo that on save.
    const typed = {
      ...OPENAI_TYPE,
      settingsSchema: {
        properties: {
          ...OPENAI_TYPE.settingsSchema.properties,
          timeout_secs: { type: 'integer', minimum: 1, maximum: 3600, 'x-meta': { label: 'Timeout (seconds)' } },
          keep_warm: { type: 'boolean', 'x-meta': { label: 'Keep the model loaded' } },
        },
        required: [],
      },
    }
    await mount({ providers: [instance({ options: { endpoint: 'https://api.openai.com/v1', timeout_secs: 120, keep_warm: false } })], types: [typed] })
    fireEvent.click(await screen.findByRole('button', { name: 'Edit: my-openai' }))
    fireEvent.click(await screen.findByRole('switch', { name: 'Keep the model loaded' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Timeout (seconds)' }), { target: { value: '300' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(calls.update).toEqual([['my-openai', { options: { timeout_secs: 300, keep_warm: true } }]])
  })

  it('an endpoint the gateway refuses says why, and the form stays open to fix it', async () => {
    const refusal = '“not a url” isn\'t a URL — enter the full address, starting with http:// or https:// (for example http://localhost:11434).'
    await mount({ providers: [instance()], update: () => Promise.reject(new Error(refusal)) })
    fireEvent.click(await screen.findByRole('button', { name: 'Edit: my-openai' }))
    fireEvent.change(await screen.findByLabelText('Endpoint'), { target: { value: 'not a url' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect((await screen.findByRole('alert')).textContent).toBe(refusal)
    expect(screen.getByLabelText('Endpoint')).toBeTruthy()
  })
})

describe('a listing that failed says so', () => {
  it("an unreachable Ollama's card says it could not be reached, not that it lists nothing", async () => {
    await mount({
      providers: [instance({ name: 'host-ollama', type: 'ollama', declared_type: 'ollama', options: { endpoint: 'http://localhost:11434' }, connection: conn('failed', 'Could not reach http://localhost:11434 — the connection was refused. Check the URL and that the service is running.') })],
      available: [{ name: 'host-ollama', type: 'host-ollama', local: true, searchable: true, models: [], error: 'Could not reach http://localhost:11434 — the connection was refused. Check the URL and that the service is running.' }],
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Manage models: host-ollama' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Could not reach http://localhost:11434')
    expect(screen.queryByText('No downloadable models listed.')).toBeNull()
  })

  it('a reachable instance that lists nothing says exactly that', async () => {
    // The floor: an error line that appeared for every empty list would pass the test above.
    await mount({ providers: [instance()], available: [{ name: 'my-openai', type: 'openai', models: [] }] })
    fireEvent.click(await screen.findByRole('button', { name: 'View models: my-openai' }))
    expect(await screen.findByText('This instance lists no models.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
