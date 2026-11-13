import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { chatContextChips } from './ChatContextLine'

// ── The chat header names its project with the project's chip ───────────────────────────────────
//
// The context line under a chat's title drew its project as a `ContextChip`; it is now the project's
// own chip (`ProjectChip`), the link every surface opens a project by, named for the project.

describe("the chat header's project", () => {
  it('is a link that opens the project, named for it', () => {
    render(<>{chatContextChips({ project: { id: 'p-garden', name: 'Garden' } })}</>)
    const link = screen.getByRole('link', { name: 'Open project Garden' })
    expect(link.getAttribute('href')).toBe('#/projects/p-garden')
  })

  it('is not drawn for a chat in no project', () => {
    expect(chatContextChips({ project: null })).toEqual([])
  })
})
