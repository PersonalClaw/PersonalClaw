// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { HeaderFilter } from './HeaderFilter'

// ── A header filter says which choice is chosen, and in which set ────────────────────────────────
//
// The chip rows, tag groups and the Store's rail these sections replaced announced their state:
// a group named for its dimension, its chosen entry pressed. The sections marked the choice with
// a tint and a check only, both hidden from a screen reader, and their titles were bare text. Each
// section is a group named by its title now, and its rows are pressed or not.

function Filter({ tags = 'trip' }: { tags?: string }) {
  return (
    <HeaderFilter sections={[
      { title: 'Type', value: 'note', defaultKey: 'all', onChange: () => {},
        options: [{ key: 'all', label: 'All types' }, { key: 'note', label: 'Note' }, { key: 'bookmark', label: 'Bookmark' }] },
      { title: 'Tag', value: tags, defaultKey: '', multi: true, onChange: () => {},
        options: [{ key: 'trip', label: 'trip' }, { key: 'work', label: 'work' }, { key: 'home', label: 'home' }] },
    ]} />
  )
}

const open = () => fireEvent.click(screen.getByRole('button', { expanded: false }))
const pressed = (title: string) => within(screen.getByRole('group', { name: title }))
  .queryAllByRole('button', { pressed: true }).map((b) => b.textContent?.trim())

describe('a header filter and its chosen rows', () => {
  it('names each section as a group and presses its chosen row', () => {
    render(<Filter />)
    open()
    expect(pressed('Type')).toEqual(['Note'])
    expect(within(screen.getByRole('group', { name: 'Type' })).getAllByRole('button', { pressed: false })
      .map((b) => b.textContent?.trim())).toEqual(['All types', 'Bookmark'])
  })

  it('presses every chosen row of a section that takes several', () => {
    render(<Filter tags="trip,home" />)
    open()
    expect(pressed('Tag')).toEqual(['trip', 'home'])
  })
})
