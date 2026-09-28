import { describe, expect, it, vi } from 'vitest'

// The editor's Check counts what is not a reason a definition cannot save, and it called every one
// "a suggestion from the workflow conventions". A validator warning is not one: `WF_GATE_TEXT_UNSHOWN`
// (words on a gate its kind never shows) read as a style hint. Each source is named as itself now.

vi.mock('@monaco-editor/react', () => ({ default: () => null }))

const { adviceLine } = await import('./WorkflowDefEditor')

describe('the Check outcome’s advice line', () => {
  it('🔴 calls a validator warning a warning', () => {
    expect(adviceLine(1, 0)).toBe('1 warning — advice, not a reason it cannot save.')
    expect(adviceLine(2, 0)).toBe('2 warnings — advice, not a reason it cannot save.')
  })

  it('keeps the conventions’ suggestions named as theirs', () => {
    expect(adviceLine(0, 1)).toBe('1 suggestion from the workflow conventions — advice, not a reason it cannot save.')
    expect(adviceLine(1, 3)).toBe(
      '1 warning and 3 suggestions from the workflow conventions — advice, not a reason it cannot save.',
    )
  })
})
