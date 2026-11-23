// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Filter, Plus, Upload } from 'lucide-react'
import { HeaderActions, HeaderControl, HeaderMenu, HeaderPopover, overflowTriggerBox } from './HeaderActions'

// ── Every header control is a 44 px target on a phone ───────────────────────────────────────────
//
// A header control's button was a 40 px box (`size-10` icon only, `h-10` with its label), and a page
// passed `TOUCH_TARGET` to the ones it wanted at 44 px from 320 to 768 px. Most passed nothing, so
// the UI quality matrix held them under 44 px on page after page (a project list's Import, Loops,
// More actions and New project at 320 to 768 px, in both themes). Every header control's button
// takes the compact widths' box now, the `…` trigger with it, and the row counts the `…` at the
// box it is drawn at. jsdom has no layout, so the classes that set the box are pinned; the matrix
// measures it.

const PHONE_BOX = ['max-lg:min-h-11', 'max-lg:min-w-11']
const classes = (el: Element) => [...el.classList]

describe('every header control is a 44 px target on a phone', () => {
  it('🔴 a control, a menu, a popover and the `…` trigger, with nothing passed', () => {
    render(
      <HeaderActions>
        <HeaderControl icon={Upload} label="Import" onClick={() => {}} />
        <HeaderMenu icon={Plus} label="New" items={[{ label: 'Task', onSelect: () => {} }]} />
        <HeaderPopover icon={Filter} label="Filters">{() => null}</HeaderPopover>
        <HeaderControl label="Export everything" onClick={() => {}} inOverflow />
      </HeaderActions>,
    )
    for (const name of ['Import', 'New', 'Filters', 'More actions']) {
      expect(classes(screen.getByRole('button', { name })), name).toEqual(expect.arrayContaining(PHONE_BOX))
    }
  })

  it('🔴 the row counts the `…` at the box it is drawn at: 44 px below the large breakpoint, 40 above', () => {
    // The row's arithmetic took the `…` as 40 px and a 4 px gap at every width, so a 44 px `…` and
    // its 8 px gap overran the room the row had left for it by 8 px.
    expect(overflowTriggerBox(true)).toBe(44)
    expect(overflowTriggerBox(false)).toBe(40)
  })
})
