import type { MemoryVaultSyncResult } from '../../lib/api'

/** What a vault sync did, as Settings → Memory says it after Sync now: the records it wrote out
 *  and what changed, the files the vault's `raw/` drop box took into Knowledge, refused, and left
 *  because they are still being written (the next sync takes those whole), then each folder's own
 *  memory's vault. */
export function vaultSyncMessage(r: MemoryVaultSyncResult): string {
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
  const parts = [`Synced ${plural(r.records, 'record')} → ${plural(r.files, 'file')}`]
  if (r.written) parts.push(`${r.written} updated`)
  if (r.pruned) parts.push(`${r.pruned} pruned`)
  if (r.absorbed) parts.push(`${plural(r.absorbed, 'edit')} read back`)
  if (r.conflicts) parts.push(`${plural(r.conflicts, 'conflict')} — see Health`)
  if (r.raw_ingested) parts.push(`${plural(r.raw_ingested, 'raw file')} → Knowledge`)
  if (r.raw_refused) parts.push(`${plural(r.raw_refused, 'raw file')} refused — see Knowledge`)
  if (r.raw_waiting) parts.push(`${plural(r.raw_waiting, 'raw file')} still being written — taken at the next sync`)
  // Each folder's own memory is synced into a vault of its own beside it, and said too.
  const own = Object.values(r.folders ?? {})
  const sum = (k: 'records' | 'files') => own.reduce((n, f) => n + f[k], 0)
  const inFolders = own.length
    ? ` ${own.length === 1 ? "One folder's own memory" : `${own.length} folders' own memories`}: ${plural(sum('records'), 'record')} → ${plural(sum('files'), 'file')}.`
    : ''
  return (parts.length === 1 ? `${parts[0]} (no changes)` : `${parts[0]} (${parts.slice(1).join(', ')})`) + (inFolders ? `.${inFolders}` : '')
}
