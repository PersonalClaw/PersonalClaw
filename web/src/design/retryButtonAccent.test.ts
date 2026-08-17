import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A ghost button whose label carries the accent, and the two ways its ink was wrong ─────────────
//
// SIX sites pushed the accent through `className` on a `ghost` Button. Four are the same retry job —
// "the load failed, try again" — and two are an open-file action:
//
//   pages/artifacts/ArtifactViewer      ghost size=xs  className="mt-1 text-primary"        Try again
//   pages/files/browse/FileViewer       ghost size=xs  className="mt-1 text-primary"        Try again
//   pages/code/CodeCockpitPage  ×2      ghost size=xs  className="text-primary"             Try again
//   pages/ChatPage.tsx                  ghost size=sm  className="…outline-variant/50 text-primary"
//   pages/ChatPage.tsx                  ghost size=xs  className="…text-[0.75rem] text-primary"
//
// 🪤 THE LAST TWO WERE FOUND BY THIS RAIL, NOT BY ME. My grep looked for `text-primary"` — a closing
// quote straight after the class — so it missed `text-[0.75rem] text-primary">` and
// `border-outline-variant/50 text-primary">`. The whole-tree sweep below has no such blind spot, which
// is the argument for writing the sweep before believing the census.
//
// It is wrong twice over:
//
// 🔑 1. TWO COLOUR UTILITIES ON ONE ELEMENT. `ghost` already sets `text-on-surface`, so which colour
//       wins is decided by Tailwind's stylesheet ORDER, not by the order they were written. It happens
//       to be the accent today and nothing guarantees it — the same trap `ui/TextLink`'s docstring
//       records, which is why that primitive took an `ink` prop instead of a className.
//
// 🔑 2. THE ACCENT IT PICKED FAILS AA. Driven on `#/artifacts/verdant-hollow-design-notes`, where the
//       retry sits on `--color-canvas` (read off the node: rgb(240,244,248)):
//
//         light   rgb(200,69,46) on rgb(240,244,248)   **4.37:1**  13px/450   ← axe agrees [serious]
//         dark    rgb(255,107,91) on rgb(15,15,15)       6.85:1               ← was already fine
//
//       4.37 is the canvas-coral number this codebase has now measured five times.
//
// The fix is a `ghost-accent` variant carrying `text-primary-emphasis` — ONE colour utility, and the
// shade the design system already ships for accent text off `--color-surface`. It clears the floor on
// every ground this button can land on: canvas 4.82 worst of 12 schemes (coral 6.0), surface-low 4.92,
// surface-high 4.70, surface 6.63. Surface-high matters because `hover:bg-surface-high` makes it the
// HOVER ground — a hover that would otherwise quietly move the measurement.
//
// 🪤 WHY THIS SHIPS WHILE `ui/Segmented`'s TONED OPTIONS STAY DEFERRED. The same sweep flagged those at
// **3.56:1** ("Schedule"/"Interval" on `#/triggers/new`, "Medium" on `#/tasks/new`). Segmented's DEFAULT
// selected state is a solid `--color-primary` fill with `on-primary` ink and passes; the failure is an
// option carrying its own `tone`, drawn as a 20% tint of that tone behind the tone itself — the
// registry-tone spelling, on an INTERACTIVE control. It needs a tinted BACKGROUND, and a container fill
// has no hover shade in the token set; inventing one is a redesign, which is exactly why cycle 146 held
// `ui/Button` and the cockpit back. Here the background stays transparent and only the ink moves, so
// there is nothing to invent. Recorded so the next pass does not read the two as one job.
//
// 🪤 NOT IN SCOPE, and named so it is not mistaken for a complete sweep of className colours: three
// other Buttons push a NON-accent colour through className (`text-danger` in StoreTriggerDetail,
// `text-warn` and `text-on-surface-low` in CodeCockpitPage). Semantic tones have no `<tone>-container`
// sibling and mostly clear AA on their own, which is the family's standing rule — they need their own
// measurement, not this variant.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

