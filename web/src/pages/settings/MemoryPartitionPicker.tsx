import { createContext, useContext, useState } from 'react'
import { FolderX, Trash2 } from 'lucide-react'
import { api, type MemoryPartition } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { Button } from '../../ui/Button'
import { Select } from '../../ui/forms'
import { InlineLoadError } from '../../ui/ListScaffold'
import { confirmDestructive } from '../../ui/dialog'
import { notify } from '../../app/appSdk'

/** Which memory Settings → Memory shows and manages: "" for the memory every chat shares, else a
 *  folder's own (`MemoryPartition.id`, from `?partition=`). Chats working in a folder of their own
 *  keep what they learn in that folder's memory, so a page that read only the shared one left
 *  every folder's facts, lessons and episodes out of sight and out of reach. Every read and write
 *  the panel makes names the memory it is about, and so does every query key (`partitionKey`), so
 *  one memory's list never answers for another's. */
export const MemoryPartitionContext = createContext('')
export const useMemoryPartition = () => useContext(MemoryPartitionContext)

/** The cache key of one memory's read: the shared memory keeps the key it always had. */
export const partitionKey = (key: string, partition: string) => (partition ? `${key}@${partition}` : key)

export const MEMORY_PARTITIONS_KEY = 'settings:memory-partitions'

/** A memory's name in the picker: the shared one, or the folder it is the memory of. */
export function partitionLabel(p: MemoryPartition): string {
  if (p.global) return 'Every chat (shared memory)'
  const named = p.folder || `A folder no record names (${p.id})`
  return p.gone ? `${named} (folder gone)` : named
}

/** What the picked memory is, in a sentence: whose it is, what it holds, and when its folder is
 *  gone, that nothing reads it any more. */
export function partitionSentence(p: MemoryPartition): string {
  const held = `${p.semantic} fact${p.semantic === 1 ? '' : 's'} and lesson${p.semantic === 1 ? '' : 's'}, ${p.episodic} episode${p.episodic === 1 ? '' : 's'}`
  if (p.global) {
    return `What every chat recalls; a chat working in a folder of its own reads its folder's memory first, then this one. It holds ${held}.`
  }
  const projects = p.projects.length
    ? ` It is the memory of ${p.projects.map((q) => q.name).join(', ')}, whose chats and runs read it.`
    : ''
  if (p.gone) {
    return `${p.folder || 'Its folder'} is no longer there, so no chat reads this memory any more. It holds ${held}: look through it, or remove it.`
  }
  if (!p.folder) {
    return `No record names the folder this memory was kept for, so no chat can be said to read it. It holds ${held}.`
  }
  return `What chats working in ${p.folder} keep, and recall before the shared memory. It holds ${held}.${projects}`
}

/** "Memory of: …" — every memory she has, the shared one first, each folder's named by its folder,
 *  and the one picked described, with a way to remove a folder's. Hidden while the shared memory is
 *  the only one, since there is nothing to pick between. */
export function MemoryPartitionPicker({ partition, onPick }: { partition: string; onPick: (id: string) => void }) {
  const { data: parts, error, refresh } = useQuery(MEMORY_PARTITIONS_KEY, () => api.memoryPartitions(), { persist: false })
  const [removing, setRemoving] = useState(false)
  if (!parts) {
    return error ? <div className="mb-l"><InlineLoadError what="your folders' memories" error={error} onRetry={refresh} /></div> : null
  }
  const current = parts.find((p) => p.id === partition)
  if (parts.length < 2 && !partition) return null
  const options = parts.map((p) => ({ value: p.id, label: partitionLabel(p) }))
  if (!current) options.push({ value: partition, label: 'A memory that is no longer there' })

  const remove = async (p: MemoryPartition) => {
    const named = p.folder || `the folder no record names (${p.id})`
    if (!(await confirmDestructive(
      `Remove the memory of ${named}?`,
      `Its ${p.semantic} facts and lessons, its ${p.episodic} episodes, and the documents chats working in that folder kept are removed. A chat working there again starts with nothing of its own; the shared memory every chat reads is not touched. This cannot be undone.`,
      { confirmLabel: 'Remove' },
    ))) return
    setRemoving(true)
    try {
      await api.removeMemoryPartition(p.id)
      notify(`Removed the memory of ${named}.`, 'success')
      invalidateKeys(MEMORY_PARTITIONS_KEY)
      onPick('')
      refresh()
    } catch (e) {
      notify(`Couldn't remove the memory of ${named}: ${String((e as Error)?.message || e)}`, 'error')
    }
    setRemoving(false)
  }

  return (
    <div className="mb-l flex flex-col gap-s rounded-lg bg-surface-container px-m py-s">
      <div className="flex flex-wrap items-center gap-s">
        <span data-type="label-s" className="text-on-surface">Memory of</span>
        <div className="min-w-0 flex-1 sm:max-w-[26rem]">
          <Select value={partition} onChange={onPick} options={options} ariaLabel="Which memory to show" size="sm" surface="high" />
        </div>
        {current && !current.global && (
          <Button variant="ghost" size="sm" onClick={() => remove(current)} loading={removing} loadingLabel="Removing…">
            <Trash2 size={14} /> Remove this memory
          </Button>
        )}
      </div>
      {current ? (
        <p data-type="caption" className="flex items-start gap-xs text-on-surface-low">
          {current.gone && <FolderX size={13} className="mt-0.5 shrink-0 text-warn" aria-hidden="true" />}
          <span>{partitionSentence(current)}</span>
        </p>
      ) : (
        <p role="alert" data-type="caption" className="text-warn">
          This memory is not there any more: it was removed. Pick another one above.
        </p>
      )}
    </div>
  )
}
