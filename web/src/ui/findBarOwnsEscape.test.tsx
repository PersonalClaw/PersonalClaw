import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, within, fireEvent, cleanup } from '@testing-library/react'
import { createRef } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { FindBar } from './FindBar'

// ── One Escape was dismissing two layers ─────────────────────────────────────────────────────────
//
// `FindBar` is transient and holds focus, and both surfaces that host it sit under a window-level
// Escape handler that dismisses something bigger. In chat that is `ChatFilePanel`, which closes the
// open file on a bare `window` keydown. Driven: open a file from chat, press ⌘F, press Escape — the
// find bar closed AND the file panel closed, so the reader had to re-open a file they never asked to
// close.
//
// 🔑 THE MECHANISM IS `preventDefault` NOT BEING `stopPropagation`. The bar called only the former,
// which cancels the browser's default action and does nothing at all to the event's journey up the
// tree. React attaches its listener at the ROOT CONTAINER, which is below `window` in the bubble
// path, so stopping the synthetic event there (React forwards it to the native event) means the
// window handler never runs. That ordering is the whole fix, and the test below asserts it against a
// real `window` listener rather than trusting the reasoning.
//
// 🪤 SCOPE: THIS FIXES THE FIND-BAR PAIR ONLY, AND SAYS SO. The sibling half of the same defect —
// the command palette over a docked file panel — is NOT fixed here and cannot be by this mechanism:
// `app/CommandPalette` binds Escape on `window` too, and `stopPropagation` does not stop other
// listeners on the SAME target. Nor is a focus-scoped guard on the panel an option: measured, the
// docked panel never takes focus on open, and the usual gesture is clicking a file chip in a
// message — so "act only when the panel owns focus" would break the primary flow, where Escape
// straight after opening is exactly what a reader does. That half needs a real dismiss-layer
// registry (26 modules bind a global Escape) or the deferred `ui/Modal` refactor of the palette.

const oneSegment = (s: string) => [s]

afterEach(() => cleanup())

/** The bar, plus the window-level Escape handler its host surfaces really have. */
function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = host

  const onClose = vi.fn()
  const outerEscape = vi.fn()
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') outerEscape() }
  window.addEventListener('keydown', onKey)

  const { container } = render(
    <FindBar items={['a paragraph of prose']} segmentsOf={oneSegment} nodeOf={() => null}
      scrollRef={scrollRef} label="Find in page" onClose={onClose} />,
  )
  return { container, onClose, outerEscape, detach: () => window.removeEventListener('keydown', onKey) }
}

describe('the focused find bar owns Escape', () => {
  it('closes itself and does NOT reach the surface behind it', () => {
    const { container, onClose, outerEscape, detach } = mount()
    try {
      // From the container, which is where the binding lives (so this also covers the
      // Previous/Next/Close tab stops, not just the input).
      fireEvent.keyDown(container.querySelector('[role="search"]')!, { key: 'Escape' })
      expect(onClose, 'the bar must still close').toHaveBeenCalledTimes(1)
      expect(outerEscape, 'a second layer also dismissed on one press').not.toHaveBeenCalled()
    } finally { detach() }
  })

  it('from the INPUT too — the same press, one tab stop deeper', () => {
    const { container, onClose, outerEscape, detach } = mount()
    try {
      fireEvent.keyDown(within(container).getByLabelText('Find in page'), { key: 'Escape' })
      expect(onClose).toHaveBeenCalledTimes(1)
      expect(outerEscape).not.toHaveBeenCalled()
    } finally { detach() }
  })

  it('🪤 does NOT swallow any other key — only Escape is claimed', () => {
    // Over-stopping would be its own bug: the surfaces behind this bar bind ⌘S, ⌘K, ⌘W and `/` on
    // window, and a blanket `stopPropagation` in this handler would break every one of them while
    // the bar is open.
    const { container, outerEscape, detach } = mount()
    const otherKeys: string[] = []
    const spy = (e: KeyboardEvent) => otherKeys.push(e.key)
    window.addEventListener('keydown', spy)
    try {
      const bar = container.querySelector('[role="search"]')!
      fireEvent.keyDown(bar, { key: 's', metaKey: true })
      fireEvent.keyDown(bar, { key: 'k', metaKey: true })
      fireEvent.keyDown(bar, { key: 'Enter' })
      expect(otherKeys, 'non-Escape keys must still reach the surface').toEqual(['s', 'k', 'Enter'])
      expect(outerEscape).not.toHaveBeenCalled()
    } finally { detach(); window.removeEventListener('keydown', spy) }
  })
})

describe('the fix is the propagation call, not the default call', () => {
  it('the handler stops propagation as well as the default', () => {
    // Pinned structurally as well as behaviourally: the two calls look interchangeable and are not,
    // which is exactly how the bug shipped.
    const code = readFileSync(join(process.cwd(), 'src', 'ui', 'FindBar.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const handler = code.match(/onKeyDown=\{\(e\) => \{ if \(e\.key === 'Escape'\)([^}]*)\}/)?.[1] ?? ''
    expect(handler, 'found the container Escape handler').not.toBe('')
    expect(handler).toMatch(/e\.stopPropagation\(\)/)
    expect(handler).toMatch(/e\.preventDefault\(\)/)
  })
})
