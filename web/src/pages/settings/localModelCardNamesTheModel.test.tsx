// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { api, type AvailableModel } from '../../lib/api'
import { LocalModelManager } from './LocalModelManager'

// ── The local-model card names the model, in the unit the rest of the product uses ───────────────
//
// Settings → Providers → Bundled offline model listed `SmolLM2-135M-Instruct-Q8_0` — the weight's
// FILE id — and "138 MB", while the onboarding offer and the chat notice said
// "SmolLM2-135M-Instruct" and "138 MiB" for the same download. A catalog `size_mb` is MiB (a job's
// byte total is `size_mb × 1024 × 1024`), so "MB" was also simply the wrong unit.

const SMOL: AvailableModel = {
  id: 'SmolLM2-135M-Instruct-Q8_0', name: 'SmolLM2-135M-Instruct-Q8_0', display_name: 'SmolLM2-135M-Instruct',
  capabilities: ['chat'], provider: 'bundled-chat', provider_type: 'bundled-chat',
  size_mb: 138.102539, license: 'Apache-2.0', downloaded: false, description: 'A small offline chat model',
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'modelDownloads').mockResolvedValue([])
})

describe('the local-model card', () => {
  it('names the model by its name, and keeps the file id one hover away', () => {
    render(<LocalModelManager provider="bundled-chat" models={[SMOL]} onChanged={() => {}} />)
    const title = screen.getByText('SmolLM2-135M-Instruct')
    expect(title.getAttribute('title'), 'the id every download and binding uses').toBe('SmolLM2-135M-Instruct-Q8_0')
    expect(screen.queryByText('SmolLM2-135M-Instruct-Q8_0'), 'the file id is not the visible title').toBeNull()
    expect(screen.getByRole('button', { name: 'Download SmolLM2-135M-Instruct' })).toBeTruthy()
  })

  it('states its size in MiB — "138 MiB", as the offer does', () => {
    render(<LocalModelManager provider="bundled-chat" models={[SMOL]} onChanged={() => {}} />)
    expect(screen.getByText(/A small offline chat model · 138 MiB$/)).toBeTruthy()
    expect(screen.queryByText(/138 MB/)).toBeNull()
  })

  it('a model whose id already reads as a name is shown as it was', () => {
    // The control: no `display_name` means the id is the name, and nothing about the row changes.
    render(<LocalModelManager provider="ollama" onChanged={() => {}}
      models={[{ ...SMOL, id: 'llama3:8b', name: 'llama3:8b', display_name: '', provider: 'ollama' }]} />)
    expect(screen.getByText('llama3:8b').getAttribute('title')).toBeNull()
    expect(screen.getByRole('button', { name: 'Download llama3:8b' })).toBeTruthy()
  })
})
