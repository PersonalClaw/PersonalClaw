// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Grid3x3, List } from 'lucide-react'
import { CURRENT_OUTLINE, RowHitTarget, SELECTED_ROW_OUTLINE } from './RowHitTarget'
import { TileButton } from './TileButton'
import { Segmented } from './Segmented'
import { HeaderActions, HeaderSegmented } from './HeaderActions'

// ── A current, selected or on state shows under forced colours ─────────────────────────────────
//
// Forced colours (the matrix's high-contrast axis) drop fills and border colours, so a state told
// by fill or weight alone vanishes there: the row whose detail is open, the card previewed, the
// current tab and List or Grid's current option all looked like their neighbours. Each draws a 2 px
// `Highlight` outline there now, one class (`CURRENT_OUTLINE`, its row form `SELECTED_ROW_OUTLINE`
// on a row's wrapper), and only by opt-in: a `Segmented` or `HeaderSegmented` without `markCurrent`
// and a `TileButton` without `current` render as before.

const outlined = (el: Element) => CURRENT_OUTLINE.split(' ').every((c) => el.classList.contains(c))
const OPTIONS = [{ key: 'list', label: 'List', icon: List }, { key: 'grid', label: 'Grid', icon: Grid3x3 }]

describe('forced colours', () => {
  it('🔑 the selected row: its wrapper outlines while its hit target is current, by a Highlight outline', () => {
    expect(CURRENT_OUTLINE).toContain('forced-colors:outline-[Highlight]')
    expect(CURRENT_OUTLINE).toContain('forced-colors:outline-2')
    for (const c of SELECTED_ROW_OUTLINE.split(' ')) expect(c.startsWith('forced-colors:has-[>button[aria-current=true]]:')).toBe(true)
    render(<div data-testid="row" className={`relative ${SELECTED_ROW_OUTLINE}`}><RowHitTarget label="Lisbon trip plan" selected /></div>)
    expect(screen.getByRole('button', { name: 'Lisbon trip plan' }).getAttribute('aria-current')).toBe('true')
  })

  it('🔑 a card previewed: aria-current and no aria-pressed, with the outline', () => {
    render(<TileButton ariaLabel="Lisbon trip plan" current>card</TileButton>)
    const card = screen.getByRole('button', { name: 'Lisbon trip plan' })
    expect(card.getAttribute('aria-current')).toBe('true')
    expect(card.hasAttribute('aria-pressed')).toBe(false)
    expect(outlined(card)).toBe(true)
  })

  it('🔑 a TileButton without current renders as before', () => {
    render(<TileButton ariaLabel="Pick" active>card</TileButton>)
    const card = screen.getByRole('button', { name: 'Pick' })
    expect(card.getAttribute('aria-pressed')).toBe('true')
    expect(card.hasAttribute('aria-current')).toBe(false)
    expect([...card.classList].some((c) => c.startsWith('forced-colors:'))).toBe(false)
  })

  it('🔑 List or Grid: the current option outlines with markCurrent, and not without', () => {
    const { unmount } = render(<HeaderActions><HeaderSegmented ariaLabel="View" options={OPTIONS} value="grid" onChange={() => {}} markCurrent /></HeaderActions>)
    expect(outlined(screen.getByRole('radio', { name: /Grid/ }))).toBe(true)
    expect(outlined(screen.getByRole('radio', { name: /List/ })), 'only the current one').toBe(false)
    unmount()
    render(<Segmented ariaLabel="View" options={OPTIONS} value="grid" onChange={() => {}} />)
    for (const r of screen.getAllByRole('radio')) expect([...r.classList].some((c) => c.startsWith('forced-colors:'))).toBe(false)
  })
})
