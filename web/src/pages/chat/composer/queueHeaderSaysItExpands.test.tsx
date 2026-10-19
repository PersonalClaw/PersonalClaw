import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

// The queued-messages header folds two or more queued messages into a deck and fans them out on a
// click. It published nothing about that, so a screen reader heard a button and no state. It now
// says whether the list is open, and only while it is a toggle: one queued message has no deck.
let reduce = false
vi.mock('framer-motion', async (importOriginal) => ({
  ...(await importOriginal<typeof import('framer-motion')>()),
  useReducedMotion: () => reduce,
}))

import { QueueStack } from './QueueStack'

const q = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `q${i}`, content: `message ${i + 1}` }))
const header = () => screen.getByRole('button', { name: /queued · sent one at a time/ })
const noop = () => {}

afterEach(() => { cleanup(); reduce = false })

describe('the queued-messages header says whether the deck is open', () => {
  it('one queued message is not a toggle, so it claims no state', () => {
    render(<QueueStack items={q(1)} onCancel={noop} onEdit={noop} />)
    expect(header()).not.toHaveAttribute('aria-expanded')
  })

  it('two or more fold into a deck, and the header announces each click', () => {
    render(<QueueStack items={q(3)} onCancel={noop} onEdit={noop} />)
    expect(header()).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(header())
    expect(header()).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(header())
    expect(header()).toHaveAttribute('aria-expanded', 'false')
  })

  it('under reduced motion the list is always flat, so it never says collapsed', () => {
    reduce = true
    render(<QueueStack items={q(3)} onCancel={noop} onEdit={noop} />)
    expect(header()).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByRole('button', { name: 'Cancel queued message' })).toHaveLength(3)
  })
})
