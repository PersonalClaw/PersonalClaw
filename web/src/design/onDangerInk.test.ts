import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The two themes need OPPOSITE inks on a solid danger fill ─────────────────
//
// `--color-danger` is not the same kind of red in each theme, and that is deliberate: dark uses
// a LIGHT red so it reads on near-black, light uses a DEEP red so it reads on near-white. The
// consequence is that no single ink can sit on both:
//
//     dark   --color-danger #f66c66   white #ffffff → 2.89:1  ✗   deep ember #3f1008 → 5.63:1 ✓
//     light  --color-danger #af2f29   white #ffffff → 6.44:1  ✓
//
// `--color-on-danger` was defined ONCE, as `#ffffff`, in the dark block — the theme where white
// is exactly wrong — with no light override. Measured on a live seeded gateway, the two
// destructive header buttons in the app ("Dismiss all" on #/inbox, "Clear all" on
// #/notifications) rendered white-on-light-red at **2.89:1** in dark and axe flagged both;
// after, 5.63:1 and axe clean on both routes.
//
// `HeaderActions` compounded it by hardcoding `text-white` where `Button` used the token — so
// the one site that bypassed the token was the one site that failed. Both halves are asserted
// here, because either alone leaves the defect: the token fix does nothing for a component that
// never reads it, and the component fix does nothing while the token holds a single value.
//
// This is a TOKEN + CLASS contract, so it is checked in source. The ratios above are the live
// measurement; a jsdom render cannot resolve a CSS var chain across a theme class, and pinning
// the numbers here rather than recomputing them keeps this test about the contract.

const WEB = process.cwd()
const tokens = () => readFileSync(join(WEB, 'src/design/tokens.css'), 'utf8')

/** The value of `prop` inside a given block of the token file. */
function valueIn(block: string, prop: string): string | null {
  const m = block.match(new RegExp(`${prop}:\\s*([^;]+);`))
  return m ? m[1].trim() : null
}
/** Split tokens.css into the `@theme` (dark default) and `.light` blocks. */
function blocks(): { dark: string; light: string } {
  const src = tokens()
  const lightAt = src.search(/\.light\s*\{/)
  if (lightAt < 0) throw new Error('could not locate the .light block in tokens.css')
  return { dark: src.slice(0, lightAt), light: src.slice(lightAt) }
}

describe('--color-on-danger', () => {
  it('is defined in BOTH themes', () => {
    const { dark, light } = blocks()
    // A single definition means one ink for two opposite fills — the defect itself.
    expect(valueIn(dark, '--color-on-danger'), 'dark block must define it').toBeTruthy()
    expect(
      valueIn(light, '--color-on-danger'),
      'light must override it — its danger is a DEEP red where the dark theme\'s ink is unreadable',
    ).toBeTruthy()
  })

  it('is NOT white in dark (white on #f66c66 is 2.89:1)', () => {
    const ink = valueIn(blocks().dark, '--color-on-danger')!.toLowerCase()
    expect(ink).not.toBe('#ffffff')
    expect(ink).not.toBe('#fff')
    expect(ink).not.toBe('white')
  })

  it('IS white in light (white on #af2f29 is 6.44:1)', () => {
    // The counterpart direction. Flipping both themes to a dark ink would trade one failure for
    // another — light's deep red needs white, and this pins that half so a later "simplify the
    // token" edit cannot quietly collapse the two values back into one.
    expect(valueIn(blocks().light, '--color-on-danger')!.toLowerCase()).toBe('#ffffff')
  })

  it('still ships the danger fill as a per-theme hue (the reason two inks are needed)', () => {
    const { dark, light } = blocks()
    const d = valueIn(dark, '--color-danger')!.toLowerCase()
    const l = valueIn(light, '--color-danger')!.toLowerCase()
    // If these ever converge to one value the two-ink rule above stops being necessary — this
    // assertion is what would tell a future reader that the premise changed.
    expect(d).not.toBe(l)
  })
})

describe('components that fill with danger', () => {
  it('read the ink from the token instead of hardcoding white', () => {
    // `HeaderActions`' danger variant was `text-white`, which pinned one ink across both themes
    // and is what actually rendered the 2.89:1 buttons. Scanning both kits together because the
    // invariant is "every danger fill uses the token", not "this one file is correct".
    const offenders: string[] = []
    for (const rel of ['src/ui/HeaderActions.tsx', 'src/ui/Button.tsx']) {
      const src = readFileSync(join(WEB, rel), 'utf8')
      for (const m of src.matchAll(/danger:\s*'([^']*bg-danger[^']*)'/g)) {
        if (!/text-on-danger/.test(m[1])) offenders.push(`${rel} — ${m[1]}`)
      }
    }
    expect(
      offenders,
      'A danger-filled control must take its ink from --color-on-danger; a literal colour ' +
        'cannot be right in both themes.\n' + offenders.join('\n'),
    ).toEqual([])
  })
})
