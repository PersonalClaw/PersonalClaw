import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A group header must count what the group shows (#667) ────────────────────────────────────────
//
// The Agents page filters every group's rows through `match(q)`, and the no-match empty states
// branch on `n` — but the `count=` on each GroupSection read the UNFILTERED collection. Measured:
// searching `zzz-no-such-agent` rendered `Native | 8` directly above "No matching agents". The
// header and the body described two different lists.
//
// The rule this rail pins: every `count=` in this file consumes the same filtered collection the
// rows render from (`shownNative` for Native, `items` for a Discovered group) — never the raw
// `native.agents.length` / `g.agents.length`. And the Discovered no-match copy stays distinct
// from the empty-catalog copy, same as the Native group's `n` branch.
//
// Source-level because the groups are inline JSX in an unexported page body — the same reason
// agentRowCountsNamed.test.ts reads this file as text.

const FILE = join(import.meta.dirname, 'AgentsListPage.tsx')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const src = () => strip(readFileSync(FILE, 'utf8'))

describe('agents group headers count the filtered list they sit above (#667)', () => {
  it('reads the real file (not vacuously green)', () => {
    const s = src()
    expect(s).toContain('GroupSection')
    expect(s).toContain('No matching agents')
  })

  it('the Native header counts shownNative, never the raw catalog', () => {
    const s = src()
    expect(s).toContain('count={shownNative.length}')
    expect(s).not.toContain('count={native.agents.length}')
  })

  it('a Discovered header counts its filtered items, never the raw group', () => {
    const s = src()
    expect(s).toContain('count={items.length}')
    expect(s).not.toContain('count={g.agents.length}')
  })

  it('a Discovered search miss reads as a miss, not an empty catalog', () => {
    const s = src()
    expect(s).toMatch(/n \? 'No matching agents\.' : 'No agents discovered\.'/)
  })
})
