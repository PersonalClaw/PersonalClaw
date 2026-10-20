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

async function settle(ms: number) {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)) })
}

/** The inline scale framer wrote for the gesture, or '' when it wrote none.
 *
 *  The mount settles first: SearchField's clear-X pops in on a spring when a value appears (a kept
 *  entrance), and read mid-flight that pop would pass for a press. */
async function transformAfter(el: HTMLElement, gesture: 'press' | 'hover'): Promise<string> {
  await settle(500)
  await act(async () => {
    if (gesture === 'press') fireEvent.pointerDown(el, { button: 0, isPrimary: true, pointerType: 'mouse' })
    else fireEvent.pointerEnter(el, { isPrimary: true, pointerType: 'mouse' })
  })
  await settle(120)
  return /scale\([^)]*\)/.exec(el.getAttribute('style') ?? '')?.[0] ?? ''
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
