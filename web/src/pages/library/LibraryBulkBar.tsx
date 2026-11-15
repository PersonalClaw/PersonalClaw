import { useState } from 'react'
import { Archive, ArchiveRestore, BookOpenCheck, FolderInput, FolderOpen, Library as LibraryIcon, MoreHorizontal, Pin, Trash2, X } from 'lucide-react'
import { api, type KnowledgeCollection, type LibraryBulkBody, type LibraryItem } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { notify } from '../../app/appSdk'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { MenuRow, Popover } from '../../ui/Popover'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { confirm } from '../../ui/dialog'
import { ProjectPickerDialog } from '../../ui/ProjectPicker'
import { n } from './libraryWords'

// ── Bulk ─────────────────────────────────────────────────────────────────────────────
//
// "3 selected": Pin · Add to collection · Archive (Restore while the selection is archived) · Delete… ·
// ⋯ (Add to project…, Mark as read, Mark as unread, Remove from <collection>) · Clear. Every act goes
// through `POST /api/library/bulk`, at most `BULK_MAX` refs a call, and its answer says how many were
// done and why the rest weren't.

/** The route's bound on refs in one call (`library_writes.BULK_MAX`). */
export const BULK_MAX = 500

/** Why a ref was left out, in words, by the op that left it out. */
const SKIPPED: Partial<Record<LibraryBulkBody['op'], string>> = {
  project: 'findings and reports have no project',
  delete: 'findings and reports are deleted from their set-up',
  read_state: 'pages have no read state',
  collect: 'findings have no collection',
}

/** Run one op over `refs`, in calls of `BULK_MAX`; answers the refs that failed, with why. */
export async function bulkAll(body: LibraryBulkBody): Promise<{ failed: string[]; notApplicable: number }> {
  const failed: string[] = []
  let notApplicable = 0
  for (let i = 0; i < body.refs.length; i += BULK_MAX) {
    const refs = body.refs.slice(i, i + BULK_MAX)
    const part = { ...body, refs, ...(body.op === 'delete' ? { expect: refs.length } : {}) } as LibraryBulkBody
    try {
      const { results } = await api.libraryBulk(part)
      for (const r of results) if (!r.ok) { failed.push(r.ref); if (r.error === 'not_applicable') notApplicable++ }
    } catch { failed.push(...refs) }
  }
  invalidateKeys('knowledge', true)
  invalidateKeys('artifacts', true)
  return { failed, notApplicable }
}

/** "3 items", "1 item". */
export const items = (count: number) => `${n(count)} ${count === 1 ? 'item' : 'items'}`

/** What a finished act says: its own sentence when every ref was done ("Archived 3 items."), else
 *  "2 of 3 done. 1 couldn't be changed: findings and reports have no project." */
export function bulkWords(done: (what: string) => string, total: number, failed: number, notApplicable: number, op: LibraryBulkBody['op']): string {
  if (!failed) return done(items(total))
  const why = notApplicable === failed && SKIPPED[op] ? `: ${SKIPPED[op]}.` : '.'
  return `${n(total - failed)} of ${n(total)} done. ${n(failed)} couldn't be changed${why}`
}

