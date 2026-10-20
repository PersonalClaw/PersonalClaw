import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The coral row rail means "active", not "native" ─────────────────────────────────────────────
//
// `ListRow`'s `accent` paints a 3px coral rail down the row's left edge. It was hard-coded onto
// EVERY native agent row (`accent="var(--color-primary)"`), so all seven built-ins wore it at once —
// which makes it a GROUP marker, a job the "Native N" header and the coral icon square already do.
// Coral is the One Voice signal ("this is the agent / this is active"), so coral on a row that is not
// active is a One Voice Rule violation, and a 3px coloured side-stripe is exactly the decoration
// DESIGN.md's Tone-Not-Line rule warns against.
//
// The rail's only other shipped uses gate it on a live state: `InboxPage` lights it for UNREAD items,
// `TriggersListPage` for ENABLED triggers — always "this row needs you / is live". The active agent's
// analogue is the single global default, so the rail now rides `isDefault` and reads the same way
// everywhere. The default row keeps its star; every other row keeps the coral icon square that says
// "agent". This is the DECLARED way to test `NativeRow`: it is a local (non-exported) function, so —
// like `agentRowCountsNamed.test.ts` and `ui/danglingSeparator.test.ts` — the rail reads the source.

const SRC = join(import.meta.dirname, '..', '..')
const stripComments = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => stripComments(readFileSync(join(SRC, rel), 'utf8'))

describe('the agent row coral rail signals the active agent, not the native group', () => {
  const src = read('pages/agents/AgentsListPage.tsx')

  // The slice under test: NativeRow only. DiscoveredRow (runtime agents) is a separate function that
  // carries no accent at all, and must not be swept into these assertions.
  const nativeStart = src.indexOf('function NativeRow(')
  const nativeEnd = src.indexOf('function DiscoveredRow(')
  const nativeRow = src.slice(nativeStart, nativeEnd)

  it('reads the real NativeRow (not vacuously green)', () => {
    expect(nativeStart, 'NativeRow moved — this rail measures nothing').toBeGreaterThan(-1)
    expect(nativeEnd, 'DiscoveredRow moved — the slice is unbounded').toBeGreaterThan(nativeStart)
    expect(nativeRow, 'NativeRow must still render a ListRow to accent').toMatch(/<ListRow\b/)
    // The prop the row already receives to make this decision — pin it so the rail cannot be
    // satisfied by simply deleting the accent and forgetting the default agent exists.
    expect(nativeRow, 'NativeRow must still know which agent is the default').toMatch(/\bisDefault\b/)
  })

  it('gates the coral accent on isDefault', () => {
    expect(nativeRow, 'the coral rail must ride isDefault, so it means "active", not "native"').toMatch(
      /accent=\{\s*isDefault\s*\?\s*'var\(--color-primary\)'\s*:\s*undefined\s*\}/,
    )
  })

  it('no longer paints the rail on every native row unconditionally', () => {
    expect(nativeRow, 'the unconditional coral rail is the One Voice violation this fix removes').not.toMatch(
      /accent="var\(--color-primary\)"/,
    )
  })

  it('leaves the runtime (discovered) rows with no rail', () => {
    const discovered = src.slice(nativeEnd)
    expect(discovered, 'DiscoveredRow must exist for this assertion to mean anything').toMatch(/<ListRow\b/)
    expect(discovered, 'runtime agents are read-only and carry no coral accent').not.toMatch(/accent=/)
  })
})
