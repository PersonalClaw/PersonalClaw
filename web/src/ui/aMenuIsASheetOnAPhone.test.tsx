// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from 'vitest'
import { act, fireEvent, render, screen, within, cleanup } from '@testing-library/react'
import { Plus, Upload, Rss } from 'lucide-react'
import { HeaderActions, HeaderControl, HeaderMenu } from './HeaderActions'
import { HeaderFilter } from './HeaderFilter'
import { ContextMenu } from './motion/ContextMenu'
import { Modal } from './Modal'
import { MenuRow } from './Popover'
import type { FilterSectionDef } from './FilterMenu'

// ── On a phone a menu is a sheet, by opt-in ─────────────────────────────────────────────────────
//
// From 320 to 768 px a header filter, a header menu, the `…`, a long press's menu and a dialog
// opened as an anchored flyout or a centred box, their rows 32 px. With `phoneSheet` (the cluster's
// `overflowPhoneSheet`) each opens as a sheet from the bottom: a dialog named by its trigger, with a
// scrim, every row a 44 px box, the focus inside it, and Escape or the scrim closing it with the
// focus back on the trigger. A filter's sheet has one Clear at its top, which clears every section,
// and its counting foot ("Show 3,344 items", closing it) in place of Done. At 1280 each is as before,
// and so is every menu that passes no `phoneSheet`, at any width.

function windowOf(width: number) {
  vi.stubGlobal('matchMedia', (query: string) => {
    const max = Number(/max-width:\s*(\d+)px/.exec(query)?.[1] ?? Infinity)
    return { matches: width <= max, media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }
  })
}
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const sections = (): FilterSectionDef[] => [
  { title: 'Kind', value: 'note', defaultKey: '', onChange: vi.fn(), multi: true, options: [{ key: 'note', label: 'Notes' }, { key: 'link', label: 'Links' }] },
  { title: 'Sort by', value: 'name', defaultKey: 'activity', onChange: vi.fn(), options: [{ key: 'activity', label: 'Recent activity' }, { key: 'name', label: 'Name' }] },
]
const ITEMS = [
  { label: 'Upload files', icon: Upload, group: 'add', onSelect: vi.fn() },
  { label: 'Follow a feed or folder', icon: Rss, group: 'setup', onSelect: vi.fn() },
]
const tall = (el: Element) => el.classList.contains('min-h-11')
const sheet = () => document.querySelector<HTMLElement>('[data-phone-sheet]')

describe('the phone sheet form', () => {
  for (const width of [320, 390, 412, 768]) {
    it(`🔑 at ${width} a filter opens as a named sheet: 44 px rows, Clear at its top, the counting foot`, () => {
      windowOf(width)
      const s = sections()
      render(<HeaderActions><HeaderFilter sections={s} prefix="Filter" phoneSheet panel foot="Show 3,344 items" /></HeaderActions>)
      const trigger = screen.getByRole('button', { name: /^Filter/ })
      fireEvent.click(trigger)
      const dialog = screen.getByRole('dialog', { name: trigger.getAttribute('aria-label')! })
      expect(dialog.getAttribute('aria-modal')).toBe('true')
      for (const row of within(dialog).getAllByRole('button', { pressed: true })) expect(tall(row), row.textContent ?? '').toBe(true)
      // One Clear, at the top, which clears every section; the sections' own links give way to it.
      const clears = within(dialog).getAllByRole('button', { name: 'Clear' })
      expect(clears).toHaveLength(1)
      fireEvent.click(clears[0])
      expect(s[0].onChange).toHaveBeenCalledWith('')
      expect(s[1].onChange).toHaveBeenCalledWith('activity')
      expect(within(dialog).queryByRole('button', { name: 'Done' })).toBeNull()
      fireEvent.click(within(dialog).getByRole('button', { name: 'Show 3,344 items' }))
      expect(screen.queryByRole('dialog')).toBeNull()
    })
  }

  it('🔑 Escape and the scrim close it, the focus back on the trigger', () => {
    windowOf(390)
    render(<HeaderActions><HeaderMenu icon={Plus} label="Add" items={ITEMS} phoneSheet /></HeaderActions>)
    const trigger = screen.getByRole('button', { name: 'Add' })
    fireEvent.click(trigger)
    expect(screen.getByRole('dialog', { name: 'Add' })).toBeTruthy()
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(trigger)
    fireEvent.click(trigger)
    fireEvent.click(document.querySelector('[data-phone-sheet-scrim]')!)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it("a header menu's sheet keeps its rows at 44 px and its break; the `…`'s too", () => {
    windowOf(390)
    render(
      <HeaderActions overflowPhoneSheet>
        <HeaderMenu icon={Plus} label="Add" items={ITEMS} phoneSheet />
        <HeaderControl label="Select items" inOverflow group="list" />
        <HeaderControl label="Read missed items" inOverflow group="upkeep" />
      </HeaderActions>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    const add = sheet()!
    expect(within(add).getAllByRole('menuitem').every(tall)).toBe(true)
    expect(within(add).getAllByRole('separator')).toHaveLength(1)
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const more = screen.getByRole('dialog', { name: 'More actions' })
    expect(tall(within(more).getByRole('button', { name: /Select items/ }))).toBe(true)
    expect(more.querySelectorAll('[data-menu-break]')).toHaveLength(1)
  })

  it('a long press opens its menu as a sheet; a dialog rises from the bottom', () => {
    windowOf(390)
    render(<ContextMenu phoneSheet name="Router manual" items={[{ label: 'Archive', onSelect: vi.fn() }]}><p>row</p></ContextMenu>)
    fireEvent.contextMenu(screen.getByText('row'))
    const dialog = screen.getByRole('dialog', { name: 'Router manual' })
    expect(tall(within(dialog).getByRole('menuitem', { name: 'Archive' }))).toBe(true)
    cleanup()
    render(<Modal title="Add to a project" onClose={() => {}} phoneSheet><MenuRow label="Homelab" /></Modal>)
    const modal = screen.getByRole('dialog', { name: 'Add to a project' })
    expect(modal.hasAttribute('data-phone-sheet')).toBe(true)
    expect(tall(screen.getByRole('button', { name: 'Homelab' }))).toBe(true)
  })

  it('🔑 at 1280 each is an anchored menu or a centred dialog, not a sheet', () => {
    windowOf(1280)
    render(<HeaderActions><HeaderFilter sections={sections()} prefix="Filter" phoneSheet panel foot="Show 3,344 items" /></HeaderActions>)
    fireEvent.click(screen.getByRole('button', { name: /^Filter/ }))
    expect(sheet()).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    cleanup()
    render(<Modal title="Add to a project" onClose={() => {}} phoneSheet><MenuRow label="Homelab" /></Modal>)
    expect(screen.getByRole('dialog').hasAttribute('data-phone-sheet')).toBe(false)
    expect(tall(screen.getByRole('button', { name: 'Homelab' }))).toBe(false)
  })

  it('🔑 a menu passed no phoneSheet is as today at a phone width: anchored, 32 px rows, Done', () => {
    windowOf(390)
    render(<HeaderActions><HeaderFilter sections={sections()} prefix="Filter" /></HeaderActions>)
    fireEvent.click(screen.getByRole('button', { name: /^Filter/ }))
    expect(sheet()).toBeNull()
    expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy()
    expect(screen.getAllByRole('button', { pressed: true }).some(tall)).toBe(false)
  })
})
