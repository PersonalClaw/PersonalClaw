import type { MemoryStats } from '../../lib/api'

/** The Memory panel's "Embedded" stat: how many memories the bound embedding model embedded
 *  (searchable by meaning), and — when some were embedded by another model — how many of those.
 *
 *  An Embedding rebind leaves the old model's vectors in place until the re-index Settings →
 *  Models starts re-embeds them, and they are never compared with the new model's: memory search
 *  reads them by keyword. Counting them as "Embedded" said they were searchable by meaning.
 *
 *  With no model bound (`embedding_provider` is `'none'`) nothing is compared, so nothing is
 *  stale: the count is every memory holding a vector, kept for when a model is chosen again, and
 *  the stat says that search matches by keyword meanwhile. */
export function memoryEmbeddedStat(stats: Pick<MemoryStats, 'embedded_count' | 'embedded_stale' | 'embedding_provider'>): {
  value: number; sub?: string; title?: string
} {
  const stale = stats.embedded_stale ?? 0
  if (stale > 0) {
    const one = stale === 1
    return {
      value: stats.embedded_count,
      sub: `${stale} by another model`,
      title: `${stale} ${one ? 'memory was' : 'memories were'} embedded by another embedding model. Memory search reads ${one ? 'it' : 'them'} by keyword until the re-index in Settings → Models re-embeds ${one ? 'it' : 'them'}.`,
    }
  }
  if (stats.embedding_provider === 'none') {
    return {
      value: stats.embedded_count,
      sub: 'no model bound',
      title: 'No embedding model is bound, so memory search matches by keyword. The embeddings memories already hold are kept; choosing a model in Settings → Models re-indexes them.',
    }
  }
  return { value: stats.embedded_count, sub: stats.embedding_provider }
}
