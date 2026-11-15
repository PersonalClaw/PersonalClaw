// A dense icon button can say its label in the themed tooltip rather than a native `title`, so a
// page with no browser tooltip (Home's pinned tiles) can use it; its other callers keep theirs.
import { fireEvent, render, screen } from '@testing-library/react'
import { X } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import { SquareIconButton } from './SquareIconButton'

describe('a square icon button with a tip', () => {
  it('says its label in the themed tooltip and carries no native title', () => {
    render(<SquareIconButton icon={X} label="Remove from Home" tip />)
    const button = screen.getByRole('button', { name: 'Remove from Home' })
    expect(button.hasAttribute('title')).toBe(false)
    fireEvent.focus(button)
    expect(screen.getAllByText('Remove from Home').length).toBeGreaterThan(0)
  })

  it('keeps the native title for a caller that passes no tip', () => {
    render(<SquareIconButton icon={X} label="Remove" />)
    expect(screen.getByRole('button', { name: 'Remove' }).getAttribute('title')).toBe('Remove')
  })
})
