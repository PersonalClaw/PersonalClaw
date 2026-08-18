import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The two ways a disabled control explains itself, and the rule that says when it must ─────────
//
// `ui/unavailable.ts` states the contract this file guards: a natively `disabled` button leaves the tab
// order, so a keyboard user "cannot reach it to hear anything — they tab straight past the action they are
// trying to take with no way to learn what is missing". Two carriers exist:
//
//   `Button`'s `disabledReason` prop  — for the component
//   `unavailableWhen(missing, reason)` — for a raw `<button>`, which cannot inherit a prop
//
// …and ONE deliberate exception, also from that file: when the action is IN FLIGHT the control goes
// natively disabled, because "an in-flight action must not be re-clickable, and its own spinner already
// carries that state".
//
// 🔑 WHAT THIS FILE DOES NOT CLAIM. It is not a total census of unexplained disabled controls, and saying
// so is the point — measured today: **337 `disabled=` props, 91 carrying an explicit reason, 191
// busy-native, 55 other**. Auditing those 55 needs an instrument this rail does not have, because three
// attempts to build one all over-reported:
//
//   • a PROXIMITY window (±600 chars) counted a reason that belonged to a different element;
//   • an OPENING-TAG scan missed reasons living in the element's CHILDREN — the knowledge outcome row
//     disables its link and says "(removed — insight kept)" in the button's own label;
//   • locating matches in COMMENT-STRIPPED source shifted every reported line number, so the sites I
//     went to read were not the sites the scan found.
//
// The 55 are also not one thing: most are pass-through primitives (`disabled={disabled}` on Slider,
// Segmented, Popover, a `<select>`) where the reason belongs to the CALLER, or whole-form sections
// (ProjectionRulesPanel, SecurityPanel) where one reason on the section beats one per input. So this rail
// pins the CARRIERS and the busy exception; a real audit of the remainder is future work with a better
// instrument.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })

describe('unavailableWhen — the raw-button carrier', () => {
  const src = read('ui/unavailable.ts')

  it('keeps the tab stop instead of going natively disabled', () => {
    // 🪤 ASSERT THE RETURNED OBJECT, NOT THE TYPE. The first version matched `'aria-disabled'?: true`
    // from the return TYPE, so swapping the implementation to `disabled: true` still passed — the
    // signature kept promising a soft-off control that the body no longer produced. Fourth time this
    // session a rail of mine checked a declaration instead of the supply.
    const returned = src.slice(src.lastIndexOf('return {'))
    expect(returned, 'the missing-input branch returns aria-disabled').toMatch(/'aria-disabled': true,/)
    expect(returned, 'and never the native attribute').not.toMatch(/\bdisabled: true/)
    expect(src, 'and it refuses the click itself').toMatch(/onClickCapture/)
    expect(src, 'the reason rides in the title').toMatch(/title: \[opts\?\.title, reason\]\.filter\(Boolean\)\.join\(' — '\)/)
  })

  it('goes NATIVELY disabled while busy — the documented exception', () => {
    // This is why a `disabled={busy}` elsewhere is correct rather than a gap.
    expect(src).toMatch(/if \(opts\?\.busy\) return \{ disabled: true/)
    // 🪤 The sentence wraps across two comment lines, so a single-line regex missed it on correct code —
    // the same "match the shape, not the meaning" trap as pinning a rail to markup. Allow the wrap.
    expect(src, 'and the reason for that is stated in the file').toMatch(
      /an in-flight action\s*\n?\s*\*?\s*must not be re-clickable/,
    )
  })

  it('returns nothing when nothing is missing', () => {
    expect(src).toMatch(/if \(!missing\) return opts\?\.title \? \{ title: opts\.title \} : \{\}/)
  })

  it('is actually used, and by raw buttons', () => {
    // A helper with no adopters is the shape this session keeps finding; 10 raw buttons were the reason
    // it was extracted, so the floor is deliberately near that.
    const users = walk(SRC).filter((f) => /\bunavailableWhen\(/.test(readFileSync(f, 'utf8')))
    expect(users.length, 'adopting files').toBeGreaterThanOrEqual(10)
    const anyRaw = users.some((f) => /<button[\s\S]{0,400}?unavailableWhen\(/.test(readFileSync(f, 'utf8')))
    expect(anyRaw, 'at least one is the raw-button case it exists for').toBe(true)
  })
})

describe('disabledReason — the Button carrier', () => {
  it('Button still accepts and renders it', () => {
    const btn = read('ui/Button.tsx')
    expect(btn, 'the prop exists').toMatch(/disabledReason\??:/)
    // It must produce the same soft-off shape, or the two carriers disagree.
    expect(btn, 'aria-disabled, not native disabled').toMatch(/aria-disabled/)
  })

  it('and the two carriers agree about what soft-off means', () => {
    // 🪤 The docstring's own warning: pair it with `aria-disabled:` styling, because `disabled:opacity-40`
    // never fires once nothing sets the native attribute. If a carrier stopped emitting `aria-disabled`,
    // every adopter would silently look enabled.
    for (const rel of ['ui/unavailable.ts', 'ui/Button.tsx']) {
      expect(read(rel), `${rel} emits aria-disabled`).toMatch(/aria-disabled/)
    }
  })
})
