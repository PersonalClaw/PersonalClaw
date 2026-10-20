import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// ── Issue 617: one toggle, one vocabulary ──────────────────────────────────────────
//
// The enable/disable toggle appeared under three different names — the grid card said
// "Activate", the ⋯ menu said "Install (activate)", and the detail panel said bare
// "Install" — and the panel's label misdescribed the effect: a deactivated app's files
// never left disk, so nothing gets installed. This rail pins the unified vocabulary at
// the SOURCE level (the three surfaces live in one file, and a label is a string —
// mounting the whole section to read three buttons would test the same characters
// through more machinery):
//
//  - the state toggle reads Activate / Deactivate on every surface;
//  - the old split labels are gone and cannot quietly return;
//  - "Install" survives ONLY where it describes a real store download.
//
// Comments are stripped before scanning so prose ABOUT the old labels can never
// satisfy or trip the rail (a source-scan counting its own commentary is a repeat
// trap in this suite).

const SRC = resolve(__dirname, 'AppsSection.tsx')

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

const code = stripComments(readFileSync(SRC, 'utf8'))

describe('one toggle, one vocabulary (issue 617)', () => {
  it('the split-vocabulary labels are gone from every surface', () => {
    expect(code).not.toContain('Install (activate)')
    expect(code).not.toContain('Uninstall (deactivate)')
  })

  it('every toggle surface renders the shared Activate/Deactivate pair', () => {
    // Menu row, context-menu row, and the detail panel each carry the ternary pair.
    const pair = /enabled\s*\?\s*'Deactivate'\s*:\s*'Activate'/g
    expect((code.match(pair) ?? []).length).toBeGreaterThanOrEqual(3)
    // The card's one-directional button (deactivated → Activate) is still present.
    expect(code).toContain('Activate</Button>')
  })

  it("the toggle never wears 'Install' or 'Uninstall' — those imply file movement", () => {
    // A toggle label is the string beside the enabled ternary; assert no toggle
    // ternary resolves to Install/Uninstall wording anymore.
    expect(code).not.toMatch(/enabled\s*\?\s*'Uninstall'\s*:\s*'Install'/)
    // "Force uninstall" (the real file-removal path) is deliberately kept.
    expect(code).toContain('Force uninstall')
  })
})
