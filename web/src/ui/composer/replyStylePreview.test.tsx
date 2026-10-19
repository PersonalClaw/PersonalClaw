import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ReplyStylePill, type ReplyStyleOption, type ReplyStylePreviewView } from './controls'

// ── The picker previews what the next turn carries for its voice ───────────────────────────
//
// The preview is the backend's (`reply_style.preview`), read from the same resolution the turn
// uses, so it names whether the chosen style or the identity's persona applies and shows the exact
// text appended. The picker renders it as it arrives; it never composes an instruction itself.

const STYLES: ReplyStyleOption[] = [
  { id: 'default', title: 'Default', description: 'No style instruction.' },
  { id: 'terse', title: 'Terse', description: 'Short and direct.' },
]

const base = { choice: 'terse', effective: 'terse', source: 'conversation', agentDefault: '', styles: STYLES }

function open(preview: ReplyStylePreviewView | null) {
  render(<ReplyStylePill {...base} preview={preview} onSelect={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: /Reply style:/ }))
}

describe('the reply-style preview', () => {
  it('names the style when a style applies, and shows its exact text on request', () => {
    open({ applies: 'style', style: 'terse', persona: 'retro-terminal', instruction: '[REPLY STYLE: Terse]\nBe brief.\n[END REPLY STYLE]', first_turn_only: false, refused: '' })
    expect(screen.getByText('Next reply: the Terse style, on every turn.')).toBeTruthy()
    expect(screen.getByText(/replaces the Retro Terminal voice/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Show what is sent' }))
    expect(screen.getByText(/Be brief\./)).toBeTruthy()
  })

  it('names the persona when no style is chosen, and says it is first turn only', () => {
    open({ applies: 'persona', style: 'default', persona: 'retro-terminal', instruction: '\n[RETRO TERMINAL PERSONA]\nOperator voice.\n[END RETRO TERMINAL PERSONA]\n\n', first_turn_only: true, refused: '' })
    expect(screen.getByText('Next reply: the Retro Terminal voice, on a new chat’s first turn.')).toBeTruthy()
  })

  it('says nothing is added when neither applies', () => {
    open({ applies: 'none', style: 'default', persona: '', instruction: '', first_turn_only: false, refused: '' })
    expect(screen.getByText('Next reply: no style instruction is added.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Show what is sent' })).toBeNull()
  })

  it('says why a refused style is not sent', () => {
    open({ applies: 'style', style: 'terse', persona: '', instruction: '', first_turn_only: false, refused: 'The Terse reply style’s text opens or closes the style section, so it is not sent.' })
    expect(screen.getByText(/opens or closes the style section, so it is not sent/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Show what is sent' })).toBeNull()
  })

  it('shows no preview line before the chat exists (nothing has been resolved yet)', () => {
    open(null)
    expect(screen.queryByText(/^Next reply:/)).toBeNull()
  })
})
