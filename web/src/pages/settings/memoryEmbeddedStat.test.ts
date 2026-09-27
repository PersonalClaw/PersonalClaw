import { describe, expect, it } from 'vitest'
import { memoryEmbeddedStat } from './memoryEmbeddedStat'

describe('the Memory panel Embedded stat', () => {
  it('names the provider when every vector is the bound model’s', () => {
    expect(memoryEmbeddedStat({ embedded_count: 4, embedded_stale: 0, embedding_provider: 'ollama' }))
      .toEqual({ value: 4, sub: 'ollama' })
  })

  it('says how many another model embedded, and that they are read by keyword', () => {
    // After an Embedding rebind, before the re-index: those vectors are never compared with the
    // new model's, so counting them as Embedded said they were searchable by meaning.
    expect(memoryEmbeddedStat({ embedded_count: 1, embedded_stale: 2, embedding_provider: 'ollama' })).toEqual({
      value: 1,
      sub: '2 by another model',
      title: '2 memories were embedded by another embedding model. Memory search reads them by keyword '
        + 'until the re-index in Settings → Models re-embeds them.',
    })
  })

  it('with no model bound, says search matches by keyword instead of naming a provider "none"', () => {
    // Clearing Embedding keeps the vectors (a model chosen again re-indexes them, or reuses its
    // own), and nothing compares them meanwhile: "6 Embedded · none" read as searchable.
    expect(memoryEmbeddedStat({ embedded_count: 6, embedded_stale: 0, embedding_provider: 'none' })).toEqual({
      value: 6,
      sub: 'no model bound',
      title: 'No embedding model is bound, so memory search matches by keyword. The embeddings memories '
        + 'already hold are kept; choosing a model in Settings → Models re-indexes them.',
    })
  })

  it('agrees with its own count at one', () => {
    expect(memoryEmbeddedStat({ embedded_count: 0, embedded_stale: 1 }).title)
      .toBe('1 memory was embedded by another embedding model. Memory search reads it by keyword '
        + 'until the re-index in Settings → Models re-embeds it.')
  })
})
