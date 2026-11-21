import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, within, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { createRef } from 'react'
import { FindBar } from './FindBar'

// ── A step to a match lands on the match ────────────────────────────────────────────────────────
//
// The bar scrolled each stop's whole item to the middle of the view, so a match deep inside a long
// item (a long message, its fold opened or not) stayed out of sight. Now a step asks the host to
// reveal the item first (`revealOf`, which opens what hides it), and once that has rendered the
// view is scrolled to the item's first occurrence. Where the page has no layout to measure, or the
// root does not scroll, it falls back to the item as before.

const RANGE_RECT = Object.getOwnPropertyDescriptor(Range.prototype, 'getBoundingClientRect')
afterEach(() => {
  cleanup()
  document.body.replaceChildren()
  if (RANGE_RECT) Object.defineProperty(Range.prototype, 'getBoundingClientRect', RANGE_RECT)
  else delete (Range.prototype as { getBoundingClientRect?: unknown }).getBoundingClientRect
})

const ITEMS = ['nothing here', 'a long item whose quokka sits far down']

/** A scroll root that scrolls (taller content than its box), with the items as its children. */
function mount(revealOf?: (item: string, index: number) => void) {
  const root = document.createElement('div')
  document.body.appendChild(root)
  Object.defineProperty(root, 'clientHeight', { configurable: true, value: 400 })
  Object.defineProperty(root, 'scrollHeight', { configurable: true, value: 4000 })
  root.scrollTop = 0
  root.getBoundingClientRect = () => ({ top: 100, bottom: 500, left: 0, right: 600, height: 400, width: 600, x: 0, y: 100, toJSON: () => ({}) })
  root.scrollTo = vi.fn() as unknown as typeof root.scrollTo
  const nodes = ITEMS.map((text) => {
    const el = document.createElement('p')
    el.textContent = text
    el.scrollIntoView = vi.fn()
    root.appendChild(el)
    return el
  })
  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = root
  const { container } = render(
    <FindBar items={ITEMS} segmentsOf={(s) => [s]} nodeOf={(_s, i) => nodes[i]} revealOf={revealOf}
      scrollRef={scrollRef} label="Find in page" onClose={() => {}} />,
  )
  return { root, nodes, container }
}

async function search(container: HTMLElement, term: string) {
  fireEvent.change(within(container).getByLabelText('Find in page'), { target: { value: term } })
  await waitFor(() => expect(within(container).getByText('1/1')).toBeTruthy())
  fireEvent.click(within(container).getByLabelText('Next match'))
}

describe('a step to a match', () => {
  it('🔴 asks the host to reveal the item, then centres the occurrence, not the item', async () => {
    // The occurrence's box, 600px below the root's top edge.
    Object.defineProperty(Range.prototype, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ top: 700, bottom: 720, left: 0, right: 60, height: 20, width: 60, x: 0, y: 700, toJSON: () => ({}) }),
    })
    const revealOf = vi.fn()
    const { root, nodes, container } = mount(revealOf)

    await search(container, 'quokka')

    expect(revealOf).toHaveBeenCalledWith(ITEMS[1], 1)
    await waitFor(() => expect(root.scrollTo).toHaveBeenCalledWith({ top: 600 - 190, behavior: 'smooth' }))
    expect(nodes[1].scrollIntoView, 'it scrolled the item, not the match').not.toHaveBeenCalled()
  })

  it('scrolls the item as before where there is no layout to measure', async () => {
    const { root, nodes, container } = mount()

    await search(container, 'quokka')

    await waitFor(() => expect(nodes[1].scrollIntoView).toHaveBeenCalled())
    expect(root.scrollTo).not.toHaveBeenCalled()
  })
})