export function LibraryBulkBar({ checked, rows, total, collections, collectionsReason, collection, onSelectAll, onClear, offReason }: {
  /** The refs selected. */
  checked: ReadonlySet<string>
  /** The rows the list holds now, for what the selection is. */
  rows: readonly LibraryItem[]
  /** How many match the list's filter: "Select all 342 matches" past what is selected. */
  total: number
  /** The manual collections, for Add to collection. */
  collections: readonly KnowledgeCollection[]
  /** Why there are none to offer yet: the collections read is loading or failed. */
  collectionsReason?: string
  /** The manual collection the list is filtered to, for Remove from it. */
  collection?: { id: string; name: string } | null
  onSelectAll: () => void
  onClear: () => void
  offReason?: string
}) {
  const [picking, setPicking] = useState(false)
  const refs = [...checked]
  const count = refs.length
  const selected = rows.filter((r) => checked.has(r.ref))
  const archived = selected.length > 0 && selected.every((r) => r.archived)
  const allPinned = selected.length > 0 && selected.every((r) => r.pinned)
  const manual = collections.filter((c) => c.kind !== 'smart')
  const run = async (body: LibraryBulkBody, done: (what: string) => string) => {
    const { failed, notApplicable } = await bulkAll(body)
    notify(bulkWords(done, body.refs.length, failed.length, notApplicable, body.op), failed.length ? 'error' : 'success')
    if (!failed.length) onClear()
  }
  const off = !!offReason || count === 0
  const reason = offReason ?? 'Select something first.'
  const deleteAll = async () => {
    const ok = await confirm({
      title: `Delete ${n(count)} ${count === 1 ? 'item' : 'items'}?`,
      body: "This deletes them, every version of the pages among them, and the copies of the files your library keeps. You can't undo this.",
      confirmLabel: 'Delete', danger: true,
    })
    // A findings log or a report is refused by the route (`not_applicable`) and counted as skipped.
    if (ok) await run({ op: 'delete', refs, expect: count }, (w) => `Deleted ${w}.`)
  }
  return (
    <div role="toolbar" aria-label={`${n(count)} selected`} className="sticky top-0 z-10 flex flex-wrap items-center gap-xs border-b border-outline-variant/40 bg-surface px-l py-s">
      <p data-type="label-l" role="status" className="mr-s text-on-surface">{n(count)} selected</p>
      {total > count && <TextLink size="sm" onClick={onSelectAll} className={TOUCH_TARGET}>Select all {n(total)} matches</TextLink>}
      <span className="flex-1" />
      <Button variant="ghost" size="sm" disabled={off} disabledReason={reason} className={TOUCH_TARGET}
        onClick={() => void run({ op: 'pin', refs, value: !allPinned }, (w) => `${allPinned ? 'Unpinned' : 'Pinned'} ${w}.`)}>
        <Pin size={14} aria-hidden /> {allPinned ? 'Unpin' : 'Pin'}
      </Button>
      <Popover portal placement="bottom" align="right" width={280} phoneSheet
        trigger={(open, toggle) => (
          <Button variant="ghost" size="sm" onClick={toggle} ariaExpanded={open} disabled={off || !manual.length}
            disabledReason={off ? reason: collectionsReason ?? 'Make a collection first.'} className={TOUCH_TARGET}>
            <LibraryIcon size={14} aria-hidden /> Add to collection
          </Button>
        )}>
        {(close) => (
          <div className="flex flex-col gap-0.5">
            {manual.map((c) => (
              <MenuRow key={c.id} icon={<LibraryIcon size={15} />} label={c.name}
                onClick={() => { close(); void run({ op: 'collect', refs, collection_id: c.id }, (w) => `Added ${w} to ${c.name}.`) }} />
            ))}
          </div>
        )}
      </Popover>
      <Button variant="ghost" size="sm" disabled={off} disabledReason={reason} className={TOUCH_TARGET}
        onClick={() => void run({ op: archived ? 'restore' : 'archive', refs }, (w) => `${archived ? 'Restored' : 'Archived'} ${w}.`)}>
        {archived ? <ArchiveRestore size={14} aria-hidden /> : <Archive size={14} aria-hidden />} {archived ? 'Restore' : 'Archive'}
      </Button>
      <Button variant="ghost" size="sm" disabled={off} disabledReason={reason} className={TOUCH_TARGET} onClick={() => void deleteAll()}>
        <Trash2 size={14} aria-hidden /> Delete…
      </Button>
      <Popover portal placement="bottom" align="right" width={280} phoneSheet
        trigger={(open, toggle) => <IconButton icon={MoreHorizontal} label={`More for ${n(count)} selected`} onClick={toggle} expanded={open} disabled={off} disabledReason={reason} className={TOUCH_TARGET} />}>
        {(close) => (
          <div className="flex flex-col gap-0.5">
            <MenuRow icon={<FolderOpen size={15} />} label="Add to project…" onClick={() => { close(); setPicking(true) }} />
            <MenuRow icon={<BookOpenCheck size={15} />} label="Mark as read" onClick={() => { close(); void run({ op: 'read_state', refs, state: 'read' }, (w) => `Marked ${w} as read.`) }} />
            <MenuRow icon={<BookOpenCheck size={15} />} label="Mark as unread" onClick={() => { close(); void run({ op: 'read_state', refs, state: 'unread' }, (w) => `Marked ${w} as unread.`) }} />
            {collection && (
              <MenuRow icon={<FolderInput size={15} />} label={`Remove from ${collection.name}`}
                onClick={() => { close(); void run({ op: 'uncollect', refs, collection_id: collection.id }, (w) => `Removed ${w} from ${collection.name}.`) }} />
            )}
          </div>
        )}
      </Popover>
      <Button variant="ghost" size="sm" onClick={onClear} className={TOUCH_TARGET}><X size={14} aria-hidden /> Clear</Button>
      {picking && (
        <ProjectPickerDialog itemTitle="" heading={`Add ${n(count)} ${count === 1 ? 'item' : 'items'} to a project`} current={null} phoneSheet
          onClose={() => setPicking(false)}
          onPick={(to) => void run({ op: 'project', refs, project_id: to?.id ?? '' }, (w) => (to ? `Added ${w} to ${to.name}.` : `Took ${w} out of their projects.`))} />
      )}
    </div>
  )
}
