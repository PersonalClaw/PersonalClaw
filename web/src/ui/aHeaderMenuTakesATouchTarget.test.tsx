// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Plus } from 'lucide-react'
import { HeaderActions, HeaderMenu } from './HeaderActions'
import { TOUCH_TARGET } from './Toggle'

// ── A header menu's trigger takes the compact widths' touch target ───────────────────────────────
//
// `HeaderControl` takes a `className` for its button, and a page passes `TOUCH_TARGET` there so the
// control is a 44 px box from 320 to 768 px. `HeaderMenu` took none, so its trigger stayed a 40 px
// `size-10` (icon only) or `h-10` pill, and a page whose primary action opens a menu (the library's
// Add, a project's New) had a control under the 44 px floor. It takes one now, added to the
// trigger as `HeaderControl` adds its own, and without it the trigger is as before.

const items = [{ label: 'Upload files', onSelect: () => {} }]
const trigger = () => screen.getByRole('button', { name: 'Add' })

describe("a header menu's trigger", () => {
  it('🔑 takes TOUCH_TARGET, keeping its name and its menu', () => {
    render(<HeaderActions><HeaderMenu icon={Plus} label="Add" variant="primary" items={items} className={TOUCH_TARGET} /></HeaderActions>)
    const button = trigger()
    for (const cls of TOUCH_TARGET.split(' ')) expect(button.classList.contains(cls), cls).toBe(true)
    expect(button.getAttribute('aria-haspopup')).toBe('menu')
    expect(button.title).toBe('Add')
  })

  it('🔑 one passed none renders as today: a 40 px trigger', () => {
    render(<HeaderActions><HeaderMenu icon={Plus} label="Add" variant="primary" items={items} /></HeaderActions>)
    const classes = [...trigger().classList]
    expect(classes.some((c) => c.includes('min-h-11') || c.includes('min-w-11'))).toBe(false)
    expect(classes.includes('h-10') || classes.includes('size-10')).toBe(true)
  })
})
