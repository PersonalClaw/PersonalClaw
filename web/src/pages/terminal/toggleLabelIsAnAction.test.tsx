import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A header toggle's label names the ACTION, not the state ───────────────────────
//
// `HeaderControl`'s `label` is three things at once: the visible text at the FULL tier, the accessible
// name at every tier, and the tooltip at the ICON tier. The terminal's persistence toggle used it as a
// status sentence:
//
//   off → "Persistent sessions off — enable tmux-backed survival"
//   on  → "Persistent sessions on — survive a restart (tmux)"
//
// Measured at 1440px: that made it **387px** wide — 3x the widest sibling header control in the app
// (`Sync agents`, 129px) and wider than the page's primary action (`New terminal session`, 183px) — so it
// was the first thing the header's FULL → TEXT → ICON → OVERFLOW ladder had to demote. And a screen-reader
// user heard the current state plus an instruction rather than an action; "Persistent sessions on" does
// not say whether activating turns it off.
//
// The app's own answer is the verb flip, with `active` carrying the state visually: `FilesSection` renders
// `label={explorerOpen ? 'Hide explorer' : 'Show explorer'}`. After: **217px** (-44%), and the explanation
// moved to `hint`, which is what the overflow menu shows as its secondary line.

const SRC = join(process.cwd(), 'src', 'pages', 'terminal', 'TerminalPage.tsx')
const raw = readFileSync(SRC, 'utf8')
// Comments quote the OLD label so the next reader sees what changed; strip them before matching.
const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the terminal persistence toggle is labelled as an action', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(raw).toMatch(/HeaderControl icon=\{Anchor\}/)
    expect(raw.length).toBeGreaterThan(3000)
  })

  it('both states are imperative, and neither is a status sentence', () => {
    expect(src).toMatch(/label=\{persist \? 'Disable persistent sessions' : 'Enable persistent sessions'\}/)
    expect(/Persistent sessions (on|off) —/.test(src), 'the label must not report state').toBe(false)
  })

  it('the explanation lives in hint, so it survives without bloating the name', () => {
    expect(src).toMatch(/hint=\{persist/)
    expect(src, 'the tmux detail must not be lost').toMatch(/tmux/)
  })

  it('state is still conveyed — via active, not via the words', () => {
    expect(src).toMatch(/active=\{persist\}/)
  })

  it('matches the shape the Files header already uses', () => {
    // The precedent, asserted so a future "simplification" of either one shows up against the other.
    const files = readFileSync(join(process.cwd(), 'src', 'pages', 'files', 'FilesSection.tsx'), 'utf8')
    expect(files).toMatch(/label=\{explorerOpen \? 'Hide explorer' : 'Show explorer'\}/)
  })
})
