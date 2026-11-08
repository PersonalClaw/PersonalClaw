import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { RenderedSource } from './RenderedSource'
import { HOSTILE, inert } from '../test/markdownSinks'

// ── An authored source reads rendered, and its raw source is a press away ─────────────────────

const SOURCE = '## Greeting\n\nHello, **{{bot_name}}**. Run `make test` first.'

describe('RenderedSource', () => {
  it('renders the source by default', () => {
    const { container } = render(<RenderedSource source={SOURCE} />)
    expect(container.querySelector('h2')?.textContent).toBe('Greeting')
    expect(container.querySelector('strong')?.textContent).toBe('{{bot_name}}')
    expect(container.querySelector('pre')).toBeNull()
    expect(screen.getByRole('button', { name: /Raw/ })).toHaveAttribute('title', 'Show raw template')
  })

  it('Raw shows the source as written, and Rendered goes back', () => {
    const { container } = render(<RenderedSource source={SOURCE} />)
    fireEvent.click(screen.getByRole('button', { name: /Raw/ }))
    expect(container.querySelector('pre')?.textContent).toBe(SOURCE)
    expect(container.querySelector('strong')).toBeNull()
    const back = screen.getByRole('button', { name: /Rendered/ })
    expect(back).toHaveAttribute('title', 'Show rendered')
    fireEvent.click(back)
    expect(container.querySelector('strong')).not.toBeNull()
  })

  it('the rendered view reads the transform; the raw view never does', () => {
    const { container } = render(<RenderedSource source={SOURCE} transform={(s) => s.replace('{{bot_name}}', 'PersonalClaw')} />)
    expect(container.querySelector('strong')?.textContent).toBe('PersonalClaw')
    fireEvent.click(screen.getByRole('button', { name: /Raw/ }))
    expect(container.querySelector('pre')?.textContent).toContain('{{bot_name}}')
  })

  it('takes its own caption, and nothing shows a dash', () => {
    render(<RenderedSource source="" label="Instructions" />)
    expect(screen.getByText('Instructions')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('a hostile source is inert in both views: rendered as a body, and raw as its text', async () => {
    const { container } = render(<RenderedSource source={HOSTILE} />)
    await act(async () => {})
    expect(inert(container, { body: true })).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: /Raw/ }))
    expect(container.querySelector('pre')?.textContent).toBe(HOSTILE)
    expect(inert(container, { body: true })).toEqual([])
  })
})
