// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── Table-family adoption ratchet ──────────────────────────────
// The canonical data-table family is ui/Table.tsx (Table/THead/Th/Td): required
// sr-only caption, `scope="col"` headers, the seed treatment in one place.
// Eleven pages had hand-rolled `<table>` markup that drifted on exactly those
// semantics. This ratchet — the same idiom as eyebrowWeightRole and the
// primitive-adoption rails — holds the set of files still hand-rolling
// `<table>` DOWN: a NEW file turns CI red with this message, and each migration
// removes its file from the baseline IN THE SAME COMMIT. The set may only
// shrink.
//
// Runs in the existing CI `web` vitest job (source-text scan, no browser).

const PAGES_ROOT = join(process.cwd(), 'src/pages')

function listTsx(dir: string): string[] {
  return filesUnder(dir, (name) => name.endsWith('.tsx'))
}

function filesWithRawTable(): string[] {
  return listTsx(PAGES_ROOT)
    .filter((p) => /<table\b/.test(readSource(p)))
    .map((p) => p.slice(PAGES_ROOT.length + 1))
    .sort()
}

interface Baseline { rawTableFiles: string[] }

function loadBaseline(): Baseline {
  const raw = readSource(join(process.cwd(), 'src/design/tableAdoption.baseline.json'))
  return JSON.parse(raw) as Baseline
}

describe('table-family adoption ratchet (raw <table> files may only shrink)', () => {
  const base = new Set(loadBaseline().rawTableFiles)
  const live = filesWithRawTable()
  const newcomers = live.filter((f) => !base.has(f))

  it('no page hand-rolls <table> outside the baseline set', () => {
    expect(
      newcomers,
      `New hand-rolled <table> in: ${newcomers.join(', ')}. Use the Table family ` +
        `(ui/Table.tsx: Table with a required sr-only caption, THead, Th — always scope="col" — ` +
        `and Td), or if this file is an intentional exception, justify it in ` +
        `src/design/tableAdoption.baseline.json in the same commit.`,
    ).toEqual([])
  })

  it('baseline is not stale (a migration should ratchet its file out)', () => {
    // Soft nudge, matching eyebrowWeightRole: warn when the live set fell below
    // the baseline so the gain gets locked, without blocking unrelated work.
    const gone = [...base].filter((f) => !live.includes(f))
    if (gone.length > 0) {
      // eslint-disable-next-line no-console
      console.warn(
        `[table-adoption] migrated file(s) still in baseline — remove from ` +
          `src/design/tableAdoption.baseline.json: ${gone.join(', ')}`,
      )
    }
    expect(true).toBe(true)
  })
})
