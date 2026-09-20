import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The restore control must gate on `restorable_count`, not `dismissed_count` (#452) ─────────────
//
// Dismissing a Discover tip was terminal: one unconfirmed X, "persisted forever", no API and no
// reset. The way back is a clear-all — the user is never shown WHICH tips they hid, so a per-id
// restore would ask them to pick from an invisible set.
//
// 🔑 THE GATE IS THE WHOLE FINDING. A tip leaves the feed for two INDEPENDENT reasons — an explicit
// dismissal, and auto-hide once its area is used — so clearing a dismissal only reveals a tip whose
// area is ALSO still unengaged. A user who hid the Chat tip and then had a conversation has
// `dismissed_count: 1` and nothing to get back. Gating the button on `dismissed_count` therefore
// ships a control that writes the settings file, fires a refresh, and changes nothing on screen:
// the ruling on #452 names this outcome ("gating on `dismissed_count` ships an inert button").
// `restorable_count` is a separate server-computed field that exists only to be this gate.
//
// Source-read rather than rendered, for the reason the sibling `discoverHeadingLevel.test.ts` gives:
// the fact under test is which identifier guards the control, and a render test would pass just as
// well with the wrong one behind a hand-built fixture that happens to make both counts equal.
//
// Both placements are covered. The empty state is where the ruling put the count, but a user with
// two tips showing and five hidden never reaches that branch — and they are exactly the user who
// wants a dismissal back. So the loaded feed carries the control too, on the same gate.

const SRC = join(__dirname, 'DiscoverPage.tsx')
const API = join(__dirname, '..', '..', 'lib', 'api.ts')

describe('Discover restore control', () => {
  const src = readFileSync(SRC, 'utf8')

  it('gates every restore affordance on restorable_count', () => {
    const gates = src.match(/data\.restorable_count > 0/g) ?? []
    // Two placements: the empty state (hint + action) and the loaded feed.
    expect(gates.length).toBeGreaterThanOrEqual(3)
  })

  it('never gates a restore affordance on dismissed_count', () => {
    // `dismissed_count` legitimately still drives the empty state's "you hid N of M" COPY
    // (#3200). What it must never do is decide whether the restore control exists.
    const lines = src.split('\n')
    for (const [i, line] of lines.entries()) {
      if (!line.includes('dismissed_count')) continue
      // Look at the line and its two neighbours: a gate reads as a conditional next to the
      // control it guards.
      const window = lines.slice(i, i + 3).join(' ')
      expect(
        /restore|onClick|action=/i.test(window) && /dismissed_count\s*>\s*0/.test(line),
        `line ${i + 1} appears to gate a restore affordance on dismissed_count: ${line.trim()}`,
      ).toBe(false)
    }
  })

  it('calls the clear-all endpoint through reportingWrite, so a failure is not reported as a restore', () => {
    expect(src).toMatch(/reportingWrite\([\s\S]{0,80}api\.restoreDiscoverTips\(\)/)
    // And it refreshes, or the feed keeps showing the state it just changed.
    expect(src).toMatch(/restoreDiscoverTips\(\)[\s\S]{0,120}refresh\(\)/)
  })

  it('offers clear-all only — no per-id restore crept in', () => {
    expect(src).not.toMatch(/restoreDiscoverTip\(/)
    const api = readFileSync(API, 'utf8')
    expect(api).toMatch(/restoreDiscoverTips: \(\) =>/)
    // DELETE, on the dismiss path: one resource, two verbs.
    expect(api).toMatch(/'\/api\/legibility\/discover\/dismiss', \{ method: 'DELETE'/)
  })

  it('declares restorable_count on the response type', () => {
    const api = readFileSync(API, 'utf8')
    expect(api).toMatch(/restorable_count: number/)
  })
})
