import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { connectorWarning } from './PacksPanel'

// ── A machine code shown to a person, against its own contract ────────────────────────────────────
//
// Measured on `#/settings/packs` with the bundled `health-os` pack installed: the installed-pack row's
// hint read
//
//   Unavailable: connector_missing:health-records
//
// `packs/connectors.py` says what that string is, in as many words: *"The machine-readable
// degraded-completion marker for a skipped connector … a stable code, **never prose**, so a UI can
// branch on it."* The row branched on it by printing it.
//
// Two defects, not one:
//   1. the code leaked into the interface, and
//   2. **"Unavailable" overstates it** — the pack installed fine and all eight of its components are
//      on the machine; one connector was skipped, which the backend itself calls *degraded*
//      completion. Reading "Unavailable" next to a working pack is worse than reading nothing.
//
// `MISSING_PREFIX` (`connector_missing:`) is the only marker shape that exists, so the parse is
// bounded. An unrecognised marker is still shown VERBATIM rather than dropped: a code nobody planned
// for is better read than hidden, and swallowing it would turn a new backend state into silence.

describe('the pack row says what is missing, in words', () => {
  it('names a single skipped connector without its prefix', () => {
    expect(connectorWarning(['connector_missing:health-records'])).toBe('Needs a connector: health-records')
  })

  it('pluralises and lists when several are skipped', () => {
    expect(connectorWarning(['connector_missing:health-records', 'connector_missing:calendar']))
      .toBe('Needs connectors: health-records, calendar')
  })

  it('says nothing when nothing is missing', () => {
    // The hint is `undefined`, not an empty string — `Row` renders no hint element at all, which is
    // the difference between "no warning" and "a blank warning".
    expect(connectorWarning([])).toBeUndefined()
  })

  it('shows an unrecognised marker verbatim instead of swallowing it', () => {
    // A future backend state must not vanish because this parser did not know it.
    expect(connectorWarning(['connector_broken:foo'])).toBe('connector_broken:foo')
    expect(connectorWarning(['connector_missing:a', 'something_else']))
      .toBe('Needs a connector: a · something_else')
  })

  it('never renders the raw prefix for a marker it understands', () => {
    // The defect, asserted directly: no output for a known marker may contain the code.
    for (const markers of [
      ['connector_missing:health-records'],
      ['connector_missing:a', 'connector_missing:b'],
    ]) {
      expect(connectorWarning(markers)).not.toContain('connector_missing:')
    }
  })

  it('the row renders through this function, and the old template is gone', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/PacksPanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src).toMatch(/hint=\{connectorWarning\(pack\.connector_markers\)\}/)
    expect(src, 'the raw join must not come back').not.toMatch(/Unavailable: \$\{pack\.connector_markers/)
  })
})
