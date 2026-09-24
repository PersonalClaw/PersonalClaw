import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The tag taxonomy's only confirmation has to be announceable ────────────────────────────────
//
// Rename, nest, make-top-level, merge and delete give NO other success surface on this panel — the
// row just re-renders — so one line of text is the whole confirmation (WCAG 4.1.3 Status Messages).
//
// Measured in Chromium on a populated home before the change: **zero `role="status"` nodes in the
// panel at rest**, and after a rename the region appeared already carrying "Renamed “backup”". That
// is a region created at the same moment its content appears, which this app has already ruled
// unreliable in three separate places:
//
//   settingsUI's `SavedToast`   "A live region created at the same moment its content appears is not
//                                reliably observed" — and it is the canonical shape: always mounted,
//                                empty at rest, with the VISIBLE half `aria-hidden` so the sentence
//                                is not announced twice.
//   `ResultAnnouncement`        records the same reasoning for list results.
//   `AudioRecorder`             carries its own region, and its rail asserts the always-mounted shape
//                                in exactly these words.
//
// 🔑 WHAT THIS RAIL CAN AND CANNOT PROVE. It cannot prove an announcement — no AT in the harness, and
// `axe` has no rule for "was the user told?". What it pins is the STRUCTURE the announcement rides
// on, which is the part this repo already decided. The visible text is unchanged, so the fix is
// pixel-identical by construction.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/TagManager.tsx'), 'utf8')

describe('the tag panel announces what it just did', () => {
  it('the live region is always mounted, polite, and sr-only', () => {
    // Always mounted — NOT `{note && <… role="status">}`, which is the shape that was measured absent
    // at rest. The text is what changes; the region is what stays.
    expect(SRC).toMatch(/<span role="status" aria-live="polite" className="sr-only">\{note\}<\/span>/)
  })

  it('the region is not gated behind the note it carries', () => {
    // The defect, pinned: a `role="status"` inside a `{note && …}` branch is created with its content.
    expect(SRC, 'no conditionally-mounted status region remains')
      .not.toMatch(/\{note && \([\s\S]{0,120}role="status"/)
  })

  it('the visible line is hidden from the tree, so the sentence is announced once', () => {
    // SavedToast's second load-bearing detail: both in the tree means two announcements.
    expect(SRC).toMatch(/\{note && \([\s\S]{0,80}aria-hidden="true"/)
    // 🪤 Comments stripped first. The explanatory comment above the fix QUOTES the old
    // `role="status"` markup, and a raw count read that prose as a second live region — the
    // "a ratchet counts markup in comments" trap, which this repo has now hit four times.
    const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    const statuses = [...code.matchAll(/role="status"/g)]
    expect(statuses.length, 'exactly one status region in this panel').toBe(1)
  })

  it('every operation still feeds it a real sentence — the vacuity floor', () => {
    // A region that is always mounted and always EMPTY would satisfy every assertion above while
    // announcing nothing. All four operations pinned by their label, not by a count.
    expect(SRC, 'rename').toMatch(/run\(t\.id, `Renamed “\$\{t\.name\}”`/)
    expect(SRC, 'make top-level / nest').toMatch(/is now top-level` : `Moved “\$\{t\.name\}”`/)
    expect(SRC, 'merge').toMatch(/`Merged “\$\{t\.name\}” into “\$\{into\.name\}”`/)
    expect(SRC, 'delete').toMatch(/run\(t\.id, `Deleted “\$\{t\.name\}”`/)
    // And the note the region renders is the label `run` was handed — not a separate string.
    expect(SRC).toMatch(/setNote\(label\)/)
  })

  it('matches the shape AudioRecorder is already held to', () => {
    // Same file tree, same rule, same words — if that sibling's contract changes, this should too.
    const sibling = readFileSync(join(process.cwd(), 'src/pages/knowledge/AudioRecorder.tsx'), 'utf8')
    expect(sibling, 'the precedent still ships the always-mounted region')
      .toMatch(/role="status" aria-live="polite"[^>]*sr-only|sr-only[^>]*role="status"/)
  })
})
