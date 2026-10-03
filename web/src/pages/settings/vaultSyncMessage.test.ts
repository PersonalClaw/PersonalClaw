import { describe, expect, it } from 'vitest'
import type { MemoryVaultSyncResult } from '../../lib/api'
import { vaultSyncMessage } from './vaultSyncMessage'

const result = (over: Partial<MemoryVaultSyncResult> = {}): MemoryVaultSyncResult => ({
  records: 12, files: 14, written: 0, pruned: 0, path: '/vault', mode: 'mirror',
  absorbed: 0, rejected: 0, conflicts: 0, seeded: 0, ...over,
})

describe('what Sync now says it did with the raw/ drop box', () => {
  it('says which dropped files were taken and which were refused', () => {
    expect(vaultSyncMessage(result({ raw_ingested: 2, raw_refused: 1 })))
      .toBe('Synced 12 records → 14 files (2 raw files → Knowledge, 1 raw file refused — see Knowledge)')
  })

  it('says a file still being copied in was left for the next sync, rather than nothing', () => {
    expect(vaultSyncMessage(result({ raw_waiting: 1 })))
      .toBe('Synced 12 records → 14 files (1 raw file still being written — taken at the next sync)')
  })

  it('says there were no changes when there were none', () => {
    expect(vaultSyncMessage(result({ raw_ingested: 0, raw_refused: 0, raw_waiting: 0 })))
      .toBe('Synced 12 records → 14 files (no changes)')
  })
})
