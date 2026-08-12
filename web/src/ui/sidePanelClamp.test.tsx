import { describe, expect, it, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { act } from 'react'
import { SidePanel } from './SidePanel'

// ── A docked panel must never be wider than the screen ───────────────────────────
//
// `SidePanel` docks `shrink-0` inside a shell whose overflow is `hidden` (not `auto`), so a stored
// width larger than the viewport is CLIPPED — there is no scroll to reach the cut-off part, and the
// part that gets cut off is the panel's own header cluster, which is where Expand and Close live.
//
// Measured live on `#/files` (whose explorer dock is open on arrival, which is why this surface is
// where it showed up), default 420px dock, `elementsFromPoint` at each control's visible centre:
//
//   viewport   #root scrollWidth   content column   Close button
//   390px      420 (30px clipped)  0px              34px wide, 19px VISIBLE
//   320px      420 (100px clipped) 0px              0px visible — elementsFromPoint → nothing
//
// At 320px — the width WCAG 2.1 SC 1.4.10 Reflow names — the page therefore had no content AND no
// pointer-reachable way to dismiss the thing covering it. (Escape still worked; a touch user has no
// Escape key.) After the clamp, at 320px: panel 32..320, content column 32px, Close fully visible at
// 271..305, and clicking it restores the content column to the full 320 with its empty state visible.
//
// This rail asserts the MECHANISM (the rendered width), not the source text: a `Math.min` that reads
// the wrong variable would still satisfy a grep. jsdom does no layout, so geometry is unavailable —
// the inline width the component computes is the honest thing to check.

const STORE_KEY = 'sidepanel-clamp-test'
const EDGE_PEEK = 32

function setViewport(w: number) {
  ;(window as unknown as { innerWidth: number }).innerWidth = w
}

/** The docked panel's inner content column carries the computed width. Returns the number of px it
 *  was rendered at — and throws rather than returning a falsy default if the node is missing, so a
 *  selector that stops matching fails the rail instead of quietly passing it. */
function renderedDockWidth(container: HTMLElement): number {
  const inner = container.querySelector<HTMLElement>('div.flex.h-full.flex-col')
  if (!inner) throw new Error('docked panel inner column not found — the selector no longer matches')
  const w = inner.style.width
  if (!/^\d+px$/.test(w)) throw new Error(`expected an explicit px width, got ${JSON.stringify(w)}`)
  return Number.parseInt(w, 10)
}

function mount(viewportW: number) {
  setViewport(viewportW)
  return render(
    <SidePanel title="Explorer" storeKey={STORE_KEY} fillHeight onClose={() => {}}>
      <p>body</p>
    </SidePanel>,
  )
}

describe('SidePanel clamps its docked width to the viewport', () => {
  beforeEach(() => {
    localStorage.clear()
    setViewport(1440)
  })

  it('does NOT engage on a desktop viewport — the stored width wins', () => {
    // The guard against over-correcting: a clamp that shrinks the dock on a wide screen would be a
    // visible regression on every page. Verified separately by pixel diff (4/4 captures identical at
    // 1440×900, both themes).
    const { container } = mount(1440)
    expect(renderedDockWidth(container)).toBe(420)
  })

  it.each([
    [390, 390 - EDGE_PEEK],
    [320, 320 - EDGE_PEEK],
  ])('at %ipx the dock renders %ipx — inside the screen, not clipped by it', (vw, expected) => {
    const { container } = mount(vw)
    const w = renderedDockWidth(container)
    expect(w).toBe(expected)
    // The load-bearing property, stated independently of the peek constant: the dock fits.
    expect(w).toBeLessThanOrEqual(vw)
  })

  it('clamps a WIDER stored width without overwriting it — a wide screen restores the choice', () => {
    localStorage.setItem(STORE_KEY, '720')
    const { container } = mount(390)
    expect(renderedDockWidth(container)).toBe(390 - EDGE_PEEK)
    // The user's 720 is their setting, not a bug to correct. Only the render is clamped.
    expect(localStorage.getItem(STORE_KEY)).toBe('720')
  })

  it('follows a resize — a rotation or a dragged window re-clamps', () => {
    const { container } = mount(1440)
    expect(renderedDockWidth(container)).toBe(420)
    act(() => {
      setViewport(360)
      window.dispatchEvent(new Event('resize'))
    })
    expect(renderedDockWidth(container)).toBe(360 - EDGE_PEEK)
    act(() => {
      setViewport(1440)
      window.dispatchEvent(new Event('resize'))
    })
    expect(renderedDockWidth(container)).toBe(420)
  })
})
