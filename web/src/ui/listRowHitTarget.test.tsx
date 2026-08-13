import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ListRow } from './ListScaffold'

// ── The row's hit target is a SIBLING of its content, never an ancestor ──────────────
//
// `ListRow` used to put `role="button" tabIndex={0}` on the wrapper. A row that carries its
// own controls then becomes `nested-interactive` (axe, serious): AT is told "one button" and
// finds a checkbox and three tag filters inside it. Measured: 60 nodes — knowledge 26,
// workflows 34. After this change both are 0, with no new violations.
//
// 🔑 WHY AN EMPTY OVERLAY AND NOT `pointer-events` ON THE CHILDREN. The obvious fix keeps the
// wrapper interactive and re-exposes its descendants with
// `[&_button]:pointer-events-auto`-style selectors. That was built in cycle 46 and REVERTED,
// because it has to ENUMERATE every control type and silently misses the conditional ones:
//
//   · workflows' delete button only exists in its `armed` state (`armed === r.id ? … : …`)
//   · workflows' second delete is gated on `d.source !== 'bundled'`
//   · knowledge's tag filters are gated on `tags?.length` AND `hidden md:flex`
//
// A fix that passes on every row you can see and breaks the one you cannot. The overlay owns
// NO descendants, so there is nothing to enumerate and nothing to miss — verified live,
// including arming the workflows delete.
//
// The click still fires through the WRAPPER's onClick: every nested control already calls
// stopPropagation for itself (`ui/forms.tsx`'s Checkbox does it on both onClick and onChange;
// the tag/run/delete buttons do it inline), so bubbling was already the contract.

// ── Cycle 159: THE TASKS LIST HAND-ROLLS ITS ROWS, AND OPENING ONE WAS POINTER-ONLY ──────────────
//
// `TasksListPage` does not use `ListRow`; it builds its own `motion.div` with `onClick={onOpen}`. So the
// idiom above never reached it, and the surface's PRIMARY action had no keyboard equivalent — WCAG 2.1.1.
// Measured with the keyboard alone across 30 rows, at 1440×900:
//
//   the only tab stop inside a row     its 24px "Select: <title>" checkbox
//   Enter / Space there                toggles SELECTION, never opens
//   Shift+F10                          opens nothing (the ContextMenu is right-click only)
//   Tab onward + Enter                 reaches the PROJECT CHIP → navigates to the project, not the task
//
// 🔑 FOUR CLEAN axe PASSES MISSED IT, and that is the lesson: a `div` with an `onclick` and no `role` is
// invisible to every rule. `#/tasks` had just measured 0 blocking findings at both themes × both
// viewports, and its four switcher views were clean too. **"Can this be done with the keyboard?" is not
// a question a scanner asks** — it has to be driven.
//
// The fix is this file's own idiom, copied verbatim into the list row and the card: an EMPTY
// `absolute inset-0 -z-10` sibling button carrying `aria-label={t.title}`, `tabIndex={-1}` on the
// wrapper, no role on the wrapper, and the ring drawn on the row via `:has(> button:focus-visible)`.
//
// After, measured the same way:
//
//   hash                     `#/tasks` → `#/tasks?open=t-8cf48814`, focus on a control named "triage"
//   tab stops per LIST row   exactly 2 — the overlay, then the select checkbox (no Framer double stop)
//   tab stops per CARD       exactly 1
//   axe serious/critical     0 in both views × both themes (no `nested-interactive`)
//   focus ring               the focused row gains `oklab(…/0.5) 0 0 0 2px inset`; an unfocused
//                            sibling has NO visible layer
//   captures                 4/4 desktop and 2/2 phone identical — the overlay is invisible at rest
//
// 🪤 The ring check had to read the FULL computed shadow: Tailwind's ring is a multi-layer box-shadow
// whose first layer is `rgba(0,0,0,0)`, so a truncated read looks like no ring at all.

