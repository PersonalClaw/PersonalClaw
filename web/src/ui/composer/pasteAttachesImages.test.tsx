import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, waitFor } from '@testing-library/react'

// ── A pasted screenshot is an attachment ──────────────────────────────────────────────────────
//
// The composer's paste handler read `text/plain` and nothing else, so ⌘V on a screenshot did
// nothing at all: no chip, no upload, no error. A screenshot on the clipboard is an image FILE
// item with no text beside it; it now goes through `onAttach`, the path a picked or dropped file
// takes. A paste that carries text as well — cells copied from a spreadsheet put a picture of the
// cells next to their text — is still a text paste.
//
// jsdom has no DataTransfer, so the event carries the one shape CodeMirror's paste path reads.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

import { Composer } from '../Composer'
import { pastedImages } from './MarkdownInput'

function pasteEvent({ text = '', files = [] as File[] }): Event {
  const ev = new Event('paste', { bubbles: true, cancelable: true })
  const items = files.map((f) => ({ kind: 'file', type: f.type, getAsFile: () => f }))
  Object.defineProperty(ev, 'clipboardData', {
    value: { getData: (t: string) => (t === 'text/plain' ? text : ''), items, files, types: text ? ['text/plain', 'Files'] : ['Files'] },
  })
  return ev
}

function mount(onAttach?: (files: File[]) => void, onChange = vi.fn()) {
  const utils = render(
    <Composer value="" onChange={onChange} onSend={() => {}} onAttach={onAttach}
      controls={{ agent: false, model: false, approval: false, reasoning: false, attach: true, mic: false, optimize: false }} />,
  )
  const editor = utils.container.querySelector('.cm-content') as HTMLElement
  expect(editor, 'the composer mounted its editor').toBeTruthy()
  return { ...utils, editor, onChange }
}

const shot = () => new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'image.png', { type: 'image/png' })

afterEach(() => cleanup())

describe('pasting into the composer', () => {
  it('attaches a pasted screenshot through the same path as a picked file', async () => {
    const onAttach = vi.fn()
    const { editor, onChange } = mount(onAttach)
    const file = shot()
    const ev = pasteEvent({ files: [file] })
    editor.dispatchEvent(ev)
    await waitFor(() => expect(onAttach).toHaveBeenCalledTimes(1))
    expect(onAttach.mock.calls[0][0]).toEqual([file])
    expect(ev.defaultPrevented, 'the image paste was consumed, not left to the editor').toBe(true)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('keeps a paste that carries text a text paste, even with an image beside it', () => {
    const onAttach = vi.fn()
    const { editor } = mount(onAttach)
    editor.dispatchEvent(pasteEvent({ text: 'A1\tB1', files: [shot()] }))
    expect(onAttach).not.toHaveBeenCalled()
  })

  it('inserts nothing for a pasted image where nothing can take an attachment', () => {
    const { editor, onChange } = mount(undefined)
    editor.dispatchEvent(pasteEvent({ files: [shot()] }))
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('pastedImages', () => {
  it('takes image file items only, in clipboard order', () => {
    const a = shot()
    const b = new File(['x'], 'b.jpg', { type: 'image/jpeg' })
    const doc = new File(['x'], 'c.pdf', { type: 'application/pdf' })
    const data = {
      items: [
        { kind: 'file', type: a.type, getAsFile: () => a },
        { kind: 'string', type: 'text/html', getAsFile: () => null },
        { kind: 'file', type: doc.type, getAsFile: () => doc },
        { kind: 'file', type: b.type, getAsFile: () => b },
      ],
    } as unknown as DataTransfer
    expect(pastedImages(data)).toEqual([a, b])
    expect(pastedImages(null)).toEqual([])
  })
})
