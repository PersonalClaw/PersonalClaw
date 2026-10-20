import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The persistence toggle's revert is honest, but silence was not ──────────────────────────────
//
// `togglePersist` flips optimistically and reverts on failure — correct for a data-driven
// switch, per the gating taxonomy in `userActionReported.test.ts`. What the 2026-09-05 audit
// flagged is the SILENT half: a user who enabled persistence and looked away believed their
// sessions were tmux-backed, and the flip-back said nothing. Both halves are pinned: the revert
// stays (removing it would leave a lying control) and the failure reaches the page's own
// `error` surface — the same one a refused create uses, which is why this lives here and not
// in the shared-reporter census.
describe('the terminal persistence toggle reports failure', () => {
  it('reverts AND reports through the page error surface', () => {
    const src = readFileSync(join(process.cwd(), 'src', 'pages/terminal/TerminalPage.tsx'), 'utf8')
    const at = src.indexOf('const togglePersist')
    expect(at, 'the toggle must exist').toBeGreaterThan(-1)
    const fn = src.slice(at, at + 700)
    expect(fn, 'the optimistic flip must still revert on failure').toMatch(/setPersist\(!next\)/)
    expect(fn, 'and the failure must speak').toMatch(/setError\(`Couldn't (\$\{next \? 'enable' : 'disable'\}|enable|disable) persistent sessions/)
  })
})
