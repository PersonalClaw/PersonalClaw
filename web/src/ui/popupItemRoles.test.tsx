import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MenuRow } from './Popover'

// ── A popup that promised a content model it did not contain ───────────────────────────────
//
// Measured on the live DOM. `#/tasks` at 390×844, where the view switcher collapses to a menu,
// and a right-click on a task row:
//
//                              BEFORE                          AFTER
//   collapsed Segmented        role=listbox, **0 options**      4 options, aria-selected on the
//                              trigger: no aria-haspopup        active one; trigger says listbox
//   task row context menu      role=menu, **0 menuitems**       3 menuitems
//
// A screen reader switches to a container's own navigation model when it sees `menu` or
// `listbox`, so it announced "View, list box" and then found nothing enumerable inside — while
// the four views sat there, visible, as plain buttons. The selected view was never conveyed at
// all (`aria-selected` count: 0).
//
// 🔑 THE SHARED ROW WAS THE REASON THOSE TWO WERE THE OUTLIERS. Of the six popup containers in
// the tree, four already marked their items correctly — `ProjectPicker` and `SlashMenu`
// (`option` + `aria-selected`), `HeaderActions` (`menuitemradio` + `aria-checked`), `MentionMenu`
// (`option`). The two that lied are exactly the two built from `MenuRow`, which rendered a plain
// `<button>`. Fixing the row fixes both; the OTHER 28 call sites sit in role-less popovers where
// a bare button is correct, and they are deliberately untouched.
//
// 🪤 A MEASUREMENT TRAP THIS CYCLE WALKED INTO FIRST. Reading `[role="tab"]` at 390px "found"
// four unreachable tabs at x=-34..472 — all of them inside `Segmented`'s `aria-hidden`,
// `invisible`, `-z-10` MEASUREMENT PROBE. The carried claim that `#/tasks` clipped its tabs at
// phone width was that artefact: `#root` is 390 and the document does not scroll horizontally.
// **Filter `closest('[aria-hidden="true"]')` before measuring anything about Segmented.**

describe('MenuRow carries the item role its container promises', () => {
  it('is a plain button by default — right for the 28 role-less popovers', () => {
    render(<MenuRow label="Rename" onClick={vi.fn()} />)
    const b = screen.getByRole('button', { name: 'Rename' })
    expect(b.getAttribute('role')).toBeNull()
    expect(b.getAttribute('aria-selected')).toBeNull()
    expect(b.getAttribute('aria-checked')).toBeNull()
  })

  it('is an option, with the selected state, inside a listbox', () => {
    render(<MenuRow role="option" label="Cards view" selected onClick={vi.fn()} />)
    expect(screen.getByRole('option', { name: 'Cards view' }).getAttribute('aria-selected')).toBe('true')
  })

  it('reports an unselected option as false, not as absent', () => {
    // `aria-selected` missing from an option means "not selectable"; false means "not chosen".
    render(<MenuRow role="option" label="Kanban board" onClick={vi.fn()} />)
    expect(screen.getByRole('option', { name: 'Kanban board' }).getAttribute('aria-selected')).toBe('false')
  })

  it('is a menuitem with NO state for an action row', () => {
    // The context menu passes `selected` to mark the keyboard-highlighted row. That is a highlight,
    // not a checkbox: an `aria-checked` on Peek / Open / Pin would claim a state they do not have.
    render(<MenuRow role="menuitem" label="Peek" selected onClick={vi.fn()} />)
    const b = screen.getByRole('menuitem', { name: 'Peek' })
    expect(b.getAttribute('aria-checked')).toBeNull()
    expect(b.getAttribute('aria-selected')).toBeNull()
  })

  it('uses aria-checked for a radio item', () => {
    render(<MenuRow role="menuitemradio" label="Agent" selected onClick={vi.fn()} />)
    expect(screen.getByRole('menuitemradio', { name: 'Agent' }).getAttribute('aria-checked')).toBe('true')
  })
})

describe('every popup container in the tree contains its item type', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  const ITEM_OF: Record<string, RegExp> = {
    menu: /role="(menuitem|menuitemradio|menuitemcheckbox)"|<MenuRow[\s\S]{0,400}?role="(menuitem|menuitemradio)"/,
    listbox: /role="option"|<MenuRow[\s\S]{0,400}?role="option"/,
  }

  const containers = walk(SRC).flatMap((f) => {
    const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    return [...src.matchAll(/role="(menu|listbox)"/g)].map((m) => ({
      file: f.slice(SRC.length + 1),
      role: m[1],
      // File-scoped on purpose: two of these delegate their rows to `MenuRow`, so the item role
      // lives at the call site a few lines below the container, not inside the same JSX literal.
      satisfied: ITEM_OF[m[1]].test(src),
    }))
  })

  it('finds them all (not vacuously green)', () => {
    // Six at the time of writing: Segmented, ContextMenu, HeaderActions, ProjectPicker,
    // MentionMenu, SlashMenu.
    expect(containers.length, 'the matcher must find the popup containers').toBeGreaterThanOrEqual(6)
    expect(containers.map((c) => c.file)).toContain('ui/Segmented.tsx')
    expect(containers.map((c) => c.file)).toContain('ui/motion/ContextMenu.tsx')
  })

  it('has no container that declares a role without the matching items', () => {
    const lying = containers.filter((c) => !c.satisfied).map((c) => `${c.file} (role=${c.role})`)
    expect(lying, 'a menu with no menuitems / a listbox with no options announces an empty container').toEqual([])
  })

  it("Segmented's trigger advertises the popup, like ProjectPicker's does", () => {
    const seg = readFileSync(join(SRC, 'ui/Segmented.tsx'), 'utf8')
    expect(seg).toMatch(/aria-haspopup="listbox"/)
    expect(seg).toMatch(/aria-expanded=\{open\}/)
  })
})
