import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef } from 'react'
import { render, waitFor, within, fireEvent } from '@testing-library/react'
import { FindBar } from './FindBar'
import type { ChatTurn } from './chatTypes'

// #546 regression cover for the PAINTER half of find-in-conversation. The scanner
// is covered in findMatches.test.ts; this drives the real component so the paint
// loop's own offset math (and its defensive clamp) is exercised, not a copy of it.
//
// jsdom ships neither CSS.highlights nor Highlight, so we stub the minimum the
// component feature-detects: a Map to write into and a constructor that records
// the Ranges it was handed. The Ranges themselves are real jsdom Ranges, so an
// out-of-bounds offset throws a real DOMException — which is the bug's mechanism.

interface Win { CSS?: { highlights?: Map<string, unknown> }; Highlight?: new (...r: Range[]) => unknown }

class FakeHighlight {
  ranges: Range[]
  constructor(...ranges: Range[]) { this.ranges = ranges }
}

function installHighlightApi() {
  const w = window as unknown as Win
  const highlights = new Map<string, unknown>()
  w.CSS = { ...(w.CSS ?? {}), highlights }
  w.Highlight = FakeHighlight as unknown as new (...r: Range[]) => unknown
  return highlights
}

function uninstallHighlightApi() {
  const w = window as unknown as Win
  delete w.CSS
  delete w.Highlight
}

const turnOf = (text: string): ChatTurn => ({ role: 'user', segments: [{ kind: 'text', text }] })

/** Render the bar over a scroll container holding `text`, type `query`, and return
 *  the strings the painter actually highlighted. */
async function paintedTexts(text: string, query: string): Promise<string[]> {
  const highlights = installHighlightApi()
  const host = document.createElement('div')
  host.textContent = text
  document.body.appendChild(host)
  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = host
  const turnNodes = { current: new Map<number, HTMLDivElement>() }

  // Scope queries to this render's own container — each call adds another bar to
  // document.body (RTL only cleans up between tests, not within one).
  const { container } = render(
    <FindBar turns={[turnOf(text)]} scrollRef={scrollRef} turnNodes={turnNodes} onClose={() => {}} />,
  )
  const input = within(container).getByLabelText('Find in conversation')
  // Set the value directly: enough for the controlled field, and keeps this test
  // independent of user-event's per-keystroke timing.
  fireEvent.change(input, { target: { value: query } })

  let painted: string[] = []
  await waitFor(() => {
    const h = highlights.get('pc-find') as FakeHighlight | undefined
    expect(h).toBeDefined()
    painted = (h as FakeHighlight).ranges.map((r) => r.toString())
  }, { timeout: 2000 })
  host.remove()
  return painted
}

describe('FindBar painter (#546)', () => {
  beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { uninstallHighlightApi(); vi.restoreAllMocks() })

  it('highlights the right characters when İ (U+0130) precedes the match', async () => {
    // The folded copy is longer, so the old math painted "et" (or threw). One İ is
    // enough to push the end offset past the node length.
    expect(await paintedTexts('İİİİtarget', 'target')).toEqual(['target'])
    expect(await paintedTexts('İtarget', 'target')).toEqual(['target'])
  })

  it('paints every occurrence instead of blanking the page on one bad node', async () => {
    // Previously the first out-of-range setEnd threw and aborted the whole walk,
    // so NOTHING on the page highlighted. Now later matches still paint.
    expect(await paintedTexts('İtarget and target again', 'target')).toEqual(['target', 'target'])
  })

  it('paints the correct span mid-text (the silent off-by-one case)', async () => {
    expect(await paintedTexts('İzmir kiln target 1240C', 'target')).toEqual(['target'])
  })

  it('still paints plain ASCII matches case-insensitively', async () => {
    expect(await paintedTexts('Docker Compose and docker again', 'DOCKER')).toEqual(['Docker', 'docker'])
  })
})
