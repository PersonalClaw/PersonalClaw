import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ContextMenu } from './ContextMenu'

// ── An overlay a pointer can open and a keyboard cannot ───────────────────────────────────────────
//
// `escapeDismissContract` covers the other half of this contract — every overlay a user can OPEN must
// be closeable from the keyboard. This is the opening side, and `ContextMenu` had only pointer routes:
//
//     onContextMenu   right-click        ✓
//     onTouchStart    long-press 500ms   ✓
//     keyboard        —                  ✗
//
// Measured on the running app: focus a task row's hit target and press Shift+F10 — nothing happened, on
// all **14 surfaces** that use this primitive (tasks, inbox, triggers, projects, agents, knowledge,
// TagManager, prompts, FileTree, loops, skills, apps, chat, notifications).
//
// 🔑 IT IS NOT A CONVENIENCE GAP, BECAUSE SOME ROWS ARE THE ONLY ROUTE TO THEIR ACTION. `Complete` on a
// task row, and `Open full page` on a prompt or knowledge row, exist nowhere else on those surfaces — so
// a keyboard user could not reach them at all. WCAG 2.1.1.
//
// The fix is the platform convention: **Shift+F10 and the dedicated ContextMenu key**, anchored to the
// FOCUSED element's rect rather than the pointer's last position (a keyboard user has no pointer, and
// opening at 0,0 or at a stale mouse position is disorienting).
//
// Driven on the running app after the change, focus starting on a row's hit target:
//
//   #/tasks      Shift+F10 → menu opens with Open / Complete / Select; focus lands on "Open";
//                ArrowDown → "Complete"; Escape closes and returns focus to the row ("triage")
//   #/prompts    Shift+F10 → Open / Open full page — the second was previously unreachable entirely
//   ContextMenu key   opens it too
//   right-click       still opens it (no regression)
//   bare F10          does NOT open it
//   axe with the menu open   0 serious/critical
//   captures     6/6 identical across three surfaces × both themes — the change is invisible
//
// 🪤 `#/notifications` could not be verified this way: its rows expose visible row actions rather than a
// row-wide hit target, so there was nothing for the probe to focus first. Stated rather than implied.

const ITEMS = () => [
  { label: 'Open', onSelect: vi.fn() },
  { label: 'Complete', onSelect: vi.fn() },
  { label: 'Delete', onSelect: vi.fn(), danger: true },
]

/** A focusable invoker plus the menu — the shape a row has: focus lives on the row's hit target. */
function mount(items = ITEMS()) {
  const view = render(
    <div>
      <button type="button">row hit target</button>
      <ContextMenu items={items}><div>row body</div></ContextMenu>
    </div>,
  )
  const invoker = screen.getByRole('button', { name: 'row hit target' })
  invoker.focus()
  return { ...view, invoker, body: screen.getByText('row body') }
}

const rows = () => screen.getAllByRole('menuitem')

describe('the row context menu opens from the keyboard', () => {
  it('Shift+F10 opens it', () => {
    const { body } = mount()
    expect(screen.queryByRole('menu')).toBeNull()
    act(() => { fireEvent.keyDown(body, { key: 'F10', shiftKey: true }) })
    expect(screen.getByRole('menu')).toBeTruthy()
    expect(rows().map((r) => r.textContent?.trim())).toEqual(['Open', 'Complete', 'Delete'])
  })

  it('the dedicated ContextMenu key opens it', () => {
    const { body } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'ContextMenu' }) })
    expect(screen.getByRole('menu')).toBeTruthy()
  })

  it('a bare F10 does not', () => {
    // The modifier matters: F10 alone is a browser/OS key, and swallowing it would be its own defect.
    const { body } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'F10' }) })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('focus moves into the menu, onto the first row', () => {
    // The same contract the pointer path has (cycle 136's "the cursor IS focus"), through the new route.
    const { body } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'F10', shiftKey: true }) })
    expect(document.activeElement).toBe(rows()[0])
  })

  it('Escape hands focus back to where it started', () => {
    // 🪤 Asserted as FOCUS, not as absence-from-the-DOM: `AnimatePresence` keeps the exiting menu
    // mounted for its exit transition, which jsdom never advances, so `queryByRole('menu')` stays
    // truthy here while the live app really does close it (driven: `Esc → menu=false`). The sibling
    // rail in `lib/useMenuCursor.test.tsx` makes the same choice for the same reason.
    const { body, invoker } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'F10', shiftKey: true }) })
    expect(document.activeElement).not.toBe(invoker)
    act(() => { fireEvent.keyDown(document, { key: 'Escape' }) })
    expect(document.activeElement).toBe(invoker)
  })

  it('a disabled menu stays closed', () => {
    render(<ContextMenu items={ITEMS()} disabled><div>quiet row</div></ContextMenu>)
    act(() => { fireEvent.keyDown(screen.getByText('quiet row'), { key: 'F10', shiftKey: true }) })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('the right-click route is untouched', () => {
    const { body } = mount()
    act(() => { fireEvent.contextMenu(body) })
    expect(screen.getByRole('menu')).toBeTruthy()
  })
})

describe('the population this reaches', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  it('14 surfaces use the primitive, so the fix is not a per-page patch', () => {
    const files = walk(join(SRC, 'pages')).filter((abs) => /<ContextMenu[\s>]/.test(readFileSync(abs, 'utf8')))
    expect(files.length, `consumers:\n${files.map((f) => f.slice(SRC.length + 1)).join('\n')}`).toBeGreaterThanOrEqual(14)
  })

  it('the primitive still binds a keyboard opener', () => {
    // If a refactor drops `onKeyDown` from the bind object, every one of those surfaces silently loses
    // its keyboard route again — the state this cycle found.
    const code = readFileSync(join(SRC, 'ui/motion/ContextMenu.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).toMatch(/onKeyDown: \(e: React\.KeyboardEvent\)/)
    expect(code).toMatch(/e\.key === 'ContextMenu' \|\| \(e\.key === 'F10' && e\.shiftKey\)/)
  })
})
