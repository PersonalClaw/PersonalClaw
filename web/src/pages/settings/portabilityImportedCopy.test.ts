import { describe, expect, it } from 'vitest'
import { importedCopy } from './PortabilityPanel'

// ── What Settings → Import / Export says once an import is done ────────────────────────────────
//
// The toast said "Import complete: …" in the success tone whatever the merge had left as it was,
// a knowledge library it could not merge included. The gateway names each part it left unchanged;
// the lead counts them and the server's line for the store says why.

const restart = 'Restart the gateway to pick up everything the merge brought in.'

describe('Settings → Import / Export, a merge import', () => {
  it('says it is complete when every part came in', () => {
    expect(
      importedCopy({
        ok: true,
        summary: {
          mode: 'merge',
          items: ['memory (merged)', 'knowledge library (merged)', 'config (left unchanged: this home keeps its own)'],
          left_unchanged: [],
        },
        restart,
      }),
    ).toBe(
      'Import complete: memory (merged), knowledge library (merged), '
        + `config (left unchanged: this home keeps its own). ${restart}`,
    )
  })

  it('counts the part it could not bring in, and the line for it says why', () => {
    expect(
      importedCopy({
        ok: true,
        summary: {
          mode: 'merge',
          items: ['memory (merged)', "knowledge library (left unchanged: the archive's copy could not be merged)"],
          left_unchanged: ['workspace/knowledge/knowledge.db'],
        },
      }),
    ).toBe(
      'Import finished, but left 1 part unchanged: memory (merged), '
        + "knowledge library (left unchanged: the archive's copy could not be merged).",
    )
  })

  it('counts several parts', () => {
    expect(
      importedCopy({
        ok: true,
        summary: { mode: 'merge', items: ['a (merged)'], left_unchanged: ['memory.db', 'workspace/lexicon/lexicon.db'] },
      }),
    ).toBe('Import finished, but left 2 parts unchanged: a (merged).')
  })

  it('says when the archive held nothing to merge', () => {
    expect(importedCopy({ ok: true, summary: { mode: 'merge', items: [] } })).toBe('Import complete: nothing to merge.')
  })
})
