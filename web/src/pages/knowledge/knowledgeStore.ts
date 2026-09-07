import { api, type KnowledgeItem, type KnowledgeStats } from '../../lib/api'

/**
 * Knowledge data layer — a thin pass-through to the real backend. Typed items
 * (note/fleeting/journal/gist/bookmark) are authored via `POST /api/knowledge/
 * items`; media types upload through the ingestion pipeline. (P6b replaced the
 * former localStorage stub — every call now hits the backend.)
 */

const FILE_TYPES = new Set(['image', 'audio', 'video', 'pdf', 'document', 'sheet', 'slides'])

export async function listKnowledge(params?: { q?: string; type?: string; includeArchived?: boolean }): Promise<KnowledgeItem[]> {
  try {
    const d = await api.knowledgeItems({ q: params?.q, type: params?.type, includeArchived: params?.includeArchived, limit: 100 })
    return d.items
  } catch {
    return []
  }
}

export async function getKnowledge(id: string): Promise<KnowledgeItem | null> {
  try { return await api.knowledgeItem(id) } catch { return null }
}

/** Create a typed knowledge item. Text/gist/bookmark/journal/fleeting persist via
 *  POST /items; a gist's language rides in insights. File types are uploaded via
 *  uploadKnowledgeFile, not here. */
export async function createKnowledge(input: {
  type: KnowledgeItem['type']; title?: string; content?: string; url?: string
  tags?: string[]; gist_language?: string
}): Promise<KnowledgeItem> {
  return api.createKnowledgeItem({
    type: input.type,
    title: input.title || '',
    content: input.content || '',
    url: input.url || '',
    tags: input.tags ?? [],
    ...(input.gist_language ? { gist_language: input.gist_language } : {}),
  })
}

export async function updateKnowledge(id: string, fields: Partial<KnowledgeItem>): Promise<void> {
  await api.updateKnowledgeItem(id, fields as Record<string, unknown>)
}

export async function deleteKnowledge(id: string): Promise<void> {
  await api.deleteKnowledgeItem(id)
}

/** Upload a real file → ONE logical-document item, run through its node-graph.
 *  Large files stream via the resumable protocol; onProgress reports bytes/pct. */
export async function uploadKnowledgeFile(
  file: File,
  onProgress?: (p: { loaded: number; total: number; pct: number }) => void,
): Promise<{ item_id?: string; type?: string; status: string }> {
  return api.ingestKnowledgeFile(file, onProgress)
}

/** 🔴 THIS READ MUST REJECT, BECAUSE ITS ZEROS ARE CLAIMS AND ONE OF THEM GIVES ADVICE.
 *
 *  It used to `.catch(() => ({ items: 0, entities: 0, relations: 0, embeddings: { enabled: false } }))`,
 *  which turns a failed GET into four confident statements. Three are wrong counts; the fourth is
 *  worse than wrong. `KnowledgeListPage`'s `EmbeddingChip` branches on `!e?.enabled` and renders
 *  "semantic search off", titled *"No embedding model active — search is keyword + entity-graph
 *  only. Set one in Settings › AI & Models."* So an unreachable gateway told a user their semantic
 *  search was off and sent them to reconfigure a setting that may already have been correct.
 *  A wrong indicator is bad; wrong ACTIONABLE ADVICE derived from a request that never landed is
 *  the part worth removing.
 *
 *  Rejecting needs no new UI, because the page's structure was already right and the swallow was
 *  the only thing defeating it: `const stats = statsData ?? null` and `{stats && (…)}` gate the
 *  whole stats strip, so an unread response now renders NOTHING instead of lying — the same as the
 *  not-yet-loaded state, which is exactly what it is. `const empty = stats && stats.items === 0`
 *  goes falsy for the same reason, so a failed stats read can no longer produce an "empty" claim
 *  either. One swallow removed, three false claims closed. */
export async function knowledgeStats(): Promise<KnowledgeStats> {
  return api.knowledgeStats()
}

export { FILE_TYPES }
