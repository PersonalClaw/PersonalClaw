// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { Popover } from './Popover'

// ── A portaled flyout the reader is in survives a scroll ─────────────────────────────────────────
//
// A portaled flyout is `position: fixed` while its trigger scrolls with the page, so any scroll
// closed it. The narrow header search is one: at 390 px its field opens in a flyout, and a page
// that scrolled under it (a list reflowing to the query, a momentum scroll) closed the field
// mid-query, with focus left on nothing. Asked to (`followWhileTyping`, which the header search
// passes), a flyout whose text field holds focus follows its trigger, and a scroll with the reader
// elsewhere still closes it. Every other portaled flyout closes on a scroll outside it, as it did:
// opening one puts focus in it, so following focus alone kept them all open. A scroll inside any
// flyout is its own and keeps it open: a header filter's long sections scroll in a capped box, and
// closing on their own scroll left their last rows out of reach.

function Search({ follows = true }: { follows?: boolean }) {
  return (
    <Popover portal followWhileTyping={follows} placement="bottom" trigger={(open, toggle) => (
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

  it('a flyout not asked to follow keeps open through a scroll inside it', () => {
    render(<Search follows={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    scroll(screen.getByTestId('flyout'))
    expect(isOpen()).toBe(true)
  })

  it('a flyout not asked to follow closes on a scroll outside it, focus in it or not', () => {
    render(<Search follows={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    screen.getByRole('textbox', { name: 'Query' }).focus()
    scroll(window)
    expect(isOpen()).toBe(false)
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
