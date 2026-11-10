import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ReplyStyleChoices, type ReplyStyleOption } from './ReplyStyleChoices'
import { replyStyleOptions } from '../../pages/agents/AgentForm'
import { replyStylePills } from '../../pages/settings/AgentDefaultsPanel'
import { SegPills } from '../../pages/settings/settingsUI'

// ── Your own and an app's styles in Chat preferences, and a style that is gone ──────────────

afterEach(() => cleanup())

const STYLES: ReplyStyleOption[] = [
  { id: 'default', title: 'Default', description: 'No style instruction.' },
  { id: 'terse', title: 'Terse', description: 'Short and direct.' },
  { id: 'own:formal', title: 'Formal', description: 'Polite and precise.' },
  { id: 'acme:brief', title: 'Brief', description: 'From Acme.', by: 'Acme' },
]

function open(props: Partial<Parameters<typeof ReplyStyleChoices>[0]> = {}) {
  render(<ReplyStyleChoices choice="" effective="default" source="platform" agentDefault="" styles={STYLES} onSelect={vi.fn()} {...props} />)
}

describe('Chat preferences lists every available style', () => {
  it('names an app’s style with the app that adds it', () => {
    open()
    expect(screen.getByRole('radio', { name: 'Brief (Acme)' })).toBeTruthy()
    expect(screen.getByRole('radio', { name: 'Formal' })).toBeTruthy()
  })

  it('"Now:" names an app’s style with its app too', () => {
    open({ choice: 'acme:brief', effective: 'acme:brief', source: 'conversation' })
    expect(screen.getByText(/^Now:/).textContent).toBe('Now: Brief (Acme), for this chat')
  })

  it('a chosen style that is gone is named as no longer available, stays checked, and cannot be picked', () => {
    const onSelect = vi.fn()
    open({ choice: 'acme:gone', effective: 'terse', source: 'agent', agentDefault: 'terse', onSelect })
    const row = screen.getByRole('radio', { name: 'acme:gone — no longer available' })
    expect(row.getAttribute('aria-checked')).toBe('true')
    expect(row.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(row)
    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe('the agent form offers what the server lists', () => {
  it('Platform default first, then every available style, an app’s with its app', () => {
    expect(replyStyleOptions(STYLES, '')).toEqual([
      { value: '', label: 'Platform default' },
      { value: 'default', label: 'No style' },
      { value: 'terse', label: 'Terse' },
      { value: 'own:formal', label: 'Formal' },
      { value: 'acme:brief', label: 'Brief (Acme)' },
    ])
  })

  it('keeps a stored style that is gone, marked, so saving does not silently clear it, and it cannot be picked', () => {
    const opts = replyStyleOptions(STYLES, 'acme:gone')
    const gone = opts.find((o) => o.value === 'acme:gone')
    expect(gone).toMatchObject({ label: 'acme:gone (no longer available)', disabled: true })
    expect(opts.filter((o) => o.disabled)).toEqual([gone])
  })

  it('before the list arrives it offers the bundled styles', () => {
    expect(replyStyleOptions([], '').map((o) => o.value)).toEqual(['', 'default', 'plain', 'terse', 'conversational'])
  })
})

describe('the Agent defaults row offers what the server lists', () => {
  const LISTED = [{ key: 'default', label: 'No style' }, { key: 'terse', label: 'Terse' }]

  it('shows a stored style that is gone, marked, and it cannot be picked', () => {
    const pills = replyStylePills(LISTED, 'acme:gone')
    expect(pills.find((p) => p.key === 'acme:gone')).toMatchObject({ label: 'acme:gone (no longer available)', disabled: true })
    expect(pills.filter((p) => p.disabled).map((p) => p.key)).toEqual(['acme:gone'])
    const onChange = vi.fn()
    render(<SegPills ariaLabel="Reply style" value="acme:gone" onChange={onChange} options={pills} />)
    const gone = screen.getByRole('button', { name: 'Reply style: acme:gone (no longer available)' })
    expect((gone as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(gone)
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Reply style: Terse' }))
    expect(onChange).toHaveBeenCalledWith('terse')
  })

  it('adds nothing while the list has not arrived, or when the stored style is listed', () => {
    expect(replyStylePills(null, 'acme:gone')).toEqual(replyStylePills(null, 'default'))
    expect(replyStylePills(LISTED, 'terse')).toEqual(LISTED)
  })
})
