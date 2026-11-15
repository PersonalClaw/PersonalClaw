import type { FilterSectionDef } from '../../ui/FilterMenu'
import type { KnowledgeCollection, LibraryResponse, ProjectItem } from '../../lib/api'
import { KINDS, MAKERS } from './libraryMeta'
import { listOf } from './useLibrary'

// ── The library's Filter ─────────────────────────────────────────────────────────────
//
// Kind and Made by in the panel's first column; Project, State and Sort by in its second; Collection
// and Tag under both. Each value's count is the read's facet, hidden at 0. Multi sections compose
// their words with `said`, as Chat history's Tag does. Every choice pushes the address, so Back
// undoes a filter.

/** The State section's values, in the brief's order, and the read's key for each. */
export const STATES: readonly { key: string; label: string }[] = [
  { key: 'pinned', label: 'Pinned' },
  { key: 'reading', label: 'Reading' },
  { key: 'unread', label: 'Unread' },
  { key: 'read', label: 'Read' },
  { key: 'archived', label: 'Archived' },
  { key: 'being_read', label: 'Still being read' },
  { key: 'unreadable', label: "Couldn't be read" },
  { key: 'conflict', label: 'Disagrees with an item' },
]

export const SORTS: readonly { key: string; label: string; said: string }[] = [
  { key: 'activity', label: 'Recent activity', said: 'Sorted by recent activity' },
  { key: 'added', label: 'Date added', said: 'Sorted by date added' },
  { key: 'name', label: 'Name', said: 'Sorted by name' },
]

export const PROJECT_LIMIT = 7
export const COLLECTION_LIMIT = 7
export const TAG_LIMIT = 8

/** Several labels as one phrase: "Notes and Links", "Notes, Links and Reports". */
export function joinWords(words: readonly string[]): string {
  if (words.length <= 1) return words[0] ?? ''
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

/** What a multi section's chosen keys say in the Filter's label. */
const saidOf = (options: readonly { key: string; label: string }[]) => (key: string) =>
  joinWords(listOf(key).map((k) => options.find((o) => o.key === k)?.label ?? k))

export interface FilterInputs {
  query: Record<string, string>
  data: LibraryResponse | undefined
  projects: readonly ProjectItem[]
  collections: readonly KnowledgeCollection[]
  /** Push the address with `patch` (a key's `null` removes it). */
  push: (patch: Record<string, string | null>) => void
  /** Below 768 px: the Filter also holds Show as (List or Grid), which never counts as set. */
  phone: boolean
  /** The layout kept on the device, for Show as. */
  layout: 'list' | 'grid'
  setLayout: (layout: 'list' | 'grid') => void
  onNewCollection: () => void
  onManageTags: () => void
}

/** The Filter's sections for the address and the read's facets. */
export function librarySections(inp: FilterInputs): FilterSectionDef[] {
  const { query, data, push } = inp
  const facet = (name: string) => data?.facets?.[name] ?? {}
  const counted = (name: string, opts: readonly { key: string; label: string }[], keep: (key: string) => boolean = () => false) =>
    opts.filter((o) => (facet(name)[o.key] ?? 0) > 0 || keep(o.key)).map((o) => ({ key: o.key, label: o.label, count: facet(name)[o.key] ?? 0 }))
  const chosen = (key: string) => (k: string) => listOf(query[key]).includes(k)

  const kinds = counted('kind', KINDS, chosen('kind'))
  const makers = counted('made', MAKERS.filter((m) => m.key !== 'not_recorded' || (facet('made').not_recorded ?? 0) > 0), chosen('made'))
  const projectCounts = facet('project')
  const projectOptions = [
    { key: '', label: 'Any project' },
    { key: 'none', label: 'No project', count: projectCounts.none ?? 0 },
    ...inp.projects.filter((p) => p.status !== 'archived' && ((projectCounts[p.id] ?? 0) > 0 || query.project === p.id))
      .map((p) => ({ key: p.id, label: p.name, count: projectCounts[p.id] ?? 0 })),
  ]
  const collectionCounts = facet('collection')
  const byId = new Map(inp.collections.map((c) => [c.id, c]))
  const collectionOptions = [
    { key: '', label: 'Any collection' },
    ...Object.keys(collectionCounts).filter((k) => (collectionCounts[k] ?? 0) > 0 || query.collection === k).map((k) => {
      const c = byId.get(k)
      return { key: k, label: c ? (c.kind === 'smart' ? `${c.name} · Smart` : c.name) : k, count: collectionCounts[k] ?? 0 }
    }),
  ]
  const tagCounts = facet('tag')
  const tagOptions = Object.keys(tagCounts).filter((k) => (tagCounts[k] ?? 0) > 0 || chosen('tag')(k))
    .sort((a, b) => (tagCounts[b] ?? 0) - (tagCounts[a] ?? 0) || a.localeCompare(b))
    .map((k) => ({ key: k, label: k, count: tagCounts[k] ?? 0 }))
  const states = counted('state', STATES, chosen('state'))

  const multi = (key: string) => (value: string) => push({ [key]: value || null })
  const sections: FilterSectionDef[] = [
    { title: 'Kind', value: query.kind ?? '', defaultKey: '', options: kinds, onChange: multi('kind'), multi: true, said: saidOf(KINDS), column: 1 },
    { title: 'Made by', value: query.made ?? '', defaultKey: '', options: makers, onChange: multi('made'), multi: true, said: (k) => { const who = saidOf(MAKERS)(k); return who ? `made by ${who}` : '' }, column: 1 },
    {
      title: 'Project', value: query.project ?? '', defaultKey: '', options: projectOptions,
      onChange: (k) => push({ project: k || null, store: null }), limit: PROJECT_LIMIT, limitNoun: 'projects', column: 2,
    },
    {
      title: 'State', value: query.state ?? '', defaultKey: '', options: states, onChange: multi('state'), multi: true,
      said: saidOf(STATES), column: 2,
    },
    {
      title: 'Sort by', value: query.sort ?? '', defaultKey: '', quiet: true,
      options: SORTS.map((s) => ({ key: s.key === 'activity' ? '' : s.key, label: s.label })),
      onChange: (k) => push({ sort: k || null }), said: (k) => SORTS.find((s) => s.key === (k || 'activity'))?.said ?? '', column: 2,
    },
    {
      title: 'Collection', value: query.collection ?? '', defaultKey: '', options: collectionOptions,
      onChange: (k) => push({ collection: k || null }), limit: COLLECTION_LIMIT, limitNoun: 'collections',
      actions: [{ label: 'New collection', onSelect: inp.onNewCollection }],
    },
    {
      title: 'Tag', value: query.tag ?? '', defaultKey: '', options: tagOptions, onChange: multi('tag'), multi: true, quiet: true,
      said: (k) => `tagged ${joinWords(listOf(k))}`, limit: TAG_LIMIT, limitNoun: 'tags',
      actions: [{ label: 'Manage tags', onSelect: inp.onManageTags }],
    },
  ]
  if (inp.phone) {
    sections.push({
      title: 'Show as', value: inp.layout === 'grid' ? 'grid' : '', defaultKey: inp.layout === 'grid' ? 'grid' : '', quiet: true,
      options: [{ key: '', label: 'List' }, { key: 'grid', label: 'Grid' }],
      onChange: (k) => inp.setLayout(k === 'grid' ? 'grid' : 'list'),
    })
  }
  return sections
}
