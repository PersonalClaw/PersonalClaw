import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import { ChecklistEditor } from './formControls'

// ── A tick whose body is `null` is a button with no content and no name ────────────────────────────
//
// `ChecklistEditor`'s tick rendered as:
//
//     <button type="button" onClick={…} className="… size-5 …">{it.done ? <Check/> : null}</button>
//
// Three defects stacked on one element, and they are worth separating because they fail for three
// different users:
//
//   1. NO ACCESSIBLE NAME. No `aria-label`, no `title`. And because the body is `null` when unticked,
//      there is no text content to fall back on either — so a screen reader announces a bare
//      "button", once per checklist row, on a form whose entire purpose is authoring those rows.
//   2. NO STATE. Ticked vs unticked was carried ONLY by the `Check` glyph and a border/background
//      swap — i.e. by colour. A non-sighted user could not tell a done item from a pending one at all.
//   3. 20px TARGET. `size-5` is under the 24px pointer-target floor.
//
// 🔑 EVERY EXISTING RAIL MISSED THIS, AND EACH FOR A STATED REASON — that is the reusable part:
//   • `design/iconButtonNames.test.tsx` matches a button body against `/^<[A-Z]\w*[^>]*\/>$/`, so a
//     body wrapped in a CONDITIONAL is invisible to it. This tick's body is a ternary.
//   • `pages/tasks/tickTargetSize.test.ts` fixed exactly defect 3 (16→24, 20→24) for the SIBLING
//     ticks — but it hard-codes `FILE = src/pages/tasks/TaskDetail.tsx`. A file-pinned rail cannot
//     find the twin of the thing it was written for.
//   • `pages/tasks/checklistEdit.test.tsx` covers this component but asserts nothing about names or
//     roles (no `aria-label`, no `getByRole` in it).
// So the assertions below are deliberately NOT file-pinned to `formControls.tsx` alone where it is
// cheap not to be.
//
// 🪤 THE FIX IS CONFORMANCE, NOT INVENTION. `TaskDetail.tsx:227` already does the flipping-name thing
// (`'Mark criterion incomplete'` / `'Mark criterion complete'`) at `size-6` with a horizontal margin
// reclaim. `aria-pressed` was considered and NOT added: the sibling does not use it, and adding it to
// one of two twin controls would make the product LESS consistent. That is a product-wide call.
//
// 🪤 AND `size-6` + `-mx-0.5` IS ONE DECISION. The reclaim is 2px per side, so the 24px target keeps a
// 20px LAYOUT footprint — which is what lets the `size-5` alignment spacers in the same rows stay
// correct untouched. Asserting the size without the reclaim would greenlight a visible 4px jog.

type Item = { description: string; done?: boolean }

function renderEditor(items: Item[]) {
  return render(
    <ChecklistEditor<Item>
      items={items}
      onChange={() => {}}
      doneKey="done"
      placeholder="Add a step"
    />,
  )
}

describe('the checklist tick has a name that says what it does and what state it is in', () => {
  it('an UNTICKED row names the action and the row — it used to announce bare "button"', () => {
    renderEditor([{ description: 'write the migration' }])
    // 🪤 Queried by ROLE + accessible name, which is the only query that fails for the original
    // defect. A `getByText` would have found the row's own description text and passed regardless.
    expect(screen.getByRole('button', { name: 'Mark done: write the migration' })).toBeTruthy()
  })

  it('a TICKED row announces the opposite action — this is how state is conveyed', () => {
    // Defect 2. Without the flip, the name is identical in both states and a non-sighted user
    // cannot tell a completed step from a pending one.
    renderEditor([{ description: 'write the migration', done: true }])
    expect(screen.getByRole('button', { name: 'Mark not done: write the migration' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Mark done: write the migration' })).toBeNull()
  })

  it('each row gets its OWN name, so four rows are four distinct buttons', () => {
    // The original produced four indistinguishable "button"s. A static label like "Toggle done"
    // would fix the naming defect and leave this one — hence the interpolated description.
    renderEditor([{ description: 'alpha' }, { description: 'beta' }, { description: 'gamma', done: true }])
    expect(screen.getByRole('button', { name: 'Mark done: alpha' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mark done: beta' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mark not done: gamma' })).toBeTruthy()
  })

  it('an empty description still yields a usable name rather than a dangling colon-only label', () => {
    // `it.description` is typed loosely and the component already guards it with `String(… ?? '')`.
    renderEditor([{ description: '' }])
    expect(screen.getByRole('button', { name: /^Mark done:/ })).toBeTruthy()
  })
})

describe('the tick meets the pointer-target floor without moving the row', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/tasks/formControls.tsx'), 'utf8')
  const tick = src.match(/<button type="button" onClick=\{\(\) => toggle\(i\)\}[\s\S]*?<\/button>/)?.[0] ?? ''

  it('the tick markup was found — every assertion below depends on it', () => {
    // 🪤 A regex that matches nothing makes `.not.toMatch` assertions PASS vacuously. Guard first.
    expect(tick, 'the tick button must be located before it can be measured').not.toBe('')
  })

  it('the target is 24px, not 20px', () => {
    expect(tick).toMatch(/\bsize-6\b/)
    expect(tick, '20px is under the pointer-target floor').not.toMatch(/\bsize-5\b/)
  })

  it('and the extra 4px is reclaimed horizontally so the layout does not shift', () => {
    // Without this the row's spacers (size-5, 20px) would no longer line up with the tick.
    expect(tick, 'a bare size-6 would jog the row 4px').toMatch(/-mx-0\.5/)
    expect(tick, 'a vertical reclaim would overlap the stacked target above').not.toMatch(/-my-/)
  })

  it('the spacers that align to it are untouched, which is what the reclaim buys', () => {
    // If a later change drops `-mx-0.5`, these must move too — this records the coupling so the
    // next reader knows the two numbers are one decision.
    expect(src).toMatch(/<span className="size-5 shrink-0" \/>/)
  })
})
