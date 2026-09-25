import { describe, it, expect } from 'vitest'
import { modelIdOf, boundModelLabel } from './modelRef'

// #3528 — the reading behind "Chat model: …" on the first-run recap. The value it turns into
// words is `active_models.json`'s chat chain, so the two traps are: a model id that contains
// colons of its own, and an empty chain that must NOT be turned into a model name.

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
