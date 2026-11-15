import type { LibraryResponse } from '../../lib/api'
import { KINDS } from './libraryMeta'
import { joinWords } from './libraryFilter'
import { listOf } from './useLibrary'

// ── What the library says about its list ───────────────────────────────────
//
// The count line under the title, and the sentence a list with no rows says in its place. Pure, so
// the page and the tests read the same words.

export const n = (count: number) => count.toLocaleString('en-US')
const items = (count: number) => `${n(count)} ${count === 1 ? 'item' : 'items'}`

export interface LineInputs {
  query: Record<string, string>
  data: LibraryResponse
  /** The project the list is filtered to, by name; "" while it is not known. */
  projectName: string
  /** The collection the list is filtered to, and the search that fills a smart one. */
  collection?: { name: string; smart: string | null }
  /** Search matches words only: no model reads meaning. */
  wordsOnly: boolean
}

export type LineAction = 'clear' | 'clear-search' | 'open-project' | 'rename-collection' | 'delete-collection' | 'set-up-meaning'

export interface CountLine {
  lead?: string
  line?: string
  actions: { key: LineAction; label: string }[]
}

/** The count line: "5,012 items"; "23 matches · Matching words only. Set up search by meaning";
 *  "34 items in Homelab · Clear · Open project"; "9 items that mention NAS · Clear"; "Trips · 34 items · Rename · Delete collection ·
 *  Clear"; "Showing the best 100 matches. Add a word or a filter to narrow it." */
export function countLine({ query, data, projectName, collection, wordsOnly }: LineInputs): CountLine {
  const total = data.total
  const searching = !!query.q?.trim()
  const filtered = ['kind', 'made', 'project', 'store', 'collection', 'tag', 'state', 'entity'].some((k) => !!query[k])
  const actions: CountLine['actions'] = []
  if (searching) {
    const meaning: CountLine['actions'] = wordsOnly ? [{ key: 'set-up-meaning', label: 'Set up search by meaning' }] : []
    if (data.capped) {
      return { lead: `Showing the best ${n(data.items.length)} matches.`, line: 'Add a word or a filter to narrow it.', actions: meaning }
    }
    return { lead: `${n(total)} ${total === 1 ? 'match' : 'matches'}`, ...(wordsOnly ? { line: 'Matching words only.' } : {}), actions: meaning }
  }
  if (query.collection && collection) {
    actions.push({ key: 'rename-collection', label: 'Rename' }, { key: 'delete-collection', label: 'Delete collection' }, { key: 'clear', label: 'Clear' })
    const smart = collection.smart ? `Smart collection: everything matching “${collection.smart}”. · ` : ''
    return { lead: collection.name, line: `${smart}${items(total)}`, actions }
  }
  if (query.project) {
    actions.push({ key: 'clear', label: 'Clear' })
    if (query.project !== 'none') actions.push({ key: 'open-project', label: 'Open project' })
    const where = query.project === 'none' ? 'in no project' : projectName ? `in ${projectName}` : 'in this project'
    const what = query.store === 'artifacts' ? `${n(total)} pages and documents` : query.store === 'knowledge' ? `${n(total)} notes and files` : items(total)
    return { lead: `${what} ${where}`, actions }
  }
  // The graph's Show all in Items: "9 items that mention NAS · Clear".
  if (query.entity) return { lead: `${n(total)} ${total === 1 ? 'item that mentions' : 'items that mention'} ${query.entity}`, actions: [{ key: 'clear', label: 'Clear' }] }
  if (filtered) return { lead: items(total), actions: [{ key: 'clear', label: 'Clear' }] }
  return { line: items(total), actions: [] }
}

/** What a narrowed list with no rows says, and what it offers. */
export interface NoRows { title: string; actions: { key: 'clear-search' | 'search-everything' | 'clear-filters' | 'open-project'; label: string }[]; tip?: string }

/** "Nothing matches “plan” in Documents." · "Nothing in Garden yet." · "Nothing in Pages and apps yet." */
export function noRows(query: Record<string, string>, projectName: string): NoRows {
  const q = query.q?.trim() ?? ''
  const kinds = listOf(query.kind).map((k) => KINDS.find((x) => x.key === k)?.label ?? k)
  const filtered = ['kind', 'made', 'project', 'store', 'collection', 'tag', 'state', 'entity'].some((k) => !!query[k])
  const scope = query.project && query.project !== 'none' && projectName ? projectName: kinds.length ? joinWords(kinds) : ''
  if (q) {
    if (filtered) {
      return {
        title: scope ? `Nothing matches “${q}” in ${scope}.` : `Nothing matches “${q}” here.`,
        actions: [{ key: 'search-everything', label: 'Search everything' }, { key: 'clear-filters', label: 'Clear filters' }],
        tip: '⌘K also searches your chats and memory.',
      }
    }
    return { title: `Nothing matches “${q}”.`, actions: [{ key: 'clear-search', label: 'Clear search' }], tip: '⌘K also searches your chats and memory.' }
  }
  if (query.project && query.project !== 'none') {
    return {
      title: `Nothing in ${projectName || 'this project'} yet.`,
      actions: [{ key: 'clear-filters', label: 'Clear filters' }, { key: 'open-project', label: 'Open project' }],
    }
  }
  return { title: scope ? `Nothing in ${scope} yet.` : 'Nothing here yet.', actions: [{ key: 'clear-filters', label: 'Clear filters' }] }
}

/** The notice for a store the read could not reach: the rows that loaded stay. */
export function failedStoreNotice(failed: readonly string[]): string {
  if (failed.includes('knowledge') && failed.includes('artifacts')) return "Your library couldn't be loaded."
  if (failed.includes('artifacts')) return "The pages and documents the agent made couldn't be loaded, so they're missing here."
  if (failed.includes('knowledge')) return "Your notes and files couldn't be loaded, so they're missing here."
  return ''
}

/** The partial notice for a read cut at the rows' cap, worded by its sort. */
export function partialWords(sort: string, shown: number, total: number): string {
  if (sort === 'added') return `Showing the ${n(shown)} added most recently of ${n(total)}.`
  if (sort === 'name') return `Showing the first ${n(shown)} of ${n(total)} by name.`
  return `Showing the newest ${n(shown)} of ${n(total)} items.`
}
