import { describe, expect, it } from 'vitest'
import { reentrySummary, revalidateNotice, revalidateSummary } from './revalidate'

// ── Mid-flight-edit re-validate warning (LOOPS-EVOLUTION R10b / criterion 9) ──
//
// A bundled template's judge calibration is tuned to the prompts it shipped with, so editing
// a stage's prompt on a live run can silently invalidate it. The notice surfaces that before
// applying; the summary restates it after, sized to the cascade. Pure text + a pure summary,
// so the wording lives in one reviewable place.

describe('revalidateNotice', () => {
  it('names re-validation as the thing to do after resuming', () => {
    expect(revalidateNotice).toMatch(/re-validate/i)
    // It has to explain WHY, or it reads as boilerplate the user dismisses.
    expect(revalidateNotice).toMatch(/calibrat/i)
  })
})

describe('revalidateSummary', () => {
  it('names the re-run count so the user sees how much the edit invalidated', () => {
    expect(revalidateSummary({ rerun: ['a', 'b', 'c'], stale: [], skipped: [], committed_effects: [], needs_confirmation: false }))
      .toBe('Edit applied — 3 steps will re-run. Re-validate this template’s judge calibration.')
  })

  it('singularizes a one-step cascade', () => {
    expect(revalidateSummary({ rerun: ['a'], stale: [], skipped: [], committed_effects: [], needs_confirmation: false }))
      .toMatch(/1 step will re-run/)
  })

  it('names committed effects after an edit instead of reducing them to the re-run count', () => {
    const s = revalidateSummary({
      rerun: ['draft', 'publish'],
      stale: [],
      skipped: [],
      committed_effects: ['publish'],
      needs_confirmation: true,
    })
    expect(s).toMatch(/1 step already committed effects \(publish\)/)
    expect(s).toMatch(/may fire tools again/)
  })

  it('still asks to re-validate when nothing re-runs', () => {
    const s = revalidateSummary({ rerun: [], stale: [], skipped: [], committed_effects: [], needs_confirmation: false })
    expect(s).toBe('Edit applied. Re-validate this template’s judge calibration.')
  })

  it('tolerates a missing preview', () => {
    expect(revalidateSummary(null)).toMatch(/Re-validate/)
    expect(revalidateSummary(undefined)).toMatch(/Re-validate/)
  })
})

describe('reentrySummary', () => {
  const preview = {
    rerun: ['gather', 'publish'],
    stale: [],
    skipped: [],
    committed_effects: ['publish'],
    needs_confirmation: true,
  }

  it('names the committed effects for rewind', () => {
    expect(reentrySummary('rewind', preview))
      .toBe('Re-running this node will reset 2 steps. 1 step already committed effects (publish); re-running may fire tools again.')
  })

  it('names the committed effects for run-from', () => {
    expect(reentrySummary('run-from', preview))
      .toBe('Running from this node will reset 2 steps. 1 step already committed effects (publish); re-running may fire tools again.')
  })
})
