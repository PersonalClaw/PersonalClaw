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

export async function knowledgeStats(): Promise<KnowledgeStats> {
  return api.knowledgeStats().catch(() => ({ items: 0, entities: 0, relations: 0, embeddings: { enabled: false } } as KnowledgeStats))
}

export { FILE_TYPES }
