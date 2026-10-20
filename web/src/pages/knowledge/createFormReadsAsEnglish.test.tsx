import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── The create form names its type in English, and keeps the title she kept ──────────────────
//
// Measured on the audio form: "Choose a audio file" and "Drop a audio file, or choose one" — the
// article was written before a word the template did not know — and a PDF read "New pdf" /
// "Add pdf", an acronym lowercased. And an audio memo whose title field she left as its file
// name was renamed by enrichment: the form sent no title when it equalled the file name, so the
// server could not tell a kept name from no name at all.

const uploads = vi.hoisted(() => ({
  update: vi.fn(async () => ({})),
  upload: vi.fn(async () => ({ item_id: 'k-memo' })),
}))

vi.mock('./knowledgeStore', () => ({
  createKnowledge: vi.fn(),
  updateKnowledge: uploads.update,
  uploadKnowledgeFile: uploads.upload,
}))
vi.mock('../../lib/chunkedUpload', () => ({ precheck: async () => '' }))
vi.mock('../../lib/api', async (orig) => {
  const mod = await orig<typeof import('../../lib/api')>()
  return { ...mod, api: { ...mod.api, knowledgeTags: async () => [] } }
})

const { KnowledgeCreatePage } = await import('./KnowledgeCreatePage')

afterEach(() => { cleanup(); uploads.update.mockClear(); uploads.upload.mockClear() })

function open(type: string) {
  const { container } = render(<KnowledgeCreatePage onBack={() => {}} onCreated={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: type }))
  // Found by what it is, not by its label, so these tests hold the title whatever the label says.
  return () => container.querySelector('input[type="file"]') as HTMLInputElement
}

describe('the create form names its type in English', () => {
  it('a word that starts with a vowel sound takes "an"', () => {
    open('Audio')

    expect(screen.getByLabelText('Choose an audio file')).toBeTruthy()
    expect(screen.getByText('Drop an audio file, or choose one')).toBeTruthy()
    expect(screen.getByText(/New audio/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Add audio/ })).toBeTruthy()
  })

  it('an acronym keeps its capitals and is read by its letters', () => {
    open('PDF')

    expect(screen.getByLabelText('Choose a PDF file')).toBeTruthy()
    expect(screen.getByText('Drop a PDF file, or choose one')).toBeTruthy()
    expect(screen.getByText(/New PDF/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Add PDF/ })).toBeTruthy()
  })
})

describe('the title she kept is sent as hers', () => {
  it('a title left as the file name is sent with the upload', async () => {
    const picker = open('Audio')
    const memo = new File(['not really audio'], '2026-09-29-dog-walk-talk.m4a', { type: 'audio/mp4' })

    fireEvent.change(picker(), { target: { files: [memo] } })
    await waitFor(() => expect((screen.getByLabelText('Audio title') as HTMLInputElement).value).toBe(memo.name))
    fireEvent.click(screen.getByRole('button', { name: /^Add/ }))

    await waitFor(() => expect(uploads.upload).toHaveBeenCalled())
    await waitFor(() => expect(uploads.update).toHaveBeenCalledWith('k-memo', { title: memo.name }))
  })

  it('an emptied title sends none, so the item is named from what is in it', async () => {
    const picker = open('Audio')
    const memo = new File(['not really audio'], 'memo.m4a', { type: 'audio/mp4' })

    fireEvent.change(picker(), { target: { files: [memo] } })
    await waitFor(() => expect((screen.getByLabelText('Audio title') as HTMLInputElement).value).toBe(memo.name))
    fireEvent.change(screen.getByLabelText('Audio title'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: /^Add/ }))

    await waitFor(() => expect(uploads.upload).toHaveBeenCalled())
    expect(uploads.update).not.toHaveBeenCalled()
  })
})
