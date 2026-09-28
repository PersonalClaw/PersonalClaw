import type { MemoryStats } from '../../lib/api'

/** The Memory panel's "Embedded" stat: how many memories the bound embedding model embedded
 *  (searchable by meaning), and — when it has not embedded some — how many memory search reads by
 *  keyword instead.
 *
 *  An Embedding rebind leaves the old model's vectors in place until the re-index Settings →
 *  Models starts re-embeds them, and a memory written while no model was bound has none: memory
 *  search reads both by keyword. The count and its sentence are the server's (`read_by_keyword`,
 *  `read_by_keyword_note`), the same the recall disclosure and the Doctor's memory row say, so the
 *  page composes neither. It used to count only another model's vectors.
 *
 *  With no model bound (`embedding_provider` is `'none'`) nothing is compared, so nothing waits:
 *  the count is every memory holding a vector, kept for when a model is chosen, and the stat says
 *  that search matches by keyword meanwhile. */
export function memoryEmbeddedStat(stats: Pick<MemoryStats, 'embedded_count' | 'read_by_keyword' | 'read_by_keyword_note' | 'embedding_provider'>): {
  value: number; sub?: string; title?: string
} {
  const waiting = stats.read_by_keyword ?? 0
  if (waiting > 0) {
    return { value: stats.embedded_count, sub: `${waiting} read by keyword`, title: stats.read_by_keyword_note }
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
