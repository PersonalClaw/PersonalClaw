import { api, type KnowledgeItem, type KnowledgeItemEdit } from '../../lib/api'
import type { UploadProgress } from '../../lib/chunkedUpload'

/**
 * Knowledge data layer — a thin pass-through to the real backend. Typed items
 * (note/fleeting/journal/gist/bookmark) are authored via `POST /api/knowledge/
 * items`; media types upload through the ingestion pipeline. (This replaced the
 * former localStorage stub — every call now hits the backend.)
 */

const FILE_TYPES = new Set(['image', 'audio', 'video', 'pdf', 'document', 'sheet', 'slides'])

export async function getKnowledge(id: string): Promise<KnowledgeItem | null> {
  try { return await api.knowledgeItem(id) } catch { return null }
}

/** Create a typed knowledge item. Text/gist/bookmark/journal/fleeting persist via
 *  POST /items; a gist's language rides in insights. File types are uploaded via
 *  uploadKnowledgeFile, not here. */
export async function createKnowledge(input: {
  type: KnowledgeItem['type']; title?: string; content?: string; url?: string
  tags?: string[]; gist_language?: string
  /** The project it is filed in, or none. */
  project_id?: string
}): Promise<KnowledgeItem> {
  return api.createKnowledgeItem({
    type: input.type,
    title: input.title || '',
    content: input.content || '',
    url: input.url || '',
    tags: input.tags ?? [],
    ...(input.gist_language ? { gist_language: input.gist_language } : {}),
    ...(input.project_id ? { project_id: input.project_id } : {}),
  })
}

/** An edit that replaces nothing whole — scalars, flags, tags one name at a time. A BODY edit
 *  names the revision it was built from and goes through `api.saveKnowledgeItem` instead. */
export async function updateKnowledge(id: string, fields: KnowledgeItemEdit): Promise<void> {
  await api.updateKnowledgeItem(id, fields)
}

export async function deleteKnowledge(id: string): Promise<void> {
  await api.deleteKnowledgeItem(id)
}

/** Upload a real file → ONE logical-document item, run through its node-graph.
 *  Large files stream via the resumable protocol; onProgress reports bytes/pct, and says when the
 *  upload is `finishing` (past cancelling). Aborting `signal` before then cancels it: no item. */
export async function uploadKnowledgeFile(
  file: File,
  onProgress?: (p: UploadProgress) => void,
  signal?: AbortSignal,
  /** The project it is filed in, or none. */
  projectId?: string,
): Promise<{ item_id?: string; type?: string; status: string; deduped?: boolean }> {
  return api.ingestKnowledgeFile(file, onProgress, signal, projectId)
}


export { FILE_TYPES }
