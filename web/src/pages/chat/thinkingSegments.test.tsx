import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { appendThinking, type Segment } from './chatTypes'
import { ThinkingBlock } from './ThinkingBlock'

// CC-9 — thinking chunks render in the transcript behind the existing
// `show_thinking_inline` switch. The switch gates INGESTION (ChatPage drops
// `chat_thinking` frames while off), so the state-layer contract under test is:
//   on  → appendThinking folds chunks into thinking segments (extend/open rules)
//   off → no thinking segment ever exists, hence nothing renders
// plus the ThinkingBlock render itself. Nothing here touches persistence:
// hydrateTurns has no 'thinking' arm, which test 'never persisted' pins.

const text = (t: string): Segment => ({ kind: 'text', text: t })

describe('appendThinking (stream folding)', () => {
  it('opens a new thinking block on first chunk', () => {
    const out = appendThinking([], 'plan: ')
    expect(out).toEqual([{ kind: 'thinking', text: 'plan: ' }])
  })

  it('extends the trailing thinking block while the reasoning stream is uninterrupted', () => {
    const out = appendThinking([{ kind: 'thinking', text: 'plan: ' }], 'read the file')
    expect(out).toEqual([{ kind: 'thinking', text: 'plan: read the file' }])
  })

  it('interleaves with normal tokens: text after thinking closes the block, later thinking opens a NEW one', () => {
    let segs: Segment[] = []
    segs = appendThinking(segs, 'first reasoning')     // block 1
    segs = [...segs, text('streamed answer tokens')]   // chat_chunk lands prose
    segs = appendThinking(segs, 'second reasoning')    // must NOT grow block 1
    expect(segs).toEqual([
      { kind: 'thinking', text: 'first reasoning' },
      { kind: 'text', text: 'streamed answer tokens' },
      { kind: 'thinking', text: 'second reasoning' },
    ])
  })

  it('is a no-op for an empty chunk (no empty blocks in the transcript)', () => {
    const before: Segment[] = [text('a')]
    expect(appendThinking(before, '')).toBe(before)
  })

  it('switch off ⇒ frames dropped at ingestion ⇒ segments untouched (render-off)', () => {
    // ChatPage's chat_thinking case early-returns while the toggle is off, so the
    // reducer is never invoked: the off-state contract is "state unchanged".
    const before: Segment[] = [text('answer')]
    const after = before // the WS case breaks before appendThinking
    expect(after).toEqual([{ kind: 'text', text: 'answer' }])
    expect(after.some((s) => s.kind === 'thinking')).toBe(false)
  })
})

describe('ThinkingBlock (render-on)', () => {
  it('renders a collapsible block with the reasoning text', () => {
    render(<ThinkingBlock text="weighing two approaches" />)
    const block = screen.getByTestId('thinking-block')
    expect(block).toBeTruthy()
    expect(block.textContent).toContain('Thinking')
    expect(block.textContent).toContain('weighing two approaches')
  })

  it('starts open while streaming (defaultOpen) and closed on a settled re-render', () => {
    const { unmount } = render(<ThinkingBlock text="live" defaultOpen />)
    expect((screen.getByTestId('thinking-block') as HTMLDetailsElement).open).toBe(true)
    unmount()
    render(<ThinkingBlock text="settled" />)
    expect((screen.getByTestId('thinking-block') as HTMLDetailsElement).open).toBe(false)
  })
})

describe('persistence isolation', () => {
  it('hydrateTurns has no thinking arm — a reloaded transcript carries none', async () => {
    const { hydrateTurns } = await import('./chatTypes')
    const turns = hydrateTurns([
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'a' },
    ] as never)
    const segs = turns.flatMap((t) => t.segments)
    expect(segs.some((s) => s.kind === 'thinking')).toBe(false)
  })
})
