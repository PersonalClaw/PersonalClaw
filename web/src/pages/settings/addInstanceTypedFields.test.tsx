import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── Settings' "Add instance" form draws and saves settings by their DECLARED type ────────────
//
// It shared onboarding's text-only field renderer, so the same defect lived in both: a boolean
// rendered as a text box holding "true", and every option was saved as a string — which a
// provider factory reading `isinstance(v, int)` (Bedrock's `max_tokens`, for one) then silently
// dropped. The fix is the family, not the one form: both render through `ProviderConfigForm`'s
// typed `SchemaField`, the renderer every other provider settings form already used.

const TYPED = {
  type: 'ollama',
  label: 'Ollama',
  app: 'ollama-models',
  capabilities: ['chat'],
  multiInstance: true,
  settingsSchema: {
    properties: {
      endpoint: { type: 'string', default: 'http://localhost:11434', 'x-meta': { label: 'Endpoint' } },
      timeout_secs: { type: 'integer', minimum: 1, maximum: 3600, default: 120, 'x-meta': { label: 'Timeout (seconds)' } },
      keep_warm: { type: 'boolean', default: false, 'x-meta': { label: 'Keep the model loaded' } },
    },
    required: [],
  },
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('Add instance saves typed settings', () => {
  it('renders a switch and a bounded number field, and sends true and 300, not "true" and "300"', async () => {
    const create = vi.fn((_body: unknown) => Promise.resolve({ ok: true, name: 'lab' }))
    vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify: vi.fn() }))
    vi.doMock('../../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        modelProviders: () => Promise.resolve([]),
        modelsAvailable: () => Promise.resolve([]),
        modelProviderTypes: () => Promise.resolve([TYPED]),
        createModelProvider: create,
      },
    }))
    const { RemoteModelProviders } = await import('./ModelBackends')
    render(<RemoteModelProviders onChanged={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: /add instance/i }))
    const toggle = await screen.findByRole('switch', { name: 'Keep the model loaded' })
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    const timeout = screen.getByRole('spinbutton', { name: 'Timeout (seconds)' }) as HTMLInputElement
    expect([timeout.value, timeout.min, timeout.max]).toEqual(['120', '1', '3600'])

    fireEvent.change(screen.getByRole('textbox', { name: 'Instance name' }), { target: { value: 'lab' } })
    fireEvent.click(toggle)
    fireEvent.change(timeout, { target: { value: '300' } })
    fireEvent.click(screen.getAllByRole('button', { name: /add instance/i }).at(-1)!)

    await waitFor(() => expect(create).toHaveBeenCalled())
    expect(create.mock.calls[0][0]).toEqual({
      name: 'lab', type: 'ollama', model: '',
      options: { endpoint: 'http://localhost:11434', timeout_secs: 300, keep_warm: true },
    })
  })
})
