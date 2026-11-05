// @vitest-environment jsdom
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { FilterSections, toggledKey, type FilterSectionDef } from './FilterMenu'

// ── A filter section that holds several choices at once ──────────────────────────────────────────
//
// Chat history's tags were a row of pressed chips under its header, any of them on at once (a chat
// shows if it carries any chosen tag). In the header's filter they are its Tag section, so a
// section can hold several keys: its value is the chosen keys joined by commas, and each row adds or
// takes out its own.

describe('toggledKey', () => {
  it('adds a key, takes it out again, and keeps the others in their order', () => {
    expect(toggledKey('', 'a')).toBe('a')
    expect(toggledKey('a', 'b')).toBe('a,b')
    expect(toggledKey('a,b', 'a')).toBe('b')
    expect(toggledKey('b', 'b')).toBe('')
  })
})

/** The rows the section has chosen, as a screen reader hears them: pressed. */
const chosen = () => screen.queryAllByRole('button', { pressed: true }).map((b) => b.textContent?.trim())

function Tags() {
  const [value, setValue] = useState('')
  const sections: FilterSectionDef[] = [{
    title: 'Tag', value, defaultKey: '', multi: true, onChange: setValue,
    options: [{ key: 'work', label: 'Work' }, { key: 'home', label: 'Home' }, { key: 'trip', label: 'Trip' }],
  }]
  return <FilterSections sections={sections} close={() => {}} />
}

describe('a multi section', () => {
  it('chooses several rows, and a second press takes one out', () => {
    render(<Tags />)
    expect(chosen()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Work' }))
    fireEvent.click(screen.getByRole('button', { name: 'Trip' }))
    expect(chosen()).toEqual(['Work', 'Trip'])
    fireEvent.click(screen.getByRole('button', { name: 'Work' }))
    expect(chosen()).toEqual(['Trip'])
  })

  it('offers Clear while anything is chosen, and Clear chooses none', () => {
    render(<Tags />)
    expect(screen.queryByRole('button', { name: 'Clear' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Home' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(chosen()).toEqual([])
  })
})

describe("Chat history's tags are its filter's Tag section", () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/chat/history/ChatHistoryPage.tsx'), 'utf8')
  it('a multi, quiet section over every tag, out of the Rooms scope', () => {
    expect(src).toMatch(/\.\.\.\(!roomsScope && tags\.length > 0 \? \[\{\s*title: 'Tag', value: \[\.\.\.tagFilter\]\.join\(','\), defaultKey: '', multi: true, quiet: true,/)
  })
  it('and no row of tag chips is left in the body', () => {
    expect(src).not.toMatch(/aria-label="Filter by tag"/)
  })
})