const RETRY_SITES = [
  'pages/artifacts/ArtifactViewer.tsx',
  'pages/files/browse/FileViewer.tsx',
  'pages/code/CodeCockpitPage.tsx',
]

describe('the retry button carries its accent through a variant, not a className', () => {
  it('the variant exists and uses the emphasis shade', () => {
    const btn = read('ui/Button.tsx')
    expect(btn).toMatch(/'ghost-accent': 'bg-transparent text-primary-emphasis hover:bg-surface-high'/)
    expect(btn, 'and it is a declared Variant').toMatch(/\| 'ghost-accent'/)
  })

  it('all six sites use it', () => {
    let n = 0
    for (const rel of [...RETRY_SITES, 'pages/ChatPage.tsx']) {
      const code = strip(read(rel))
      n += [...code.matchAll(/<Button variant="ghost-accent"/g)].length
    }
    expect(n, 'converged accent-ghost buttons').toBe(6)
  })

  it('no Button anywhere pushes the ACCENT through className any more', () => {
    // The whole-tree sweep: this is the assertion that stops the idiom coming back somewhere new.
    //
    // 🪤 `[^>]*` CANNOT BE USED TO SCAN A JSX TAG HERE, and a mutation caught it: `onClick={() =>
    // reload()}` contains a `>`, so the class stops inside the arrow function and the sweep matched
    // NOTHING on the very site it was written for. Neutralising `=>` first makes `[^>]*` mean "still
    // inside the tag" again. Verified by reverting a call site — this test fails.
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      const code = strip(readFileSync(abs, 'utf8')).replace(/=>/g, '\u21d2')
      for (const m of code.matchAll(/<Button[^>]*className="[^"]*\btext-primary\b[^"]*"/g))
        offenders.push(`${abs.replace(SRC, '')}: ${m[0].slice(0, 60)}`)
    }
    expect(offenders, 'Buttons inking the accent through className').toEqual([])
  })

  it('and that sweep is not vacuous — it sees a planted offender', () => {
    // The floor for the sweep itself. If the regex ever stops matching the shape it is meant to catch,
    // this fails instead of the sweep passing on an empty match set.
    const planted = '<Button variant="ghost" size="xs" onClick={() \u21d2 reload()} className="mt-1 text-primary">'
    expect([...planted.matchAll(/<Button[^>]*className="[^"]*\btext-primary\b[^"]*"/g)].length).toBe(1)
  })

  it('the ghost variant itself is untouched — the blast-radius floor', () => {
    // Every other ghost button keeps `text-on-surface`. If this ever changes, the reasoning above
    // ("only the four retries move") is stale and dozens of buttons shifted colour.
    expect(read('ui/Button.tsx')).toMatch(/ghost: 'bg-transparent text-on-surface hover:bg-surface-high'/)
  })

  it('the variant is documented, and the trap with it', () => {
    // `uiDocs.drift` checks that props are named; this checks the reviewer is told WHY, since the
    // failure mode (two colour utilities, stylesheet order) is invisible from the call site.
    const doc = read('ui/Button.doc.ts')
    expect(doc).toMatch(/ghost-accent/)
    expect(doc, 'the Do-not entry').toMatch(/Do not push a colour through className/)
  })

  it('the measurement rides with the variant, not just the token', () => {
    expect(read('ui/Button.tsx')).toMatch(/4\.37:1/)
  })

  it('Segmented is still deliberately deferred — not silently swept in', () => {
    // Same sweep flagged it at 3.56:1. If a future pass converges it, that is a real decision about a
    // hover shade, and this rail should be the thing that has to change.
    const seg = read('ui/Segmented.tsx')
    // Its DEFAULT selected state is a solid `--color-primary` fill with `on-primary` ink, which passes.
    // The failing case is an option carrying its own `tone`: a 20% tint of that tone behind the tone
    // itself — the registry-tone spelling again, this time on an interactive control with a hover.
    expect(seg, 'Segmented still tints an option tone').toMatch(/color-mix\(in srgb, \$\{o\.tone\} 20%, transparent\)/)
  })
})
