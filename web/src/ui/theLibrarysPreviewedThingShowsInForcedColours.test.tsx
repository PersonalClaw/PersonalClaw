/** The library's previewed thing shows under forced colours (, the
 *  library's half: the primitives are `aSelectedRowShowsInForcedColours`): the previewed row's wrapper
 *  outlines while its hit target is current, and the previewed grid card is `aria-current`, never
 *  `aria-pressed`, with the outline. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { row } from '../test/libraryHarness'
import { CURRENT_OUTLINE, SELECTED_ROW_OUTLINE } from './RowHitTarget'
import { LibraryRow } from './LibraryRow'
import { LibraryCard } from './LibraryCard'
import { registerBuiltinContentTypes } from './content/registerBuiltins'

// A card draws its artifact through the content types the app registers at boot.
registerBuiltinContentTypes()

afterEach(cleanup)
const outlined = (el: Element, cls: string) => cls.split(' ').every((c) => el.classList.contains(c))

describe('the library\'s previewed thing in forced colours', () => {
  it('🔑 the previewed row: current, inside the outlined wrapper', () => {
    render(<LibraryRow item={row('knowledge:k1', 'Router manual')} selected onOpen={vi.fn()} />)
    const hit = screen.getAllByRole('button').find((b) => (b.getAttribute('aria-label') ?? '').startsWith('Router manual'))!
    expect(hit.getAttribute('aria-current')).toBe('true')
    let wrap: Element | null = hit.parentElement
    while (wrap && !outlined(wrap, SELECTED_ROW_OUTLINE)) wrap = wrap.parentElement
    expect(wrap, 'a wrapper draws the outline').not.toBeNull()
  })

  it('🔑 the previewed card: aria-current, no aria-pressed, and the outline', () => {
    render(<LibraryCard item={row('artifact:lisbon', 'Lisbon trip plan')} selected onOpen={vi.fn()} />)
    const card = screen.getAllByRole('button').find((b) => (b.getAttribute('aria-label') ?? '').startsWith('Lisbon trip plan'))!
    expect(card.getAttribute('aria-current')).toBe('true')
    expect(card.hasAttribute('aria-pressed')).toBe(false)
    expect(outlined(card, CURRENT_OUTLINE)).toBe(true)
  })

  it('🔴 control: a row and a card not previewed are not current', () => {
    render(<><LibraryRow item={row('knowledge:k1', 'Router manual')} onOpen={vi.fn()} /><LibraryCard item={row('artifact:lisbon', 'Lisbon trip plan')} onOpen={vi.fn()} /></>)
    for (const b of screen.getAllByRole('button')) expect(b.getAttribute('aria-current')).toBeNull()
  })
})