describe('ListRow hit target', () => {
  it('is a real <button>, not a role=button div', () => {
    const { container } = render(
      <ListRow onClick={() => {}} label="Deploy pipeline"><span>body</span></ListRow>,
    )
    const btn = screen.getByRole('button', { name: 'Deploy pipeline' })
    expect(btn.tagName).toBe('BUTTON')
    // And the wrapper must no longer claim the role.
    expect(container.firstElementChild?.getAttribute('role')).toBeNull()
  })

  it('does not CONTAIN the row content (the nested-interactive shape)', () => {
    render(
      <ListRow onClick={() => {}} label="Row name">
        <button type="button">nested action</button>
      </ListRow>,
    )
    const hit = screen.getByRole('button', { name: 'Row name' })
    const nested = screen.getByRole('button', { name: 'nested action' })
    expect(hit.contains(nested), 'the hit target must not be an ancestor of the row content').toBe(false)
    // Symmetrically, the row's controls must not be inside ANY interactive ancestor.
    for (let el = nested.parentElement; el; el = el.parentElement) {
      expect(
        el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button',
        `a nested control must have no interactive ancestor (found <${el.tagName}>)`,
      ).toBe(false)
    }
  })

  it('owns exactly ONE tab stop per row', () => {
    // `whileTap` makes Framer Motion set tabindex="0" on the wrapper itself, so simply
    // dropping the attribute left TWO tab stops per row (measured live: Tab landed on a bare
    // div, then on the overlay). The wrapper is pinned to -1 to keep the single stop.
    const { container } = render(<ListRow onClick={() => {}} label="Row"><span>body</span></ListRow>)
    const wrapper = container.firstElementChild!
    expect(wrapper.getAttribute('tabindex')).toBe('-1')
    expect(container.querySelectorAll('[tabindex="0"]').length).toBe(0)
    // The <button> is focusable natively without an explicit tabindex.
    expect(screen.getByRole('button', { name: 'Row' }).getAttribute('tabindex')).toBeNull()
  })

  it('fires onClick from the row body (bubbling), so the whole row stays clickable', () => {
    const onClick = vi.fn()
    render(<ListRow onClick={onClick} label="Row"><span>the body text</span></ListRow>)
    fireEvent.click(screen.getByText('the body text'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('lets a nested control stop the row from firing', () => {
    const onRow = vi.fn()
    const onNested = vi.fn()
    render(
      <ListRow onClick={onRow} label="Row">
        <button type="button" onClick={(e) => { e.stopPropagation(); onNested() }}>action</button>
      </ListRow>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'action' }))
    expect(onNested).toHaveBeenCalledTimes(1)
    expect(onRow, 'a control that stops propagation must not also trigger the row').not.toHaveBeenCalled()
  })

  it('adds no hit target to a NON-interactive row', () => {
    const { container } = render(<ListRow label="unused"><span>static</span></ListRow>)
    expect(container.querySelector('button')).toBeNull()
    expect(container.firstElementChild?.getAttribute('tabindex')).toBeNull()
  })
})

describe('the tasks list row and card carry the same overlay', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/tasks/TasksListPage.tsx'), 'utf8')

  it('both wrappers hand their tab stop to the shared hit-target primitive', () => {
    // 🪤 The first version of this change hand-rolled the overlay, and the primitive-adoption ratchet
    // went red at 273/272 — correctly: `ui/RowHitTarget` already owns this idiom. The ratchet is what
    // found the primitive.
    const overlays = [...src.matchAll(/<RowHitTarget label=\{t\.title\} \/>/g)]
    expect(overlays.length, 'one for the list row, one for the card').toBeGreaterThanOrEqual(2)
    expect(src, 'through the primitive, not a bespoke element').toMatch(/import \{ RowHitTarget \}/)
  })

  it('neither wrapper claims a role, and both keep tabIndex -1', () => {
    // A role="button" wrapper containing the select checkbox is nested-interactive; and without
    // `tabIndex={-1}` Framer's `whileTap` puts a second tab stop on the wrapper itself.
    //
    // 🪤 Strip comments first. The first version of this assertion failed on CORRECT source, because the
    // explanatory comment at the call site quotes the very markup the scan forbids — the recorded
    // "a ratchet counts markup in comments" trap, third occurrence in this session.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const wrappers = [...code.matchAll(/<motion\.div[\s\S]{0,900}?onClick=\{onOpen\}([\s\S]{0,1400}?)>/g)]
    expect(wrappers.length, 'the list row and the card').toBeGreaterThanOrEqual(2)
    for (const w of wrappers) {
      expect(w[1], 'no role on the wrapper').not.toMatch(/role="button"/)
      expect(w[1]).toMatch(/tabIndex=\{-1\}/)
    }
  })

  it('the ring is drawn on the row, keyed off the overlay', () => {
    const rings = [...src.matchAll(/has-\[>button:focus-visible\]:ring-2 has-\[>button:focus-visible\]:ring-inset has-\[>button:focus-visible\]:ring-primary\/50/g)]
    expect(rings.length, 'both wrappers').toBeGreaterThanOrEqual(2)
  })

  it('the select checkbox still names itself per row, so the two stops are distinguishable', () => {
    // Two tab stops per row only helps if they announce differently: "triage" opens, "Select: triage"
    // selects.
    expect(src).toMatch(/aria-label=\{`\$\{selected \? 'Deselect' : 'Select'\}: \$\{t\.title\}`\}/)
  })
})
