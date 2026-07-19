import { describe, it, expect } from 'vitest'
import { capableModels } from './ModelsPanel'
import type { AvailableModel } from '../../lib/api'

const M = (provider: string, id: string, caps: string[], downloaded?: boolean): AvailableModel =>
  ({ id, name: id, provider, capabilities: caps, downloaded } as AvailableModel)

describe('capableModels', () => {
  it('returns catalog models declaring the capability, deduped by provider:id', () => {
    const all = [
      M('OpenAI', 'gpt-image-1', ['image_modality']),
      M('OpenAI', 'gpt-image-1', ['image_modality']), // dup discovery path
      M('Bedrock', 'gemma-3', ['image_modality']),
      M('OpenAI', 'gpt-4', ['chat']), // wrong capability
    ]
    const out = capableModels('image_modality', all, [])
    expect(out.map((m) => `${m.provider}:${m.id}`)).toEqual(['OpenAI:gpt-image-1', 'Bedrock:gemma-3'])
  })

  it('surfaces an active binding absent from the catalog as a synthetic not-downloaded row', () => {
    // The phantom-binding case: moondream is bound to image_modality but ollama has
    // no such model in the catalog (deleted / never pulled). It must still appear so
    // the user can see + unbind it — otherwise it reads "1 active" but is invisible.
    const all = [M('Bedrock', 'gemma-3', ['image_modality'])]
    const out = capableModels('image_modality', all, ['Ollama:moondream:latest'])
    const phantom = out.find((m) => m.provider === 'Ollama')
    expect(phantom).toBeTruthy()
    expect(phantom!.id).toBe('moondream:latest') // colon in the model id preserved
    expect(phantom!.downloaded).toBe(false)
    expect(phantom!.capabilities).toContain('image_modality')
  })

  it('does not duplicate an active binding that IS in the catalog', () => {
    const all = [M('Bedrock', 'gemma-3', ['image_modality'])]
    const out = capableModels('image_modality', all, ['Bedrock:gemma-3'])
    expect(out.filter((m) => `${m.provider}:${m.id}` === 'Bedrock:gemma-3')).toHaveLength(1)
  })

  it('handles a ref with no provider prefix', () => {
    const out = capableModels('stt', [], ['bare-model'])
    expect(out).toHaveLength(1)
    expect(out[0].provider).toBe('')
    expect(out[0].id).toBe('bare-model')
  })
})
