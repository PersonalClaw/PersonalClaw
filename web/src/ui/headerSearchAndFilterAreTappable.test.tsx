// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { HeaderFilter } from './HeaderFilter'
import { HeaderSearch } from './HeaderSearch'
import { TOUCH_TARGET } from './Toggle'

// ── The header's search and filter take the compact widths' 44 px hit box by default ─────────────
//
// Each page passed `TOUCH_TARGET` or did not, so the UI matrix's 44 px check found the filter and
// the search under 44 px on most surfaces below 1024 px, and the baselines accepted them. The two
// members carry it themselves now, so those entries leave the baselines.

const classes = (el: Element) => el.getAttribute('class')?.split(/\s+/) ?? []

describe('the header filter', () => {
  it('has the hit box with no className from the page', () => {
    render(<HeaderFilter sections={[{ title: 'Status', value: 'open', defaultKey: 'open', onChange: () => {}, options: [{ key: 'open', label: 'Open' }] }]} />)
    const trigger = screen.getByRole('button', { name: 'Show: Open' })
    for (const c of TOUCH_TARGET.split(' ')) expect(classes(trigger)).toContain(c)
  })
})

describe('the header search', () => {
  it("has it on its field's input", () => {
    render(<HeaderSearch value="" onChange={() => {}} placeholder="Search tasks" />)
    const wrap = screen.getByRole('searchbox', { name: 'Search tasks' }).closest('div[class*="max-lg:[&_input]:min-h-11"]')
    expect(wrap, 'the field sits in the hit-box wrapper').not.toBeNull()
  })

  it('and on its shed form, the Search button', () => {
    const src = readFileSync(join(process.cwd(), 'src/ui/HeaderSearch.tsx'), 'utf8')
    const button = src.slice(src.indexOf('<button type="button" onClick={toggle} aria-haspopup="dialog"'))
    expect(button.slice(0, button.indexOf('</button>'))).toContain('TOUCH_TARGET,')
  })
})
