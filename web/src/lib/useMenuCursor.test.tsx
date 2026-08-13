import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ContextMenu } from '../ui/motion/ContextMenu'

// ── A context menu's highlighted row must BE the focused row ───────────────────────────────────
//
// Both context menus tracked a cursor in React state and only painted it. Driven on the stack
// head with the shared menu open on `#/inbox` (row menu on a proposal), ArrowDown ×2:
//
//   the paint moved            row 0 `wght 500` + coral dot  →  row 1
//   document.activeElement     the row BEHIND the menu       →  UNCHANGED (focusedRow: -1)
//   axe (wcag2a/aa, 21a/aa)    0 violations                  →  0 violations
//
// 🔑 SO THE CURSOR EXISTED ONLY FOR PEOPLE WHO COULD SEE IT. A screen reader was never told the
// menu opened, never told which row the cursor sat on, and Enter then activated a row the user was
// never told about (WCAG 4.1.2 / 2.4.3). **axe cannot ask "was the user told?"** — role, name and
// menuitem children were all correct, which is exactly why 0 violations is not evidence here.
//
// 🪤 AND THE ROWS WERE STRAY TAB STOPS. `MenuRow` renders a <button>, so every row was tabbable, in
// a portal at the END of the document: Tab from the open menu landed on a page control ("Proposals")
// with the menu still open, and the rows themselves were only reachable by tabbing the whole page.
// Roving tabindex collapses that to the one cursor row.
//
// FileTree's menu (a deliberate second implementation — different OPEN contract, see
// design/primitiveShadowing) was worse, and it is the one a keyboard user can actually open:
// measured from its "⋯" button with Enter, focus stayed on the button, arrows did nothing, Tab moved
// into the tree BEHIND the open menu, and Escape dropped focus on <body>. Rename / Upload here /
// **Delete** were unreachable. Both now share `lib/useMenuCursor`.
//
// The canonical form was already in the kit: `ui/Popover` "restores focus to the trigger on
// Escape/selection, so keyboard focus isn't dropped to <body>". This converges the two menus onto
// that contract instead of inventing one — including its split, where an outside CLICK does not
// restore focus because the pointer is already elsewhere.

const ITEMS = (onSelect = () => {}) => [
  { label: 'Open', onSelect },
  { label: 'Go to chat', onSelect: () => {} },
  { label: 'Delete', onSelect: () => {}, danger: true },
]

/** Render a focusable invoker + the menu, and open it the way a right-click does. */
function openMenu(items = ITEMS()) {
  const view = render(
    <div>
      <button type="button">invoker</button>
      <ContextMenu items={items}><div>row body</div></ContextMenu>
    </div>,
  )
  const invoker = screen.getByRole('button', { name: 'invoker' })
  // The browser focuses the nearest focusable ancestor of the right-clicked element (measured:
  // activeElement was the inbox row itself), so the test must start focus somewhere real.
  invoker.focus()
  act(() => { fireEvent.contextMenu(screen.getByText('row body')) })
  return { ...view, invoker }
}

const rows = () => screen.getAllByRole('menuitem')

describe('the cursor is focus', () => {
  it('opening the menu moves focus into it, onto the first row', () => {
    openMenu()
    expect(document.activeElement).toBe(rows()[0])
  })

  it('ArrowDown/ArrowUp move real focus, not just a font weight', () => {
    openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(rows()[1])
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(rows()[2])
    act(() => { fireEvent.keyDown(document, { key: 'ArrowUp' }) })
    expect(document.activeElement).toBe(rows()[1])
  })

  it('clamps at both ends instead of wrapping — the pre-existing behaviour', () => {
    openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'ArrowUp' }) })
    expect(document.activeElement).toBe(rows()[0])
    for (let i = 0; i < 5; i++) act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(rows()[2])
  })

  it('the menu is ONE tab stop: the cursor row roves, the rest sit at -1', () => {
    openMenu()
    expect(rows().map((r) => r.getAttribute('tabindex'))).toEqual(['0', '-1', '-1'])
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(rows().map((r) => r.getAttribute('tabindex'))).toEqual(['-1', '0', '-1'])
  })

  it('the paint follows focus rather than leading it', () => {
    // `selected` still bolds the label + shows the dot; the point is that it can no longer
    // disagree with focus, because both read the same cursor.
    openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    const painted = rows().findIndex((r) => r.querySelector('.bg-primary'))
    expect(painted).toBe(rows().indexOf(document.activeElement as HTMLElement))
  })
})

