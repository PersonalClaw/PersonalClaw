import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { Toaster } from './Toaster'

// ── A word wider than the toast wraps inside it ───────────────────────────────────────────────────
//
// A toast's text broke only at spaces and hyphens, so a word wider than the card ran past its right
// edge, where the card cut it off. Measured on a twin: Resume all's toast for a file automation whose
// folder is not on this machine names the folder's path, and 152 px of it (310 px of box, 462 px of
// text) were hidden. The box now breaks such a word (`break-words`,
// `overflow-wrap: break-word`). jsdom lays nothing out, so this holds the rule the browser needs;
// the measurement above is what it buys.

vi.mock('../design/soundCues', () => ({
  playCue: () => {}, armCueAudio: () => {}, soundCuesEnabled: () => false, setSoundCuesEnabled: () => {},
}))

beforeEach(() => { cleanup() })

describe("a toast's text", () => {
  it('breaks a word wider than the card, such as a path, instead of running past its edge', () => {
    render(<Toaster />)
    const path = '/Users/ada/Documents/QuarterlyPlanningMeetingNotes/2026-Drafts/notes'
    act(() => {
      window.dispatchEvent(new CustomEvent('ne:toast', {
        detail: { level: 'error', message: `New notes is on. It watches ${path}, which is not on this machine.` },
      }))
    })
    // The card's text, not the live region's copy of it, which is the screen reader's.
    const text = screen.getAllByText(/New notes is on/).find((el) => el.hasAttribute('aria-hidden'))
    const box = text?.parentElement as HTMLElement
    expect(box.className.split(/\s+/)).toContain('break-words')
    expect(box.className.split(/\s+/)).toContain('min-w-0')
  })
})
