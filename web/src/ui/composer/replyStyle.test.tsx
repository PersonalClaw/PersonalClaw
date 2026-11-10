import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ReplyStyleChoices, type ReplyStyleOption } from './ReplyStyleChoices'

// ── Chat preferences' Reply style shows what actually takes effect, and never resolves it ──────
//
// A style can be stated by the conversation (this section), the bound agent, or the platform
// default. The order between them is stated ONCE, in Python (`reply_style.REPLY_STYLE_PRECEDENCE`),
// and the backend hands this component the resolved `effective` style and its `source`.
//
// 🪤 THE FAILURE THIS RAIL EXISTS FOR: working the effect out here from `choice` and the agent's
// own style. That is a second copy of the order, free to drift from the one the turn uses, and it
// lets the section name a style the turn did not carry.

const STYLES: ReplyStyleOption[] = [
  { id: 'default', title: 'Default', description: 'No style instruction.' },
  { id: 'plain', title: 'Plain', description: 'Plainer prose.' },
  { id: 'terse', title: 'Terse', description: 'Short and direct.' },
  { id: 'conversational', title: 'Conversational', description: 'Fuller and warmer.' },
]

function section(props: Partial<Parameters<typeof ReplyStyleChoices>[0]> = {}) {
  const onSelect = vi.fn()
  render(<ReplyStyleChoices choice="" effective="default" source="platform" agentDefault="" styles={STYLES} onSelect={onSelect} {...props} />)
  return onSelect
}

const now = () => screen.getByText(/^Now:/).textContent

describe('the Reply style section shows what actually takes effect', () => {
  it('names the conversation’s own style', () => {
    section({ choice: 'terse', effective: 'terse', source: 'conversation' })
    expect(now()).toBe('Now: Terse, for this chat')
  })

  it('names the AGENT when the agent is what chose it', () => {
    section({ effective: 'conversational', source: 'agent', agentDefault: 'conversational' })
    expect(now()).toBe('Now: Conversational, from the agent')
  })

  it('names your default when that is what applies', () => {
    section({ effective: 'plain', source: 'platform' })
    expect(now()).toBe('Now: Plain, your default')
  })

  it('reads No style when no style applies', () => {
    section({ effective: 'default', source: 'platform' })
    expect(now()).toBe('Now: No style, your default')
  })

  it('a conversation’s No style over an agent’s style reads No style: the agent does not win', () => {
    section({ choice: 'default', effective: 'default', source: 'conversation', agentDefault: 'terse' })
    expect(now()).toBe('Now: No style, for this chat')
  })

  it('before a conversation exists it shows the CHOICE, never a guessed effect', () => {
    section({ choice: '', source: '', agentDefault: 'terse' })
    expect(now()).toBe('Now: Agent default')
  })

  it('and a pre-session pick reads back as that pick', () => {
    section({ choice: 'conversational', source: '' })
    expect(now()).toBe('Now: Conversational')
  })

  it('lists every style with its one-line description, plus the reset row', () => {
    section()
    expect(screen.getByRole('radiogroup', { name: 'Reply style' })).toBeTruthy()
    for (const s of STYLES) {
      const row = screen.getByRole('radio', { name: s.title === 'Default' ? 'No style' : s.title })
      expect(document.getElementById(row.getAttribute('aria-describedby') ?? '')?.textContent).toBe(s.description)
    }
    expect(screen.getByRole('radio', { name: 'Agent default' }).getAttribute('aria-checked')).toBe('true')
  })

  it('a pick sends the style id, and the reset row sends ""', () => {
    const onSelect = section({ choice: 'terse', effective: 'terse', source: 'conversation' })
    fireEvent.click(screen.getByRole('radio', { name: 'Conversational' }))
    expect(onSelect).toHaveBeenLastCalledWith('conversational')
    fireEvent.click(screen.getByRole('radio', { name: 'Agent default' }))
    expect(onSelect).toHaveBeenLastCalledWith('')
  })

  it('the reset row says what the chat then follows: the agent\'s style, or the platform default', () => {
    section({ agentDefault: 'terse' })
    expect(screen.getByText('This agent asks for Terse.')).toBeTruthy()
    cleanup()
    section({ agentDefault: '' })
    expect(screen.getByText('Follows this agent, then the platform default.')).toBeTruthy()
  })

  it('says a style changes style only, and a choice here leaves the agent as it is, word for word', () => {
    section()
    expect(screen.getByText('Changes style only, never a fact, a caveat or a refusal. A choice here applies to this conversation and does not edit the agent.')).toBeTruthy()
  })
})

describe('the section does not re-derive the resolution order', () => {
  const SRC = readFileSync(join(process.cwd(), 'src/ui/composer/ReplyStyleChoices.tsx'), 'utf8')

  it('"Now:" reads the resolved style and source, never the agent default', () => {
    const at = SRC.indexOf('export function ReplyStyleChoices')
    expect(at, 'the component moved, so this rail measures nothing').toBeGreaterThan(-1)
    const body = SRC.slice(at, SRC.indexOf('\n}\n', at))
    const from = body.indexOf('const now =')
    expect(from, 'the "Now:" expression moved, so this rail measures nothing').toBeGreaterThan(-1)
    const nowExpr = body.slice(from, body.indexOf('const options', from))
    expect(nowExpr.length, 'empty slice, vacuous').toBeGreaterThan(20)
    expect(nowExpr, '"Now:" must come from the backend, not from agentDefault').not.toMatch(/agentDefault/)
    expect(nowExpr).toMatch(/effective/)
    expect(nowExpr).toMatch(/source/)
  })

  it('the shared composer draws no reply-style control: Chat preferences holds it', () => {
    const COMPOSER = readFileSync(join(process.cwd(), 'src/ui/Composer.tsx'), 'utf8')
    expect(COMPOSER).not.toMatch(/ReplyStyle|replyStyle/)
    const PREFS = readFileSync(join(process.cwd(), 'src/pages/chat/composer/ChatPreferences.tsx'), 'utf8')
    expect(PREFS).toMatch(/<ReplyStyleChoices \{\.\.\.replyStyle\}/)
  })

  it('no natural-voice control is left', () => {
    expect(SRC).not.toMatch(/NaturalVoice/)
  })
})
