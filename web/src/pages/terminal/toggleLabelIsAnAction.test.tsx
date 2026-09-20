import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { persistToggleCopy } from '../../lib/persistClaim'

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
//
// 🔑 THE STRINGS MOVED TO `lib/persistClaim`, so this rail asserts in two places instead of one — the
// OWNER produces an imperative label and keeps the explanation out of it, and the PAGE wires all of the
// owner's verdict through. That split is deliberate: branching only the hint at the call site is what left
// this control clickable and `aria-pressed` true on a host with no tmux, which is the defect the owner
// exists to make unrepresentable. Reading the owner's real return value (not its source text) also means a
// reworded label cannot pass by matching a regex that no longer describes what renders.

const PAGE = join(process.cwd(), 'src', 'pages', 'terminal', 'TerminalPage.tsx')
const raw = readFileSync(PAGE, 'utf8')
// Comments quote the OLD label so the next reader sees what changed; strip them before matching.
const src = raw
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

describe('the terminal persistence toggle is labelled as an action', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(raw).toMatch(/HeaderControl icon=\{Anchor\}/)
    expect(raw.length).toBeGreaterThan(3000)
  })

  it('both states are imperative, and neither is a status sentence', () => {
    expect(persistToggleCopy(true, false).label).toBe('Enable persistent sessions')
    expect(persistToggleCopy(true, true).label).toBe('Disable persistent sessions')
    for (const c of [persistToggleCopy(true, true), persistToggleCopy(true, false)]) {
      expect(/Persistent sessions (on|off) —/.test(c.label), 'the label must not report state').toBe(false)
    }
  })

  it('the explanation lives in hint, so it survives without bloating the name', () => {
    // The page must render a `hint`, and the owner must be what fills it with the tmux detail.
    expect(src).toMatch(/hint=\{persistCopy\.hint\}/)
    for (const c of [persistToggleCopy(true, true), persistToggleCopy(true, false), persistToggleCopy(false, true)]) {
      expect(c.hint, 'the tmux detail must not be lost').toMatch(/tmux/)
      expect(c.label, 'and it must not migrate back into the name').not.toMatch(/tmux-backed|survive a restart/)
    }
  })

  it('state is still conveyed — via active, not via the words', () => {
    expect(src).toMatch(/active=\{persistCopy\.active\}/)
    // The owner, not the raw flag: `active` is `aria-pressed`, and it must be false for a saved
    // flag the host cannot honour — a lit toggle over an inert setting is the same lie the label
    // used to tell.
    expect(src, 'the lit state may not be read off the config flag').not.toMatch(/active=\{persist\}/)
    expect(persistToggleCopy(true, true).active).toBe(true)
    expect(persistToggleCopy(false, true).active).toBe(false)
  })

  it('matches the shape the Files header already uses', () => {
    // The precedent, asserted so a future "simplification" of either one shows up against the other.
    const files = readFileSync(join(process.cwd(), 'src', 'pages', 'files', 'FilesSection.tsx'), 'utf8')
    expect(files).toMatch(/label=\{explorerOpen \? 'Hide explorer' : 'Show explorer'\}/)
  })
})
