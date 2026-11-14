// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { BookOpen, Link, Plus, Rss, Upload } from 'lucide-react'
import { HeaderActions, HeaderControl, HeaderMenu, HeaderOverflow } from './HeaderActions'

// ── A menu as wide as its hints need, with a break between its groups ─────────────────────────────
//
// A `HeaderMenu` opened 260 px wide and the `…` 220, each a run of unbroken rows, and a `MenuRow`
// cuts its hint to one line, so a menu whose hints say more (the library's Add: "The agent checks
// what you add and keeps what it finds.") lost their ends, and its set-up rows ran on from its add
// rows with nothing between them. Each takes a width now (`HeaderMenu`'s `width`, the cluster's
// `overflowWidth`), and a row a `group` (on the `…`'s rows from `HeaderControl`'s own `group`): a
// break is drawn between two rows whose group differs, never at the top or the foot. In
// `HeaderMenu`'s `role="menu"` the break is its `separator`; in the role-less `…` it is drawn only.
// Without them each menu is as before.

const ADD = [
  { label: 'Upload files', icon: Upload, hint: 'They stay on this computer.', group: 'add', onSelect: () => {} },
  { label: 'Add a link', icon: Link, group: 'add', onSelect: () => {} },
  { label: 'Follow a feed or folder', icon: Rss, hint: 'New items come in on their own.', group: 'setup', onSelect: () => {} },
  { label: 'Look out for a topic', icon: BookOpen, hint: 'The agent checks what you add and keeps what it finds.', group: 'setup', onSelect: () => {} },
]

/** The open menu's box, which carries its width. */
const box = (menu: HTMLElement) => {
  let el: HTMLElement | null = menu
  while (el && !el.style.width) el = el.parentElement
  return el
}
/** The menu's rows and breaks in order: a row by its label, a break as `|`. */
const shape = (menu: HTMLElement) => [...menu.querySelectorAll<HTMLElement>('[data-menu-break], button')]
  .map((el) => (el.hasAttribute('data-menu-break') ? '|' : (el.querySelector('[data-type="label-s"]')?.textContent ?? '')))

describe("a header menu's width and breaks", () => {
  it('🔑 opens at its width, every hint whole, with a separator where the group changes', () => {
    render(<HeaderActions><HeaderMenu icon={Plus} label="Add" variant="primary" items={ADD} width={384} /></HeaderActions>)
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    const menu = screen.getByRole('menu', { name: 'Add' })
    expect(box(menu)?.style.width).toBe('384px')
    expect(shape(menu)).toEqual(['Upload files', 'Add a link', '|', 'Follow a feed or folder', 'Look out for a topic'])
    expect(within(menu).getAllByRole('separator')).toHaveLength(1)
    // Each hint is there in full, its own words.
    expect(within(menu).getByText('The agent checks what you add and keeps what it finds.')).toBeTruthy()
    // The cursor steps over the break: the rows are its only stops.
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(4)
  })

  it('never opens or ends on a break', () => {
    const items = [{ ...ADD[0], group: 'a' }, { ...ADD[1], group: 'a' }]
    render(<HeaderActions><HeaderMenu icon={Plus} label="Add" items={items} width={384} /></HeaderActions>)
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(shape(screen.getByRole('menu'))).toEqual(['Upload files', 'Add a link'])
  })

  it('🔑 a HeaderMenu passed neither is as today: 260 px, no break', () => {
    const items = ADD.map(({ group: _g, ...rest }) => rest)
    render(<HeaderActions><HeaderMenu icon={Plus} label="Add" items={items} /></HeaderActions>)
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    const menu = screen.getByRole('menu', { name: 'Add' })
    expect(box(menu)?.style.width).toBe('260px')
    expect(shape(menu)).not.toContain('|')
    expect(within(menu).queryAllByRole('separator')).toEqual([])
  })

  it('🔑 the `…` opens at overflowWidth, with a break between its groups, drawn and not announced', () => {
    render(
      <HeaderActions overflowWidth={360}>
        <HeaderControl label="Select items" inOverflow group="list" />
        <HeaderControl label="Feeds, intents and reports" hint="See and change what adds on its own." inOverflow group="list" />
        <HeaderControl label="Read missed items" hint="For items added while no model could read them." inOverflow group="upkeep" />
        <HeaderControl label="Process everything again…" hint="Uses your model for a while. You can keep working." inOverflow group="upkeep" />
      </HeaderActions>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const first = screen.getByRole('button', { name: /Select items/ })
    const menu = first.closest('.flex.flex-col') as HTMLElement
    expect(box(menu)?.style.width).toBe('360px')
    expect(shape(menu)).toEqual(['Select items', 'Feeds, intents and reports', '|', 'Read missed items', 'Process everything again…'])
    expect(menu.querySelector('[data-menu-break]')?.getAttribute('aria-hidden')).toBe('true')
    expect(screen.getByText('Uses your model for a while. You can keep working.')).toBeTruthy()
  })

  it('🔑 a cluster passed neither is as today: 220 px, no break', () => {
    render(<HeaderOverflow actions={[{ id: 1, label: 'Export' }, { id: 2, label: 'Archive' }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const menu = screen.getByRole('button', { name: 'Export' }).closest('.flex.flex-col') as HTMLElement
    expect(box(menu)?.style.width).toBe('220px')
    expect(menu.querySelector('[data-menu-break]')).toBeNull()
  })
})