describe('Enter activates exactly once', () => {
  it('the document handler no longer duplicates the native button activation', () => {
    // 🪤 THE POINT OF THIS TEST. jsdom does not synthesize a click from Enter on a button, so it
    // must model what the browser does: the keydown AND the activation click. With the old
    // `else if (e.key === 'Enter')` branch still present, this counts 2.
    const onSelect = vi.fn()
    openMenu(ITEMS(onSelect))
    const cursor = document.activeElement as HTMLElement
    act(() => { fireEvent.keyDown(document, { key: 'Enter' }) })
    act(() => { fireEvent.click(cursor) })
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('the Enter branch is gone from the source, not just inert', () => {
    const src = readFileSync(join(process.cwd(), 'src/ui/motion/ContextMenu.tsx'), 'utf8')
    expect(src, 'a re-added Enter branch fires onSelect twice per press').not.toMatch(/key === 'Enter'/)
  })
})

describe('closing hands focus back', () => {
  it('Escape returns focus to the invoker instead of dropping it on <body>', () => {
    const { invoker } = openMenu()
    expect(document.activeElement).not.toBe(invoker)
    act(() => { fireEvent.keyDown(document, { key: 'Escape' }) })
    expect(document.activeElement).toBe(invoker)
  })

  it('Tab dismisses and returns focus, so the browser tabs on from the invoker', () => {
    // Not preventDefault-ed: the restore happens during the keydown, then the default Tab moves on
    // from the invoker. jsdom does not implement that second half — this asserts the restore, and
    // the live drive covers where focus ends up.
    const { invoker } = openMenu()
    act(() => { fireEvent.keyDown(document, { key: 'Tab' }) })
    expect(document.activeElement).toBe(invoker)
  })

  it('choosing a row returns focus too', () => {
    const { invoker } = openMenu()
    act(() => { fireEvent.click(rows()[0]) })
    expect(document.activeElement).toBe(invoker)
  })

  it('an outside CLICK does not yank focus back — the pointer is already elsewhere', () => {
    // ui/Popover's split, kept: restoring focus here would fight the user's own click. Read
    // against the Escape test above — same close, one restores focus and one deliberately doesn't.
    //
    // 🪤 DO NOT assert the menu is GONE here. Under jsdom an AnimatePresence exit never completes
    // (no frames), so the rows stay in the DOM after `pos` goes null — measured: 2 rows before and
    // after. "Is it closed?" is only answerable in a browser, and the live drive covers it.
    const { invoker } = openMenu()
    const cursor = document.activeElement
    act(() => { fireEvent.mouseDown(document.body) })
    expect(document.activeElement, 'focus stays where the user left it').toBe(cursor)
    expect(document.activeElement).not.toBe(invoker)
  })
})

describe('a disabled row stays reachable and says why it cannot act', () => {
  it('announces aria-disabled rather than dropping out of the cursor path', () => {
    // The one live consumer: Apps → Install while an install is in flight. Before this it rendered
    // identically to an enabled row and silently did nothing when clicked.
    const onSelect = vi.fn()
    render(
      <ContextMenu items={[{ label: 'Details', onSelect: () => {} }, { label: 'Install', onSelect, disabled: true }]}>
        <div>card</div>
      </ContextMenu>,
    )
    act(() => { fireEvent.contextMenu(screen.getByText('card')) })
    const install = screen.getByRole('menuitem', { name: 'Install' })
    expect(install).toHaveAttribute('aria-disabled', 'true')
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(document.activeElement, 'APG: a disabled item is reachable, not skipped').toBe(install)
    act(() => { fireEvent.click(install) })
    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe("FileTree's second implementation shares the keyboard contract", () => {
  const whole = readFileSync(join(process.cwd(), 'src/pages/files/browse/FileTree.tsx'), 'utf8')
  // Scoped to the menu component — it is the LAST declaration in the file. The tree's inline
  // rename/create inputs legitimately handle Enter, so a whole-file "no Enter branch" assertion is
  // a false red (it was, on the first run of this rail).
  const at = whole.indexOf('function ContextMenu({ x, y')
  const src = whole.slice(at)

  it('the slice really is the menu and nothing else', () => {
    expect(at, 'the menu component moved — re-anchor this slice').toBeGreaterThan(0)
    expect(src).toContain('createPortal')
    expect(src, 'the slice must not swallow the tree rows that own Enter').not.toContain('commitRename')
  })

  it('uses the hook rather than a second cursor', () => {
    expect(src).toMatch(/useMenuCursor\(\{\s*containerRef: ref, count: items\.length/)
  })

  it('declares the roles its new focus behaviour promises', () => {
    // role=menu with plain <button> children would be an axe aria-required-children violation —
    // the roles and the focus move ship together or not at all.
    expect(src).toMatch(/role="menu" aria-orientation="vertical"/)
    expect(src).toMatch(/role="menuitem" tabIndex=\{tabIndexFor\(i\)\}/)
  })

  it('arrow keys and a focus-returning Escape are wired', () => {
    // Cycle 137 moved the arrow/Tab branches into `menuCursorKeydown` so five popups share one
    // spelling; the Escape branch stays local because each popup closes differently.
    expect(src).toMatch(/menuCursorKeydown\(e, \{ move, dismiss: closeAndReturnFocus \}\)/)
    expect(src).toMatch(/if \(e\.key === 'Escape'\) \{ e\.stopPropagation\(\); closeAndReturnFocus\(\); return \}/)
  })

  it('consumes Escape so it does not also collapse the Explorer', () => {
    // The Explorer is a ui/SidePanel and SidePanel binds Escape on WINDOW. Measured without the
    // stopPropagation: one press closed the menu AND the panel — 13 tree rows to 0 — which also
    // destroyed the "⋯" button this menu hands focus back to. Popover's single-layer rule.
    expect(src).toMatch(/e\.stopPropagation\(\); closeAndReturnFocus/)
  })

  it('has no Enter branch of its own either', () => {
    expect(src).not.toMatch(/key === 'Enter'/)
  })
})

describe('every ContextMenu consumer inherits this — the census', () => {
  it('is worth doing in the primitive: 13 files, 16 call sites', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const PAGES = join(process.cwd(), 'src/pages')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const sites = walk(PAGES).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/<ContextMenu[\s>]/g)].map(() => f))
    expect(new Set(sites).size, 'files rendering a context menu').toBeGreaterThanOrEqual(13)
    expect(sites.length, 'call sites — one primitive, sixteen row menus').toBeGreaterThanOrEqual(16)
  })
})
