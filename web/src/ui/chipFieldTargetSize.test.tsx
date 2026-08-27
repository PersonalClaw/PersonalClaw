import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { ChipInput, Field } from './forms'

// ── The chip field's target must match the well it lives in ────────────────────────────
//
// MEASURED live on `#/tasks/new` and `#/prompts/new` (834×1112, identical at 1440×900), reading
// `getBoundingClientRect()` off every visible `<input>`:
//
//     TextInput            40px
//     NumberField          40px
//     DateInput            40px
//     the textarea rows    36px
//     ChipInput's field  **19.5px**   ← min-height: auto, inside a 40px well
//
// WCAG 2.2 SC 2.5.8 wants 24px. The undersized-target *spacing* exception cannot rescue it once a
// chip exists either: chips are `h-7` (28px) sitting `gap-1.5` (6px) away, so the 24px circles
// intersect. `ux-audit --viewport tablet` reports it independently.
//
// Two things were wrong and they are one defect: the control's own box was under the floor, AND the
// 40px well that LOOKS like the field was not a way into it — 20 of its 40 pixels did nothing.
//
// 🪤 WHY THIS ASSERTS THE CLASS AND NOT THE PIXELS. jsdom has no layout: every
// `getBoundingClientRect()` here returns zeros, so a height assertion would pass against a 0px box
// and prove nothing. The live measurement is the evidence and lives in the PR; what this rail can
// hold is the MECHANISM — that the floor class is present, that it is the app's established 24px
// idiom, and that the well still routes a click into the field. A source-level assertion with a
// stated reason beats a layout assertion that cannot fail.

const SRC = join(process.cwd(), 'src')
const forms = readFileSync(join(SRC, 'ui/forms.tsx'), 'utf8')

/** The `<input>` inside `ChipInput` — sliced from the component body so a sibling input in the same
 *  file cannot satisfy the assertion by accident. */
function chipInputTag(): string {
  const start = forms.indexOf('export function ChipInput')
  expect(start, 'ChipInput was renamed or removed').toBeGreaterThan(0)
  const body = forms.slice(start, forms.indexOf('\nexport ', start + 10))
  const i = body.indexOf('<input ')
  expect(i, "ChipInput no longer renders an <input>").toBeGreaterThan(0)
  // The tag ends at the first `>` outside any {} — NOT the first `>`, and not the first that isn't
  // `=>`: this tag's props contain several inside arrow bodies and template strings.
  let depth = 0
  for (let k = i + 7; k < body.length; k++) {
    const c = body[k]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) return body.slice(i, k + 1)
  }
  throw new Error('could not find the end of the ChipInput <input> tag')
}

describe("the chip field clears SC 2.5.8's 24px floor", () => {
  const tag = chipInputTag()

  it('the field carries the 24px hit-box floor', () => {
    // `min-h-6` = 24px. Asserted on the ChipInput tag specifically, so raising some OTHER input in
    // this file cannot make this pass.
    expect(tag, "ChipInput's field lost its 24px minimum hit box").toMatch(/\bmin-h-6\b/)
  })

  it('it uses the app\'s established idiom, not a bespoke height', () => {
    // The fix is a MINIMUM, not a fixed height: the field must still stretch when the row does.
    expect(tag).toMatch(/\bflex-1\b/)
    // 🪤 `\bh-\d` MATCHES INSIDE `min-h-6` — `\b` fires after the hyphen, so the first draft of this
    // assertion failed against the very fix it is guarding. The lookbehind is what makes it mean
    // "a fixed height", not "any height utility".
    expect(tag, 'a fixed h-* would stop the field tracking its row').not.toMatch(/(?<!min-)\bh-\d/)
  })

  it('is pixel-neutral by construction — the well already reserves exactly 24px', () => {
    // `min-h-10` (40px) minus `py-2` (8px top + 8px bottom) = a 24px content box. That arithmetic is
    // WHY `min-h-6` moves nothing, so it is pinned: if the well's padding or height changes, the
    // neutrality claim in the PR stops being true and this fails.
    const start = forms.indexOf('export function ChipInput')
    const well = forms.slice(start, forms.indexOf('<input ', start))
    expect(well).toMatch(/\bmin-h-10\b/)
    expect(well).toMatch(/\bpy-2\b/)
  })

  it('the well routes a click into the field, and only its own background', () => {
    // The `focus-within:ring` already promises the whole well is the field. `e.target === e.currentTarget`
    // is what keeps a click on a chip's remove button from being hijacked.
    const start = forms.indexOf('export function ChipInput')
    const well = forms.slice(start, forms.indexOf('<input ', start))
    expect(well).toMatch(/onMouseDown=/)
    expect(well).toMatch(/e\.target === e\.currentTarget/)
    expect(well).toMatch(/focus\(\)/)
    // preventDefault stops the caret being placed and then stolen.
    expect(well).toMatch(/preventDefault\(\)/)
  })

  it('still renders, names itself, and keeps its chips (no behaviour lost)', () => {
    render(<ChipInput values={['alpha', 'beta']} onChange={() => {}} placeholder="Add a tag, Enter" />)
    expect(screen.getByRole('textbox', { name: 'Add a tag' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove alpha' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove beta' })).toBeTruthy()
  })

  it('inside a Field it still takes the Field\'s label, not the fallback', () => {
    render(<Field label="Tags"><ChipInput values={[]} onChange={() => {}} /></Field>)
    expect(screen.getByRole('textbox', { name: 'Tags' })).toBeTruthy()
  })
})

// ── The population this protects ───────────────────────────────────────────────────────
//
// A primitive fix is only worth its rail if the rail knows how many surfaces depend on it. Twelve
// call sites at the time of writing; a floor rather than an equality so adding one does not red this.
describe('the chip field is shared widely enough to be worth a primitive fix', () => {
  // 🪤 `withFileTypes`, NOT `statSync` per entry. The first draft used the `statSync(abs).isDirectory()`
  // idiom the other design rails use, and it took **20.5 SECONDS** and intermittently timed out while
  // four sibling test suites were running on the same machine — a rail that reds under load is worse
  // than no rail. One `readdir` syscall per directory instead of one `stat` per file.
  function walk(dir: string, out: string[] = []): string[] {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const abs = join(dir, e.name)
      if (e.isDirectory()) walk(abs, out)
      else if (/\.tsx?$/.test(e.name) && !e.name.includes('.test.')) out.push(abs)
    }
    return out
  }

  it('has at least 10 production call sites (vacuity floor)', () => {
    const sites: string[] = []
    for (const abs of walk(SRC)) {
      if (abs.endsWith('ui/forms.tsx')) continue
      const src = readFileSync(abs, 'utf8')
      for (const _ of src.matchAll(/<ChipInput[\s/>]/g)) sites.push(abs.slice(SRC.length + 1))
    }
    // If this ever matches nothing, the assertion above is guarding a component nobody renders.
    expect(sites.length, `ChipInput call sites found: ${sites.join(', ')}`).toBeGreaterThanOrEqual(10)
    // And they span more than one area, which is the argument for fixing the primitive rather than
    // the loudest call site.
    const areas = new Set(sites.map((s) => s.split('/').slice(0, 2).join('/')))
    expect(areas.size).toBeGreaterThanOrEqual(4)
  })
})
