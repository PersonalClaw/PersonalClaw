import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ModelWaitNotice, waitSentences } from './ModelWaitNotice'
import type { ShownWait } from '../lib/useModelWaits'

// A request somebody waits for, behind a local model busy with knowledge processing. Measured:
// the chat read "Thinking…" and the loop page "Analyzing…" for minutes, with no reason and no way
// forward. The notice names what waits, what holds the model, and what happens next.

const NOW = 1_000_000

function wait(over: Partial<ShownWait> = {}): ShownWait {
  return {
    id: 'w1',
    step: 'Analyzing the task',
    session: '',
    model: 'local-bg:stone:12b',
    busy_with: 'knowledge processing',
    next: 'cloud:swift-1',
    waited_secs: 3,
    left_secs: 12,
    movesOnAt: NOW + 12_000,
    ...over,
  }
}

describe('ModelWaitNotice', () => {
  it('says why the request waits and when the next model is asked', () => {
    const { why, next } = waitSentences(wait(), NOW)
    expect(why).toBe(
      'Analyzing the task is waiting for stone:12b, the local model: it is busy with knowledge processing.',
    )
    expect(next).toBe('It asks swift-1 instead in 12 s.')
  })

  it('counts down against the time the list was read', () => {
    expect(waitSentences(wait(), NOW + 5_000).next).toBe('It asks swift-1 instead in 7 s.')
    expect(waitSentences(wait(), NOW + 12_000).next).toBe('Asking swift-1 instead…')
  })

  it('with no other model, says it starts once the model is free and what would take over', () => {
    const { next } = waitSentences(wait({ next: '', left_secs: null, movesOnAt: null }), NOW)
    expect(next).toBe(
      'It starts as soon as that is done. A second model for this use in Settings → Models would take over instead of waiting.',
    )
  })

  it('offers to ask the next model now, and asks it', () => {
    const onMoveOn = vi.fn()
    render(<ModelWaitNotice waits={[wait()]} onMoveOn={onMoveOn} />)
    expect(screen.getByRole('status')).toHaveTextContent('busy with knowledge processing')
    fireEvent.click(screen.getByRole('button', { name: 'Ask swift-1 now' }))
    expect(onMoveOn).toHaveBeenCalledWith('w1')
  })

  it('offers nothing to move on to when there is no other model', () => {
    render(<ModelWaitNotice waits={[wait({ next: '', left_secs: null, movesOnAt: null })]} onMoveOn={() => {}} />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders nothing while nothing waits', () => {
    const { container } = render(<ModelWaitNotice waits={[]} onMoveOn={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})
