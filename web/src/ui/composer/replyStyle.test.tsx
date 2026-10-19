import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ReplyStylePill, type ReplyStyleOption } from './controls'

// ── The reply-style picker shows what actually takes effect, and never resolves it ──────────
//
// A style can be stated by the conversation (this picker), the bound agent, or the platform
// default. The order between them is stated ONCE, in Python (`reply_style.REPLY_STYLE_PRECEDENCE`),
// and the backend hands this component the resolved `effective` style and its `source`.
//
// 🪤 THE FAILURE THIS RAIL EXISTS FOR: working the effect out here from `choice` and the agent's
// own style. That is a second copy of the order, free to drift from the one the turn uses, and it
// lets the picker name a style the turn did not carry.

const STYLES: ReplyStyleOption[] = [
  { id: 'default', title: 'Default', description: 'No style instruction.' },
  { id: 'plain', title: 'Plain', description: 'Plainer prose.' },
  { id: 'terse', title: 'Terse', description: 'Short and direct.' },
  { id: 'conversational', title: 'Conversational', description: 'Fuller and warmer.' },
]

function pill(props: Partial<Parameters<typeof ReplyStylePill>[0]> = {}) {
  const onSelect = vi.fn()
  render(<ReplyStylePill choice="" effective="default" source="platform" agentDefault="" styles={STYLES} onSelect={onSelect} {...props} />)
  return onSelect
}

describe('the reply-style picker shows what actually takes effect', () => {
  it('names the conversation’s own style', () => {
    pill({ choice: 'terse', effective: 'terse', source: 'conversation' })
    expect(screen.getByRole('button', { name: 'Reply style: Terse' })).toBeTruthy()
  })

  it('names the AGENT when the agent is what chose it', () => {
    pill({ effective: 'conversational', source: 'agent', agentDefault: 'conversational' })
    expect(screen.getByRole('button', { name: 'Reply style: Conversational (agent)' })).toBeTruthy()
  })

  it('names the platform default when that is what applies', () => {
    pill({ effective: 'plain', source: 'platform' })
    expect(screen.getByRole('button', { name: 'Reply style: Plain (default)' })).toBeTruthy()
  })

  it('reads Default when no style applies', () => {
    pill({ effective: 'default', source: 'platform' })
    expect(screen.getByRole('button', { name: 'Reply style: Default' })).toBeTruthy()
  })

  it('a conversation’s Default over an agent’s style reads Default: the agent does not win', () => {
    pill({ choice: 'default', effective: 'default', source: 'conversation', agentDefault: 'terse' })
    expect(screen.getByRole('button', { name: 'Reply style: Default' })).toBeTruthy()
  })

  it('before a conversation exists it shows the CHOICE, never a guessed effect', () => {
    pill({ choice: '', source: '', agentDefault: 'terse' })
    expect(screen.getByRole('button', { name: 'Reply style: Agent default' })).toBeTruthy()
  })

  it('and a pre-session pick reads back as that pick', () => {
    pill({ choice: 'conversational', source: '' })
    expect(screen.getByRole('button', { name: 'Reply style: Conversational' })).toBeTruthy()
  })

  it('lists every style with its one-line description, plus the reset row', () => {
    pill()
    fireEvent.click(screen.getByRole('button', { name: /Reply style:/ }))
    for (const s of STYLES) {
      expect(screen.getByText(s.title === 'Default' ? 'No style' : s.title)).toBeTruthy()
      expect(screen.getByText(s.description)).toBeTruthy()
    }
    expect(screen.getByText('Agent default')).toBeTruthy()
  })

  it('a pick sends the style id, and the reset row sends ""', () => {
    const onSelect = pill({ choice: 'terse', effective: 'terse', source: 'conversation' })
    fireEvent.click(screen.getByRole('button', { name: /Reply style:/ }))
    fireEvent.click(screen.getByText('Conversational'))
    expect(onSelect).toHaveBeenLastCalledWith('conversational')
    fireEvent.click(screen.getByRole('button', { name: /Reply style:/ }))
    fireEvent.click(screen.getByText('Agent default'))
    expect(onSelect).toHaveBeenLastCalledWith('')
  })

  it('the reset row says what the chat then follows: the agent\'s style, or the platform default', () => {
    pill({ agentDefault: 'terse' })
    fireEvent.click(screen.getByRole('button', { name: /Reply style:/ }))
    expect(screen.getByText('This agent asks for Terse')).toBeTruthy()
    cleanup()
    pill({ agentDefault: '' })
    fireEvent.click(screen.getByRole('button', { name: /Reply style:/ }))
    expect(screen.getByText('Follows this agent, then the platform default')).toBeTruthy()
  })

  it('says a style changes style only', () => {
    pill()
    fireEvent.click(screen.getByRole('button', { name: /Reply style:/ }))
    expect(screen.getByText(/Changes style only/)).toBeTruthy()
  })
})

describe('the picker does not re-derive the resolution order', () => {
  const SRC = readFileSync(join(process.cwd(), 'src/ui/composer/controls.tsx'), 'utf8')

  it('the label reads the resolved style and source, never the agent default', () => {
    const at = SRC.indexOf('export function ReplyStylePill')
    expect(at, 'the component moved, so this rail measures nothing').toBeGreaterThan(-1)
    const body = SRC.slice(at, SRC.indexOf('\n}\n', at))
    const from = body.indexOf('const label =')
    expect(from, 'the label expression moved, so this rail measures nothing').toBeGreaterThan(-1)
    const labelExpr = body.slice(from, body.indexOf('return (', from))
    expect(labelExpr.length, 'empty slice, vacuous').toBeGreaterThan(20)
    expect(labelExpr, 'the label must come from the backend, not from agentDefault').not.toMatch(/agentDefault/)
    expect(labelExpr).toMatch(/effective/)
    expect(labelExpr).toMatch(/source/)
  })

  it('the composer renders it whenever the host supplies it', () => {
    const COMPOSER = readFileSync(join(process.cwd(), 'src/ui/Composer.tsx'), 'utf8')
    expect(COMPOSER).toMatch(/\{replyStyle && <ReplyStylePill \{\.\.\.replyStyle\} \/>\}/)
  })

  it('no natural-voice control is left', () => {
    expect(SRC).not.toMatch(/NaturalVoice/)
  })
})
