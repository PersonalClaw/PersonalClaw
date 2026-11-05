// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { Popover } from './Popover'

// ── A portaled flyout the reader is in survives a scroll ─────────────────────────────────────────
//
// A portaled flyout is `position: fixed` while its trigger scrolls with the page, so any scroll
// closed it. The narrow header search is one: at 390 px its field opens in a flyout, and a page
// that scrolled under it (a list reflowing to the query, a momentum scroll) closed the field
// mid-query, with focus left on nothing. A flyout holding focus now follows its trigger, a scroll
// inside it is its own, and only a scroll elsewhere with the reader elsewhere still closes it.

function Search() {
  return (
    <Popover portal placement="bottom" trigger={(open, toggle) => (
      <button type="button" onClick={toggle} aria-expanded={open}>Search</button>
    )}>
      {() => <div data-testid="flyout" style={{ overflow: 'auto' }}><input aria-label="Query" /></div>}
    </Popover>
  )
}

const scroll = (target: EventTarget) => act(() => { target.dispatchEvent(new Event('scroll', { bubbles: false })) })
/** Whether the flyout is open, by its trigger: a closing flyout stays in the DOM while it animates out. */
const isOpen = () => screen.getByRole('button', { name: 'Search' }).getAttribute('aria-expanded') === 'true'

describe('a portaled flyout and a scroll', () => {
  it('stays open while its field holds focus', () => {
    render(<Search />)
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    screen.getByRole('textbox', { name: 'Query' }).focus()
    scroll(window)
    expect(isOpen()).toBe(true)
  })

  it('stays open through a scroll inside it', () => {
    render(<Search />)
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    scroll(screen.getByTestId('flyout'))
    expect(isOpen()).toBe(true)
  })

  it('closes on a scroll elsewhere while the reader is elsewhere', () => {
    render(<Search />)
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    // Opening it puts focus in its field; the reader leaves it for the page.
    screen.getByRole('button', { name: 'Search' }).focus()
    scroll(window)
    expect(isOpen()).toBe(false)
  })
})
