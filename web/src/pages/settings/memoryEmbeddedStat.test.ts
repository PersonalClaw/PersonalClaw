import { describe, expect, it } from 'vitest'
import { memoryEmbeddedStat } from './memoryEmbeddedStat'

// The server's sentence for the count, as `GET /api/memory/stats` sends it (`keyword_read_note`).
const NOTE = '2 memories not embedded yet are read by keyword until the re-index in Settings → Models embeds them.'

describe('the Memory panel Embedded stat', () => {
  it('names the provider when every memory is embedded by the bound model', () => {
    expect(memoryEmbeddedStat({ embedded_count: 4, read_by_keyword: 0, read_by_keyword_note: '', embedding_provider: 'ollama' }))
      .toEqual({ value: 4, sub: 'ollama' })
  })

  it('counts memories no model embedded as read by keyword, in the server’s words', () => {
    // A memory written while no model was bound has no vector. The stat counted only another
    // model's vectors, so it read "4 · ollama" while search read these two by keyword.
    expect(memoryEmbeddedStat({ embedded_count: 4, read_by_keyword: 2, read_by_keyword_note: NOTE, embedding_provider: 'ollama' }))
      .toEqual({ value: 4, sub: '2 read by keyword', title: NOTE })
  })

  it('counts another model’s vectors the same way', () => {
    const note = '2 memories embedded by another embedding model are read by keyword until the re-index in Settings → Models re-embeds them.'
    expect(memoryEmbeddedStat({ embedded_count: 1, read_by_keyword: 2, read_by_keyword_note: note, embedding_provider: 'ollama' }))
      .toEqual({ value: 1, sub: '2 read by keyword', title: note })
  })

  it('with no model bound, says search matches by keyword instead of naming a provider "none"', () => {
    // Clearing Embedding keeps the vectors (a model chosen again re-indexes them, or reuses its
    // own), and nothing compares them meanwhile: "6 Embedded · none" read as searchable.
    expect(memoryEmbeddedStat({ embedded_count: 6, read_by_keyword: 0, read_by_keyword_note: '', embedding_provider: 'none' })).toEqual({
      value: 6,
      sub: 'no model bound',
      title: 'No embedding model is bound, so memory search matches by keyword. The embeddings memories '
        + 'already hold are kept; choosing a model in Settings → Models re-indexes them.',
    })
  })
})
