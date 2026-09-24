import { describe, it, expect } from 'vitest'
import { capableModels, modelChips } from './ModelsPanel'
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

  it('chat sub-categories draw from the chat-capable pool', () => {
    // Models never declare "code_tools"/"background"/… as capabilities — a
    // sub-category row must offer every CHAT-capable model (MODEL-USE-CASES-V2).
    const all = [
      M('OpenAI', 'gpt-4', ['chat']),
      M('Bedrock', 'gemma-3', ['image_modality']), // not chat → excluded
    ]
    for (const uc of ['code_tools', 'reasoning', 'background', 'orchestration', 'loops']) {
      const out = capableModels(uc, all, [])
      expect(out.map((m) => `${m.provider}:${m.id}`)).toEqual(['OpenAI:gpt-4'])
    }
  })
})

// The catalog-contract chips (LMMV §2.2/§2.3). modelChips is pure so the mapping is
// tested independently of rendering.
const MC = (fields: Partial<AvailableModel>): AvailableModel =>
  ({ id: 'm', name: 'm', provider: 'p', capabilities: [], ...fields } as AvailableModel)

describe('modelChips', () => {
  it('shows no chips for a plain (hosted/remote) model', () => {
    expect(modelChips(MC({ status: 'active' }))).toEqual([])
    expect(modelChips(MC({}))).toEqual([]) // no catalog fields at all
  })

  it('shows a status chip for deprecated and sunset (still bindable)', () => {
    expect(modelChips(MC({ status: 'deprecated' }))).toEqual(['status'])
    expect(modelChips(MC({ status: 'sunset' }))).toEqual(['status'])
  })

  it('shows a non-commercial warning chip at bind time (Success Criterion 7)', () => {
    expect(modelChips(MC({ non_commercial: true, license: 'CC-BY-NC-4.0' }))).toContain('non-commercial')
    expect(modelChips(MC({ non_commercial: false, license: 'MIT' }))).not.toContain('non-commercial')
  })

  it('shows a truncated chip (whose row carries Repair) only when integrity is truncated', () => {
    expect(modelChips(MC({ integrity: 'truncated' }))).toContain('truncated')
    expect(modelChips(MC({ integrity: '' }))).not.toContain('truncated')
    expect(modelChips(MC({ downloaded: true }))).not.toContain('truncated')
  })

  it('stacks every applicable chip', () => {
    const chips = modelChips(MC({ status: 'deprecated', non_commercial: true, integrity: 'truncated' }))
    expect(chips).toEqual(['status', 'non-commercial', 'truncated'])
  })
})
