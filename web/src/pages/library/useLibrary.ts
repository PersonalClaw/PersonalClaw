import { useCallback, useMemo } from 'react'
import { api, type LibraryParams, type LibraryResponse } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { getReadingPosition } from '../knowledge/readingPosition'
import { refId, type LibraryContext } from './libraryMeta'

// ── The library's address and its one read ───────────────────────────────────────
//
// What the list shows lives in the address (`#/library?q=&kind=&made=&project=&store=&collection=&tag=
// &state=&sort=&entity=&layout=grid&item=<ref>`), so a reload, Back and a copied link open the same
// list. Multi-valued keys take a comma list, as `GET /api/library` does. The read is cached under the
// `knowledge` namespace, so a write that busts the knowledge store busts the library too.

/** The keys that narrow the list: any one of them set is a filter (the count line says what it left,
 *  and the announcement speaks). */
export const FILTER_KEYS = ['kind', 'made', 'project', 'store', 'collection', 'tag', 'state', 'entity'] as const
export const MULTI_KEYS = ['kind', 'made', 'tag', 'state'] as const

/** A comma list's values. */
export const listOf = (v: string | undefined): string[] => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : [])

/** An old Knowledge or Artifacts address's values, as the library reads them: the knowledge
 *  page's `type` and `provider`, and the artifacts page's `kind`, `src` and `sort`, which their
 *  redirect rows carry over under the names the library reads (`app/routeRedirects.ts`). Mapped
 *  here, never rewritten in the address. */
const OLD_TYPES: Record<string, string> = {
  note: 'note', fleeting: 'note', journal: 'note', gist: 'note', decision: 'note', bookmark: 'link',
  image: 'media', audio: 'media', video: 'media', pdf: 'document', document: 'document', sheet: 'document', slides: 'document',
}
const OLD_ARTIFACT_KINDS: Record<string, string> = {
  widget: 'page', html: 'page', react: 'page', infographic: 'page', image: 'media', video: 'media', svg: 'media',
}
const OLD_SOURCES: Record<string, string> = { chat: 'chats', subagent: 'chats', cron: 'automations', manual: 'you', import: 'you' }
const KIND_KEYS = new Set(['note', 'document', 'link', 'media', 'page', 'report', 'findings'])
const MADE_KEYS = new Set(['you', 'chats', 'automations', 'sources', 'not_recorded'])

const mapList = (v: string | undefined, known: Set<string>, old: Record<string, string>, fallback = '') =>
  [...new Set(listOf(v).map((x) => (known.has(x) ? x: old[x] ?? fallback)).filter(Boolean))].join(',')

export function mappedQuery(query: Record<string, string>): Record<string, string> {
  const out = { ...query }
  // The knowledge page's `type` is a kind; its `provider` names a source, so the thing was made by one.
  if (query.type && !query.kind) out.kind = mapList(query.type, KIND_KEYS, OLD_TYPES)
  if (query.provider && !query.made) out.made = 'sources'
  // The artifacts page's kinds are formats: each is the library kind that holds it, a document
  // when none other does.
  if (query.kind) out.kind = mapList(query.kind, KIND_KEYS, OLD_ARTIFACT_KINDS, 'document')
  if (query.made) out.made = mapList(query.made, MADE_KEYS, OLD_SOURCES)
  if (query.sort === 'updated') out.sort = 'activity'
  for (const k of Object.keys(out)) if (out[k] === '') delete out[k]
  return out
}

/** The read's params for the address `query` holds. Sort by is kept on the device when the address
 *  names none (`stored`). */
