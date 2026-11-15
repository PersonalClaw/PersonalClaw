import { api, type KnowledgeCollection } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { reportActionFailure, reportingWrite } from '../../app/reportingWrite'
import { confirm, promptInput } from '../../ui/dialog'
import { n } from './libraryWords'

// ── Collections, in the regular view ─────────────────────────────────────────────────
//
// New collection asks for a name, then the search that fills it (empty makes a manual one, a search a
// smart one); Rename and Delete collection are the count line's while the list is filtered to one.
// Deleting a collection keeps its items.

const changed = () => { invalidateKeys('knowledge:collections'); invalidateKeys('knowledge:library', true) }

/** Make a collection; answers its id, or '' when the owner stopped or it failed (which says so). */
export async function newCollection(): Promise<string> {
  const name = await promptInput({ title: 'New collection', label: 'Collection name', placeholder: 'Trips', confirmLabel: 'Next' })
  if (!name?.trim()) return ''
  const q = await promptInput({
    title: 'New collection',
    label: 'Search that fills it (leave empty to add items by hand)',
    placeholder: 'rust ownership',
    confirmLabel: 'Create collection',
  })
  if (q === null) return ''
  const res = await api.createKnowledgeCollection(q.trim() ? { name: name.trim(), kind: 'smart', query: q.trim() } : { name: name.trim() })
    .catch(reportActionFailure(`create the collection “${name.trim()}”`))
  if (!res) return ''
  changed()
  return res.collection.id
}

export async function renameCollection(c: Pick<KnowledgeCollection, 'id' | 'name'>): Promise<void> {
  const name = await promptInput({ title: `Rename “${c.name}”`, label: 'Collection name', initial: c.name, confirmLabel: 'Rename' })
  if (!name?.trim() || name.trim() === c.name) return
  if (await reportingWrite(`rename “${c.name}”`, () => api.updateKnowledgeCollection(c.id, { name: name.trim() }))) changed()
}

/** Delete a collection, keeping its items; answers whether it went. */
export async function deleteCollection(c: Pick<KnowledgeCollection, 'id' | 'name'>, count: number): Promise<boolean> {
  const ok = await confirm({
    title: `Delete “${c.name}”?`,
    body: count === 1 ? 'Its 1 item stays in your library.' : `Its ${n(count)} items stay in your library.`,
    confirmLabel: 'Delete collection',
    danger: true,
  })
  if (!ok) return false
  const done = await reportingWrite(`delete the collection “${c.name}”`, () => api.deleteKnowledgeCollection(c.id))
  changed()
  return done
}
