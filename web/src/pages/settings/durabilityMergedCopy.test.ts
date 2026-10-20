import { describe, expect, it } from 'vitest'
import { mergedCopy } from './DurabilityPanel'

// ── What the Archive's Merge-restore says once it is done ──────────────────────────────────────
//
// The toast said "Merged <snapshot>." whenever the gateway answered, including after a merge that
// had left the whole knowledge library as it was. The gateway names each part it left unchanged,
// and the toast says them.

describe('Settings → Backups, a merge restore', () => {
  it('says it merged when every part came in', () => {
    expect(mergedCopy('personalclaw-snapshot-20260930T070105Z.tar.gz', { ok: true, left_unchanged: [], restart: 'Restart the gateway.' }))
      .toBe('Merged personalclaw-snapshot-20260930T070105Z.tar.gz. Restart the gateway.')
  })

  it('names the part it left unchanged', () => {
    expect(mergedCopy('snap.tar.gz', { ok: true, left_unchanged: ['workspace/knowledge/knowledge.db'] }))
      .toBe('Merged snap.tar.gz, but left 1 part unchanged: workspace/knowledge/knowledge.db.')
  })

  it('counts the parts it left unchanged', () => {
    expect(mergedCopy('snap.tar.gz', { ok: true, left_unchanged: ['memory.db', 'learning.db (items)'] }))
      .toBe('Merged snap.tar.gz, but left 2 parts unchanged: memory.db, learning.db (items).')
  })
})
