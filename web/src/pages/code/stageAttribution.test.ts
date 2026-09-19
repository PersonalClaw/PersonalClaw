import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { normalizeStageLabel } from './CodeCockpitPage'

// ── Issue 642: cycle findings attribute to tasks ────────────────────────────────────
//
// A finding's `stage` is unconstrained model output; exact equality against the plan's
// stage id/title attributed ZERO of a 12-cycle blocked loop's findings, so the cockpit
// said "No activity yet" on every task while the diagnosis sat invisible in cycle 10.
// The ingest now canonicalizes new findings; this normalize keeps every
// already-ledgered legacy label attributable, mirroring loop/files.py's regex.

describe('normalizeStageLabel (issue 642)', () => {
  it("strips all three observed model decorations to the bare title", () => {
    expect(normalizeStageLabel('1 — Write bell_times.py')).toBe('write bell_times.py')
    expect(normalizeStageLabel('2 — Verify & QA')).toBe('verify & qa')
    expect(normalizeStageLabel('Stage 2/2 — Verify & QA')).toBe('verify & qa')
  })

  it('handles colon and hyphen separators and plain labels', () => {
    expect(normalizeStageLabel('3: Verify & QA')).toBe('verify & qa')
    expect(normalizeStageLabel('Stage 1 - Implementation')).toBe('implementation')
    expect(normalizeStageLabel('implementation')).toBe('implementation')
  })

  it('never eats a title that merely starts with a number', () => {
    // No separator → not a decoration; the title survives intact.
    expect(normalizeStageLabel('2026 planning notes')).toBe('2026 planning notes')
  })
})

describe('the matcher uses the normalized fallback (wiring pin)', () => {
  it('stageTasksFor falls back to normalizeStageLabel on both title and stage id', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/code/CodeCockpitPage.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    const fn = src.slice(src.indexOf('const stageTasksFor'), src.indexOf('for (const f of'))
    expect(fn).toMatch(/normalizeStageLabel\(sg\.title\) === normalizeStageLabel\(fstage\)/)
    expect(fn).toMatch(/normalizeStageLabel\(sg\.stage \?\? ''\) === normalizeStageLabel\(fstage\)/)
  })
})
