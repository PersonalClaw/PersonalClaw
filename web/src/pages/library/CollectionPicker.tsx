import { Library as LibraryIcon, Plus } from 'lucide-react'
import type { KnowledgeCollection, LibraryItem } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { Modal } from '../../ui/Modal'
import { MenuRow } from '../../ui/Popover'
import { bulk } from './libraryActions'
import { newCollection } from './libraryCollections'
import { titleOf } from './libraryMeta'

/** Add to collection…: the manual collections, since a smart one fills itself, and New
 *  collection. Picking one adds the item, through the library's one write path. */
export function CollectionPicker({ item, collections, onClose, phoneSheet = false }: {
  item: LibraryItem
  collections: readonly KnowledgeCollection[]
  onClose: () => void
  phoneSheet?: boolean
}) {
  const title = titleOf(item)
  const manual = collections.filter((c) => c.kind !== 'smart')
  const inIt = new Set(item.collections.map((c) => c.id))
  const add = async (id: string, name: string) => {
    onClose()
    const failed = await bulk({ op: 'collect', refs: [item.ref], collection_id: id }).catch(() => [item.ref])
    notify(failed.length ? `Couldn't add “${title}” to ${name}.` : `Added “${title}” to ${name}.`, failed.length ? 'error' : 'success')
  }
  return (
    <Modal title={`Add “${title}” to a collection`} onClose={onClose} size="compact" phoneSheet={phoneSheet}>
      <div className="flex flex-col gap-0.5">
        {manual.map((c) => (
          <MenuRow key={c.id} label={c.name} hint={inIt.has(c.id) ? 'It is in it now.' : undefined} disabled={inIt.has(c.id)}
            icon={<LibraryIcon size={15} />} onClick={() => { if (!inIt.has(c.id)) void add(c.id, c.name) }} />
        ))}
        <MenuRow icon={<Plus size={15} />} label="New collection…"
          onClick={() => {
            onClose()
            void newCollection().then((id) => { if (id) void bulk({ op: 'collect', refs: [item.ref], collection_id: id }) })
          }} />
      </div>
    </Modal>
  )
}
