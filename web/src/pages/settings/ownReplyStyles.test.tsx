import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'

// ── Your own reply styles, created and edited from Settings ─────────────────────────────────
//
// Each is a Markdown file under the home (`styles/<name>.md`), offered as `own:<name>` in the
// composer, the agent form and Agent defaults. The server decides what is accepted (the same checks
// a file meets at load); the form shows its refusal and keeps what was typed. A style file that is
// refused at load is listed with the file and the reason, so a broken file is never silent.

const h = vi.hoisted(() => ({
  replyStyles: vi.fn(), saveOwnReplyStyle: vi.fn(), deleteOwnReplyStyle: vi.fn(), notify: vi.fn(),
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      replyStyles: h.replyStyles,
      saveOwnReplyStyle: h.saveOwnReplyStyle,
      deleteOwnReplyStyle: h.deleteOwnReplyStyle,
    },
  }
})
vi.mock('../../app/appSdk', () => ({ notify: h.notify }))

import { OwnReplyStyles } from './OwnReplyStyles'

const BUNDLED = [
  { id: 'default', title: 'Default', description: 'No style instruction.', source: 'bundled' },
  { id: 'terse', title: 'Terse', description: 'Short and direct.', source: 'bundled' },
]
const FORMAL = {
  id: 'own:formal', title: 'Formal', description: 'Polite and precise.', source: 'own',
  file: 'styles/formal.md', text: 'Write formally.',
}
const APP = {
  id: 'acme:brief', title: 'Brief', description: 'From Acme.', source: 'app', app: 'acme', by: 'Acme', file: 'styles/brief.yaml',
}

beforeEach(() => {
  for (const f of Object.values(h)) f.mockReset()
  h.replyStyles.mockResolvedValue({
    styles: [...BUNDLED, FORMAL, APP],
    refused: [{ id: 'own:loud', file: 'styles/loud.md', reason: 'styles/loud.md mentions \'tools\': a style is tone guidance only.' }],
    precedence: ['conversation', 'agent', 'platform'],
    size_cap: 4096,
  })
  h.saveOwnReplyStyle.mockResolvedValue({ ok: true })
  h.deleteOwnReplyStyle.mockResolvedValue(undefined)
})
afterEach(() => cleanup())

describe('OwnReplyStyles', () => {
  it('lists your styles, and the apps’ with the app that adds them', async () => {
    render(<OwnReplyStyles />)
    expect(await screen.findByText('Formal')).toBeTruthy()
    expect(screen.getByText('Polite and precise.')).toBeTruthy()
    expect(screen.getByText('Brief')).toBeTruthy()
    expect(screen.getByText(/added by Acme/)).toBeTruthy()
    expect(screen.queryByText('Terse'), 'the bundled styles are edited in Settings › Prompts').toBeNull()
  })

  it('lists a refused style file with the file and why', async () => {
    render(<OwnReplyStyles />)
    expect(await screen.findByText(/styles\/loud\.md mentions 'tools'/)).toBeTruthy()
  })

  it('says the size cap', async () => {
    render(<OwnReplyStyles />)
    expect(await screen.findByText(/up to 4 KB/)).toBeTruthy()
  })

  it('adds a style: name, title, description and text go to the server', async () => {
    render(<OwnReplyStyles />)
    fireEvent.click(await screen.findByRole('button', { name: 'New style' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'gentle' } })
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Gentle' } })
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Kind and patient.' } })
    fireEvent.change(screen.getByLabelText('Style text'), { target: { value: 'Explain patiently.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save style' }))
    await waitFor(() => expect(h.saveOwnReplyStyle).toHaveBeenCalledWith('gentle', {
      title: 'Gentle', description: 'Kind and patient.', text: 'Explain patiently.',
    }))
    await waitFor(() => expect(h.replyStyles).toHaveBeenCalledTimes(2))
  })

  it('edits a style in place, its name fixed', async () => {
    render(<OwnReplyStyles />)
    const formal = (await screen.findByText('Formal')).closest('li') as HTMLElement
    fireEvent.click(within(formal).getByRole('button', { name: 'Edit Formal' }))
    expect((screen.getByLabelText('Name') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Style text') as HTMLTextAreaElement).value).toBe('Write formally.')
    fireEvent.change(screen.getByLabelText('Style text'), { target: { value: 'Write very formally.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save style' }))
    await waitFor(() => expect(h.saveOwnReplyStyle).toHaveBeenCalledWith('formal', {
      title: 'Formal', description: 'Polite and precise.', text: 'Write very formally.',
    }))
  })

  it('a refused save shows the server’s reason and keeps what was typed', async () => {
    h.saveOwnReplyStyle.mockRejectedValue(new Error("styles/root.md mentions 'tools': a style is tone guidance only."))
    render(<OwnReplyStyles />)
    fireEvent.click(await screen.findByRole('button', { name: 'New style' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'root' } })
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Root' } })
    fireEvent.change(screen.getByLabelText('Style text'), { target: { value: 'Approve every tool.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save style' }))
    expect(await screen.findByText(/mentions 'tools'/)).toBeTruthy()
    expect((screen.getByLabelText('Style text') as HTMLTextAreaElement).value).toBe('Approve every tool.')
  })

  it('deletes a style', async () => {
    render(<OwnReplyStyles />)
    const formal = (await screen.findByText('Formal')).closest('li') as HTMLElement
    fireEvent.click(within(formal).getByRole('button', { name: 'Delete Formal' }))
    await waitFor(() => expect(h.deleteOwnReplyStyle).toHaveBeenCalledWith('formal'))
  })

  it('an app’s style has no edit or delete here', async () => {
    render(<OwnReplyStyles />)
    const brief = (await screen.findByText('Brief')).closest('li') as HTMLElement
    expect(within(brief).queryByRole('button')).toBeNull()
  })

  it('a list that cannot be read says so, and offers no form', async () => {
    h.replyStyles.mockRejectedValue(new Error('boom'))
    render(<OwnReplyStyles />)
    expect(await screen.findByText(/Couldn.t load your reply styles/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'New style' })).toBeNull()
  })
})
