import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { Download } from 'lucide-react'
import { Button } from './Button'
import { IconButton } from './IconButton'
import { QuietButton } from './QuietButton'
import { SquareIconButton } from './SquareIconButton'
import { TileButton } from './TileButton'
import { AddItemButton } from './AddItemButton'
import { HeaderControl } from './HeaderActions'
import { SearchField } from './SearchField'

// ── A button press shows by colour, at once ────────────────────────────────────────────────────
//
// Every button tier used to answer a press by shrinking (`whileTap` scale, 0.88 to 0.97) and a hover
// by growing (Button and IconButton, `whileHover` scale), on a spring. A button is pressed many times
// an hour, and a scale that repeats that often explains nothing: it only makes the control wobble
// under the pointer. So no tier moves: the press shows by the tier's colour, which changes at once.
//
// framer-motion's gestures are observable in jsdom: a `whileTap` writes `transform: scale(…)` on the
// element at pointer-down, and a `whileHover` writes one at pointer-enter. Measured on the parent of
// this change, 120 ms into each gesture: Button at `scale(0.96…)` pressed and `scale(1.02…)` hovered,
// SquareIconButton at `scale(0.90…)`, SearchField's clear-X at `scale(0.80…)`. So this reads the DOM
// rather than the source, and a scale wired back in under another prop name still fails here.
//
// The colour half: each tier carries an `active:` tint, the same shade its hover already shows, so a
// press is visible on a touch screen too, where no hover precedes it.

const SCALE = /scale\([^)]*\)/

/** *n* animation frames, the clock framer's springs and gestures write on. */
async function frames(n: number) {
  await act(async () => {
    for (let i = 0; i < n; i++) await new Promise((r) => requestAnimationFrame(() => r(undefined)))
  })
}

const scaleOf = (el: HTMLElement) => SCALE.exec(el.getAttribute('style') ?? '')?.[0] ?? ''

/** Frames in a row the element must show no scale before a gesture. A frame that lands where a
 *  spring crosses 1 writes `transform: none` mid-flight, and the clear-X's last swing below 1 after
 *  such a frame lasts about 190 ms (11 frames idle), so 15 quiet frames outlast it. */
const QUIET = 15

/** The inline scale framer wrote for the gesture, or '' when it wrote none.
 *
 *  The mount settles first, read off the element: SearchField's clear-X pops in on a spring when a
 *  value appears (a kept entrance), and read mid-flight that pop passes for a press. The pop takes
 *  about 480 ms idle, so this waits until the element has shown no scale for `QUIET` frames,
 *  rather than for a fixed time: a fixed 500 ms raced it, and main's run 40108768003 read the pop
 *  at `scale(0.877…)`. Then the gesture gets eight frames (about the 120 ms the measurements above
 *  took idle), so a slow runner only makes the read later. */
async function transformAfter(el: HTMLElement, gesture: 'press' | 'hover'): Promise<string> {
  for (let quiet = 0, waited = 0; quiet < QUIET; waited++) {
    if (waited === 400) throw new Error(`the mount never settled: ${el.getAttribute('style')}`)
    await frames(1)
    quiet = scaleOf(el) ? 0 : quiet + 1
  }
  await act(async () => {
    if (gesture === 'press') fireEvent.pointerDown(el, { button: 0, isPrimary: true, pointerType: 'mouse' })
    else fireEvent.pointerEnter(el, { isPrimary: true, pointerType: 'mouse' })
  })
  await frames(8)
  return scaleOf(el)
}

const TIERS: [string, () => HTMLElement][] = [
  ['Button', () => { render(<Button onClick={vi.fn()}>Download</Button>); return screen.getByRole('button', { name: 'Download' }) }],
  ['IconButton', () => { render(<IconButton icon={Download} label="Download" onClick={vi.fn()} />); return screen.getByRole('button', { name: 'Download' }) }],
  ['QuietButton', () => { render(<QuietButton onClick={vi.fn()}>Download</QuietButton>); return screen.getByRole('button', { name: 'Download' }) }],
  ['SquareIconButton', () => { render(<SquareIconButton icon={Download} label="Download" onClick={vi.fn()} />); return screen.getByRole('button', { name: 'Download' }) }],
  ['TileButton', () => { render(<TileButton onClick={vi.fn()} ariaLabel="Download">tile body</TileButton>); return screen.getByRole('button', { name: 'Download' }) }],
  ['AddItemButton', () => { render(<AddItemButton onClick={vi.fn()}>Download</AddItemButton>); return screen.getByRole('button', { name: 'Download' }) }],
  ['HeaderControl', () => { render(<HeaderControl icon={Download} label="Download" onClick={vi.fn()} />); return screen.getByRole('button', { name: 'Download' }) }],
  ['SearchField clear', () => {
    render(<SearchField value="logs" onChange={vi.fn()} ariaLabel="Search runs" />)
    return screen.getByRole('button', { name: /clear/i })
  }],
]

describe('no button tier moves on press', () => {
  for (const [name, mount] of TIERS) {
    it(`${name} writes no scale at pointer-down`, async () => {
      const el = mount()
      expect(await transformAfter(el, 'press'), `${name} still scales on press`).toBe('')
    })
  }
})

describe('no button tier moves on hover', () => {
  for (const [name, mount] of TIERS) {
    it(`${name} writes no scale at pointer-enter`, async () => {
      const el = mount()
      expect(await transformAfter(el, 'hover'), `${name} still scales on hover`).toBe('')
    })
  }
})

describe('the press shows by colour', () => {
  for (const [name, mount] of TIERS) {
    it(`${name} carries an active: tint`, () => {
      const el = mount()
      expect(el.getAttribute('class') ?? '', `${name} shows nothing while pressed`).toMatch(/(^|\s)active:(bg|text|border|opacity)-/)
    })
  }

  it('a disabled button still clicks nothing, now that nothing scales either', () => {
    const onClick = vi.fn()
    render(<QuietButton onClick={onClick} disabled disabledReason="Busy">Download</QuietButton>)
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    expect(onClick).not.toHaveBeenCalled()
  })
})
