import { describe, it, expect } from 'vitest'
import { modelIdOf, boundModelLabel, splitModelRef } from './modelRef'

// #3528 — the reading behind "Chat model: …" on the first-run recap. The value it turns into
// words is `active_models.json`'s chat chain, so the two traps are: a model id that contains
// colons of its own, and an empty chain that must NOT be turned into a model name.

describe('splitModelRef', () => {
  it('splits on the FIRST colon, so both halves survive their own punctuation', () => {
    expect(splitModelRef('Local Ollama:qwen2.5vl:7b')).toEqual({ provider: 'Local Ollama', model: 'qwen2.5vl:7b' })
    expect(splitModelRef('openrouter:anthropic/claude-3.5-sonnet'))
      .toEqual({ provider: 'openrouter', model: 'anthropic/claude-3.5-sonnet' })
  })

  it('is lossless — the halves rebuild the ref, so a caller may use them as identity', () => {
    // The Models picker unbinds a row it synthesised from these halves by re-joining them, so a
    // split that trimmed or dropped a segment would leave a binding the user cannot clear.
    for (const ref of ['ollama:gpt-oss:20b', 'My Work OpenAI:gpt-5', 'p: padded ', 'openai:']) {
      const { provider, model } = splitModelRef(ref)
      expect(`${provider}:${model}`).toBe(ref)
    }
  })

  it('gives an unqualified ref no provider, and the whole ref as its model', () => {
    expect(splitModelRef('claude-sonnet-4-5')).toEqual({ provider: '', model: 'claude-sonnet-4-5' })
  })
})

describe('modelIdOf', () => {
  it('splits on the FIRST colon, so a colon inside the model id survives', () => {
    // The exact ref #3528 was measured against. Splitting on the LAST colon yields '7b', and
    // `.split(':').pop()` is the shape that does it.
    expect(modelIdOf('Local Ollama:qwen2.5vl:7b')).toBe('qwen2.5vl:7b')
    expect(modelIdOf('ollama:gpt-oss:20b')).toBe('gpt-oss:20b')
  })

  it('returns an unqualified ref whole — a bare model id is still a model id', () => {
    expect(modelIdOf('claude-sonnet-4-5')).toBe('claude-sonnet-4-5')
  })

  it('tolerates a provider name with spaces, which is what the display name is', () => {
    expect(modelIdOf('My Work OpenAI:gpt-5')).toBe('gpt-5')
  })
})

describe('boundModelLabel', () => {
  it('reports position 0 — the default the next ordinary turn uses', () => {
    expect(boundModelLabel(['ollama:llama3.2:3b', 'openai:gpt-5'])).toBe('llama3.2:3b')
  })

  it('is empty for an empty chain, so a caller cannot name a model nobody chose', () => {
    expect(boundModelLabel([])).toBe('')
    expect(boundModelLabel(undefined)).toBe('')
  })

  it('skips a ref that names nothing rather than reporting a blank label as a model', () => {
    // A provider prefix with no model id after it is not a model, and reporting '' as the
    // label would be read by every caller as "nothing is bound" — which is the honest answer.
    expect(boundModelLabel(['openai:', 'openai:gpt-5'])).toBe('gpt-5')
    expect(boundModelLabel(['   '])).toBe('')
  })
})
