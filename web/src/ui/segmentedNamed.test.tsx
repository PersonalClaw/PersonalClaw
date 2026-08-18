import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Segmented } from './Segmented'
import { Field } from './forms'

// ── Every tablist names the dimension it chooses ──────────────────────────────────
//
// A DOM census of 14 routes, counting `[role="tablist"]` nodes and resolving their accessible name from
// EITHER `aria-label` or `aria-labelledby`:
//
//   before   named  9 · UNNAMED 7      after   named 16 · UNNAMED 0
//
// The seven were: trigger kind · schedule kind ("When") · artifact kind · artifact sort · approval mode ·
// task status · task priority. A screen-reader user heard "Critical / High / Medium / Low" as four bare
// values with no statement of WHAT was being chosen — the same ambiguity #1132 fixed for the project
// picker, one level up.
//
// 🔑 THE ROOT CAUSE WAS A CONTRACT NOT CLAIMED, not eleven forgotten props. `forms.tsx` publishes the
// enclosing `Field`'s label id, and `TextInput` claims it (`aria-labelledby`) unless it passes its own
// `ariaLabel`. `Segmented` never did — so six of the seven sat inside a `Field` whose VISIBLE label
// already said it ("Status", "Priority", "When", "Runs", "Approval mode", "Trigger kind"). The primitive
// now claims it with the documented precedence (an explicit `ariaLabel` always WINS), which fixes those
// six and every future `Segmented` inside a `Field`. The remaining five sites are bare — no `Field` to
// claim — and take an explicit name in the surface's own wording.
//
// 🪤 The census initially reported 7 → 5 because it read `aria-label` only, and the Field-claiming path
// sets `aria-labelledby`. **A name probe must resolve BOTH attributes** or it under-reports exactly the
// fix it is measuring.

const SRC = join(process.cwd(), 'src')

/** Bare sites (no enclosing `Field`) with the name each one takes. */
const EXPLICIT: Array<[string, string]> = [
  [join('pages', 'artifacts', 'ArtifactsSection.tsx'), 'Artifact kind'],
  // The "Sort artifacts" Segmented was removed, not renamed: artifacts' sort is now a section inside
  // the canonical `ui/FilterMenu` pill, which names itself. The kind strip is still a bare Segmented.
  [join('pages', 'files', 'FilesSection.tsx'), 'File root'],
  [join('pages', 'inbox', 'InboxDetail.tsx'), 'Reclassify'],
  [join('pages', 'learning', 'LearningPage.tsx'), 'Proposal kind'],
]

describe('Segmented claims its Field label', () => {
  it('inside a Field, the tablist is named by the Field', () => {
    const { container } = render(
      <Field label="Priority">
        <Segmented value="a" onChange={() => {}} options={[{ key: 'a', label: 'High' }, { key: 'b', label: 'Low' }]} />
      </Field>,
    )
    const list = container.querySelector('[role="tablist"]')!
    const id = list.getAttribute('aria-labelledby')
    expect(id, 'the group must claim the published label id').toBeTruthy()
    // `getElementById`, not a `#id` selector: React's `useId()` emits ids containing colons (`:r0:`),
    // which are not valid CSS selectors — the first version of this test failed on its own selector.
    expect(document.getElementById(id!)!.textContent).toBe('Priority')
    expect(list.getAttribute('aria-label'), 'and must not also carry a redundant aria-label').toBeNull()
  })

  it('an explicit ariaLabel WINS over the Field label', () => {
    const { container } = render(
      <Field label="Priority">
        <Segmented ariaLabel="Sort artifacts" value="a" onChange={() => {}} options={[{ key: 'a', label: 'High' }]} />
      </Field>,
    )
    const list = container.querySelector('[role="tablist"]')!
    expect(list.getAttribute('aria-label')).toBe('Sort artifacts')
    expect(list.getAttribute('aria-labelledby')).toBeNull()
  })

  it('outside a Field with no ariaLabel, nothing is invented', () => {
    const { container } = render(
      <Segmented value="a" onChange={() => {}} options={[{ key: 'a', label: 'High' }]} />,
    )
    const list = container.querySelector('[role="tablist"]')!
    expect(list.getAttribute('aria-labelledby')).toBeNull()
    expect(list.getAttribute('aria-label')).toBeNull()
  })
})

describe('the bare tablists carry an explicit name', () => {
  it.each(EXPLICIT)('%s names one of its groups "%s"', (rel, name) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    expect(src, `${rel} must name that group`).toContain(`ariaLabel="${name}"`)
  })

  it('the primitive still reads the Field context (not vacuously green)', () => {
    const src = readFileSync(join(SRC, 'ui', 'Segmented.tsx'), 'utf8')
    expect(src).toMatch(/import \{ useFieldLabelId \} from '\.\/forms'/)
    expect(src).toMatch(/const claimsFieldLabel = !!fieldLabelId && !ariaLabel/)
  })
})