export function libraryParamsOf(query: Record<string, string>, stored = ''): LibraryParams {
  const sort = (query.sort || stored) as LibraryParams['sort'] | ''
  return {
    ...(query.q?.trim() ? { q: query.q.trim() } : {}),
    ...(query.kind ? { kind: listOf(query.kind) } : {}),
    ...(query.made ? { made: listOf(query.made) } : {}),
    ...(query.project ? { project: query.project } : {}),
    ...(query.store ? { store: query.store } : {}),
    ...(query.collection ? { collection: query.collection } : {}),
    ...(query.tag ? { tag: listOf(query.tag) } : {}),
    ...(query.state ? { state: listOf(query.state) } : {}),
    ...(sort === 'activity' || sort === 'added' || sort === 'name' ? { sort } : {}),
    ...(query.entity ? { entity: query.entity } : {}),
  }
}

/** Whether the address narrows the list: a search or any filter key. */
export function isNarrowed(query: Record<string, string>): boolean {
  return !!query.q?.trim() || FILTER_KEYS.some((k) => !!query[k])
}

/** The Library's side reads, as its page makes them: shared with the boot prefetch
 *  (`app/routePrefetch.ts`), so a reload's early read under each key is the page's own read. */
export const readLibraryProjects = () => api.projects()
export const readLibraryCollections = () => api.knowledgeCollections()
export const readLibraryStats = () => api.knowledgeStats()
/** The list's read for *params*, as `useLibrary` makes it. */
export const readLibrary = (params: LibraryParams) => api.library(params)

/** Where this device keeps the Library's sort. */
export const LIBRARY_SORT_KEY = 'library:sort'

/** The sort kept on this device, '' for none (or no storage). */
export function storedLibrarySort(): string {
  try { return localStorage.getItem(LIBRARY_SORT_KEY) ?? '' } catch { return '' }
}

/** The cache key a read of `params` is kept under. */
export const libraryKey = (params: LibraryParams) => `knowledge:library:${JSON.stringify(params)}`

/** The library's read for the address: every row the filter keeps, cache-first. */
export function useLibrary(params: LibraryParams) {
  const key = libraryKey(params)
  const fetcher = useCallback(() => readLibrary(params), [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return useQuery<LibraryResponse>(key, fetcher)
}

/** What the page knows beside the rows (`LibraryContext`): each intent's findings count and whether
 *  it is paused, each report's schedule and last run, read from the set-up's own reads, and how far
 *  a reader got. A failed read leaves its part out; the rows still draw. */
export function useLibraryContext(project?: string): LibraryContext {
  const intents = useQuery('knowledge:intents', () => api.knowledgeIntents())
  const reports = useQuery('knowledge:reports', () => api.researchReports())
  return useMemo<LibraryContext>(() => ({
    intents: new Map((intents.data?.intents ?? []).map((i) => [i.id, { count: i.outcome_count ?? 0, enabled: i.enabled !== false, proposesSkill: !!i.propose_skill }])),
    reports: new Map((reports.data?.reports ?? []).map((r) => [r.id, {
      schedule: r.schedule_shown?.words ?? '', lastRunTs: r.last_run_ts ?? null, lastStatus: r.last_status ?? '',
    }])),
    ...(project ? { project } : {}),
    readingPct: (id: string) => getReadingPosition(id)?.pct ?? null,
  }), [intents.data, reports.data, project])
}

/** The ref an old address names, mapped by the page: a bare `item=<id>` is a knowledge item. */
export function itemRefOf(raw: string | undefined): string {
  if (!raw) return ''
  return raw.includes(':') ? raw: `knowledge:${raw}`
}

/** A report's write-up, the knowledge item a report attaches and opens as. */
export const writeUpIdOf = (ref: string, writeUps: ReadonlyMap<string, string>) => writeUps.get(refId(ref)) ?? ''

/** Each report's write-up, by the report's id: the knowledge item a report attaches and opens as. */
export function useWriteUps(): ReadonlyMap<string, string> {
  const reports = useQuery('knowledge:reports', () => api.researchReports())
  return useMemo(() => new Map((reports.data?.reports ?? []).filter((r) => r.finding_item_id).map((r) => [r.id, r.finding_item_id!])), [reports.data])
}
