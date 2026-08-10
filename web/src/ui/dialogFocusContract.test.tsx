import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── aria-modal is a PROMISE that focus is owned ─────────────────────────────────
//
// `aria-modal="true"` tells assistive tech that everything outside the dialog is unavailable. If Tab
// still reaches the page behind the scrim, the markup is lying — and a keyboard user tabs into
// controls they cannot see.
//
// Census of every dialog-role surface in web/src:
//
//   ui/Modal.tsx                    aria-modal + useFocusTrap        ✓
//   ui/dialog/DialogShell.tsx       aria-modal + useFocusTrap        ✓
//   ui/UpdateProgressOverlay.tsx    aria-modal, NO trap              ✗ fixed here
//   ui/DegradedChip.tsx             role=dialog, no aria-modal       distinction (a popover)
//   ui/NavRail.tsx                  role=dialog, no aria-modal       distinction (a drawer, `inert`)
//
// The two without `aria-modal` are deliberate: neither claims to own focus, and NavRail marks the
// page behind it `inert` instead. Only a surface that DECLARES aria-modal is held to the trap.
//
// Measured on the live DOM, driving a faked in-flight update through GET /api/status:
//
//     BEFORE   focus on open: BODY (never entered the dialog)
//              Tab escapes:   YES after 1 press → the nav's "Home" button, behind the scrim
//     AFTER    focus on open: BUTTON "Cancel", inDialog=true
//              Tab escapes:   no (trapped)
//
// The fix is a component SPLIT, not just a ref. `useFocusTrap` is a mount/unmount contract — its
// effect has a `[]` dep list and it captures the previously-focused element during its FIRST RENDER.
// Called in `UpdateProgressOverlay` (which mounts once in the app shell and renders nothing until an
// update starts) it would run at app boot, with a null ref. So the sheet became its own component
// that mounts with the dialog.
//
// One thing deliberately NOT treated as a defect: after close, focus lands on `<body>`. For Modal
// that would be wrong (a button opened it, so focus returns there — verified). This overlay appears
// UNPROMPTED from a WS event, so there is no trigger to return to. `useFocusTrap` already guards
// this: it only restores a `prevActive` that is still connected and outside the dialog.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

describe('the update overlay honours the contract it declares', () => {
  const src = read('ui/UpdateProgressOverlay.tsx')

  it('the alertdialog carries the focus trap', () => {
    expect(src).toMatch(/import \{ useFocusTrap \} from '\.\/useFocusTrap'/)
    expect(src).toMatch(/ref=\{trapRef\} role="alertdialog" aria-modal="true"/)
  })

  it('the trap lives in a child that mounts WITH the dialog, not in the shell', () => {
    // The hook's effect is `[]` and its capture happens on first render, so calling it in the
    // always-mounted shell would run it at app boot against a null ref. This is the part a
    // "just add the ref" fix would get wrong while still looking correct.
    expect(src).toMatch(/function UpdateSheet\(\{ progress, cancel \}/)
    expect(src).toMatch(/const trapRef = useFocusTrap<HTMLDivElement>\(\)/)
    // The shell renders the sheet conditionally and does NOT call the hook itself.
    const shell = src.slice(src.indexOf('export function UpdateProgressOverlay()'), src.indexOf('function UpdateSheet'))
    expect(shell).toMatch(/\{progress && <UpdateSheet progress=\{progress\} cancel=\{cancel\} \/>\}/)
    expect(/useFocusTrap/.test(shell), 'the always-mounted shell must not call the hook').toBe(false)
  })
})

describe('the rail: aria-modal implies a focus trap', () => {
  const files = walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

  it('every aria-modal surface uses useFocusTrap', () => {
    const offenders = files
      .filter((f) => /aria-modal="true"/.test(f.src))
      .filter((f) => !/useFocusTrap/.test(f.src))
      .map((f) => f.rel)
    expect(
      offenders,
      `aria-modal="true" promises focus is owned; without a trap Tab reaches the page behind the ` +
        `scrim:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the aria-modal surfaces', () => {
    // Two cycles ago a rail matched NOTHING and reported a clean sweep, because
    // `expect(offenders).toEqual([])` cannot tell "nothing is broken" from "my matcher is broken".
    const modal = files.filter((f) => /aria-modal="true"/.test(f.src)).map((f) => f.rel).sort()
    expect(modal).toEqual([
      'ui/Modal.tsx',
      'ui/UpdateProgressOverlay.tsx',
      'ui/dialog/DialogShell.tsx',
    ])
    // And the check must still FLAG the shape: a surface declaring aria-modal with no trap.
    const sample = { rel: 'x.tsx', src: '<div role="dialog" aria-modal="true" />' }
    expect(/aria-modal="true"/.test(sample.src) && !/useFocusTrap/.test(sample.src)).toBe(true)
  })

  it('the two non-modal dialog roles are a recorded distinction', () => {
    // A popover and a drawer: neither CLAIMS to own focus, so neither owes a trap. Pinned so a
    // later pass does not "finish the sweep" by adding aria-modal to them — that would create the
    // very defect this rail guards, and NavRail already marks the page behind it `inert`.
    for (const rel of ['ui/DegradedChip.tsx', 'ui/NavRail.tsx']) {
      const src = read(rel)
      expect(src, `${rel} should still be a dialog role`).toMatch(/role="dialog"/)
      expect(/aria-modal/.test(src), `${rel} must NOT claim aria-modal`).toBe(false)
    }
    expect(read('ui/NavRail.tsx')).toMatch(/inert/)
  })
})
