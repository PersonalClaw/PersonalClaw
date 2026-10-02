import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { act, render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'

// ── An attached image says how it reaches the model ───────────────────────────────────────────
//
// An image goes to a model that takes images AS the image, and to any other model as the text
// read from it. The chip above the composer says which, before sending — from the same record
// the turn decides by (`GET /api/chat/image-input`), with the server's sentence for why. The chip
// on a sent turn says what happened, from the user message's `meta.image_delivery`.

const h = vi.hoisted(() => ({
  imageInput: vi.fn(),
  attachmentExtract: vi.fn(),
  // The tab's socket: the gateway says a reading finished with an `attachments` refresh hint.
  hears: [] as Array<(m: { type: string; data: Record<string, unknown> }) => void>,
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => {
        if (key === 'chatImageInput') return h.imageInput
        if (key === 'attachmentExtract') return h.attachmentExtract
        return (target as Record<string | symbol, unknown>)[key]
      },
    }),
  }
})

vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<typeof import('../../lib/useChatSocket')>()),
  useChatSocket: (onMessage: (m: { type: string; data: Record<string, unknown> }) => void) => { h.hears.push(onMessage) },
}))

import { AttachmentChips, TurnAttachments } from './AttachmentChips'
import { hydrateTurns, type HistMsg } from './chatTypes'
import { isImagePath, imagesAsTextNote } from './imageAttachments'

const IMG = '/home/uploads/' + 'a'.repeat(32) + '_shot.png'
const DOC = '/home/uploads/' + 'b'.repeat(32) + '_notes.md'

function chips(session = 's1', key = Math.random().toString(36)) {
  return render(
    <AttachmentChips paths={[DOC, IMG]} images={[IMG]} session={session} agent={key} model="" runtime=""
      onRemove={() => {}} onOpen={() => {}} />,
  )
}

beforeEach(() => { h.imageInput.mockReset(); h.attachmentExtract.mockReset(); h.hears.length = 0 })

/** The gateway's word that an attachment's reading finished, to every listener on the page. */
const readingFinished = () => act(() => { for (const hear of [...h.hears]) hear({ type: 'refresh', data: { kinds: ['attachments'] } }) })
afterEach(() => cleanup())

describe('the composer chip for an attached image', () => {
  it('says the image goes as text, and why, when the model takes no images', async () => {
    h.imageInput.mockResolvedValue({ accepted: false, reason: "gemma3:1b can't take images.", model: 'gemma3:1b' })
    h.attachmentExtract.mockResolvedValue({ name: 'shot.png', text: 'INVOICE 42', read: true })
    chips()
    await waitFor(() => expect(screen.getByRole('note').textContent).toBe("gemma3:1b can't take images. It gets the text read from the image instead."))
    expect(screen.getByText('as text')).toBeTruthy()
    expect(h.attachmentExtract).toHaveBeenCalledWith(IMG)
    expect(h.imageInput).toHaveBeenCalledWith('s1', expect.objectContaining({ model: '' }))
  })

  it("says only the size and format go when nothing could read the image — never 'text read from'", async () => {
    h.imageInput.mockResolvedValue({ accepted: false, reason: "gemma3:1b can't take images.", model: 'gemma3:1b' })
    h.attachmentExtract.mockResolvedValue({ name: 'shot.png', text: 'Image: shot.png (240×160, PNG, 1 KB) — no extractable text content.', read: false, unread: '' })
    chips()
    await waitFor(() => expect(screen.getByRole('note').textContent)
      .toBe("gemma3:1b can't take images. Nothing could read the image, so it gets only the image's size and format."))
    // An image model read it and found nothing: "set one up" would say none is.
    expect(screen.queryByRole('link', { name: 'Settings → Models' })).toBeNull()
  })

  it('says no image model is set up, and links to Settings → Models, when nothing can read images', async () => {
    h.imageInput.mockResolvedValue({ accepted: false, reason: "gemma3:1b can't take images.", model: 'gemma3:1b' })
    h.attachmentExtract.mockResolvedValue({ name: 'shot.png', text: 'Image: shot.png (240×160, PNG, 1 KB) — not read: no image model is set up.', read: false, unread: 'no_image_model' })
    chips()
    await waitFor(() => expect(screen.getByRole('note').textContent).toBe(
      "gemma3:1b can't take images. No image model is set up, so it gets only the image's size and format. Choose one in Settings → Models.",
    ))
    expect(screen.getByRole('link', { name: 'Settings → Models' }).getAttribute('href')).toBe('#/settings/models')
    expect(screen.getByText('as text')).toBeTruthy()
  })

  it('claims only the reason while the image is still being read', async () => {
    h.imageInput.mockResolvedValue({ accepted: false, reason: "gemma3:1b can't take images.", model: 'gemma3:1b' })
    h.attachmentExtract.mockReturnValue(new Promise(() => {}))
    chips()
    await waitFor(() => expect(screen.getByRole('note').textContent).toBe("gemma3:1b can't take images."))
  })

  it('waits while an image model is still reading, and says what it read once it has', async () => {
    h.imageInput.mockResolvedValue({ accepted: false, reason: "gemma3:1b can't take images.", model: 'gemma3:1b' })
    h.attachmentExtract.mockResolvedValueOnce({ name: 'shot.png', pending: true, text: '', read: false, unread: '' })
    chips()
    await waitFor(() => expect(screen.getByRole('note').textContent).toBe("gemma3:1b can't take images."))
    h.attachmentExtract.mockResolvedValueOnce({ name: 'shot.png', pending: false, text: 'INVOICE 42', read: true, unread: '' })
    readingFinished()
    await waitFor(() => expect(screen.getByRole('note').textContent).toBe(
      "gemma3:1b can't take images. It gets the text read from the image instead."))
    expect(h.attachmentExtract).toHaveBeenCalledTimes(2)
  })

  it('makes no claim when the model takes images', async () => {
    h.imageInput.mockResolvedValue({ accepted: true, reason: '', model: 'gemma4:12b' })
    chips()
    await waitFor(() => expect(h.imageInput).toHaveBeenCalled())
    expect(screen.queryByRole('note')).toBeNull()
    expect(screen.queryByText('as text')).toBeNull()
    expect(h.attachmentExtract, 'an image shown as pixels needs no text read from it').not.toHaveBeenCalled()
  })

  it('says so when the question itself fails, rather than making a claim', async () => {
    h.imageInput.mockRejectedValue(new Error('gateway unreachable'))
    chips()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe("Couldn't check how images reach this chat's model — gateway unreachable")
    expect(screen.queryByText('as text')).toBeNull()
  })

  it('asks nothing when no image is attached', () => {
    render(<AttachmentChips paths={[DOC]} images={[]} session="s1" agent="" model="" runtime="" onRemove={() => {}} onOpen={() => {}} />)
    expect(h.imageInput).not.toHaveBeenCalled()
  })

  it('asks about the ACP runtime a picked agent runs on', async () => {
    h.imageInput.mockResolvedValue({ accepted: false, reason: "claude-code can't be handed an image.", model: '' })
    h.attachmentExtract.mockResolvedValue({ name: 'shot.png', text: 'x', read: true })
    render(<AttachmentChips paths={[IMG]} images={[IMG]} session="" agent="Claude" model="" runtime="acp:claude-code" onRemove={() => {}} onOpen={() => {}} />)
    await screen.findByRole('note')
    expect(h.imageInput).toHaveBeenCalledWith('', { runtime: 'acp:claude-code', agent: 'Claude' })
  })
})

describe('the chip on a sent turn', () => {
  it('says an image went as text, and the preview says why', async () => {
    h.attachmentExtract.mockResolvedValue({ name: 'shot.png', text: 'TEXT READ FROM IT', read: true })
    render(<TurnAttachments paths={[IMG]} delivery={{ byPath: { [IMG]: 'text' }, reason: "gemma3:1b can't take images." }} onOpenFile={() => {}} />)
    const chip = screen.getByRole('button', { name: /shot\.png/ })
    expect(chip.textContent).toContain('sent as text')
    fireEvent.click(chip)
    expect(await screen.findByText("gemma3:1b can't take images. The text read from the image was sent instead.")).toBeTruthy()
    expect(await screen.findByText('TEXT READ FROM IT')).toBeTruthy()
  })

  it('says no image model is set up, and links to Settings → Models, when nothing read the image', async () => {
    h.attachmentExtract.mockResolvedValue({ name: 'shot.png', text: 'Image: shot.png (240×160, PNG, 1 KB) — not read: no image model is set up.', read: false, unread: 'no_image_model' })
    render(<TurnAttachments paths={[IMG]} delivery={{ byPath: { [IMG]: 'text' }, reason: "gemma3:1b can't take images." }} onOpenFile={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /shot\.png/ }))
    const link = await screen.findByRole('link', { name: 'Settings → Models' })
    expect(link.getAttribute('href')).toBe('#/settings/models')
    expect(link.closest('p')?.textContent).toBe(
      "gemma3:1b can't take images. No image model is set up, so only its size and format were sent. Choose one in Settings → Models.",
    )
  })

  it('shows the reading in progress, and the text once it lands', async () => {
    h.attachmentExtract
      .mockResolvedValueOnce({ name: 'notes.md', pending: true, text: '', read: false, unread: '' })
      .mockResolvedValueOnce({ name: 'notes.md', pending: false, text: 'Ship the beta on Friday.', read: true, unread: '' })
    render(<TurnAttachments paths={[DOC]} onOpenFile={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /notes\.md/ }))
    expect(await screen.findByText('Extracting…')).toBeTruthy()
    readingFinished()
    expect(await screen.findByText('Ship the beta on Friday.')).toBeTruthy()
    expect(h.attachmentExtract).toHaveBeenCalledTimes(2)
  })

  it('leaves no read out once the preview is gone', () => {
    const signals: Array<AbortSignal | undefined> = []
    h.attachmentExtract.mockImplementation((_path: string, opts: { signal?: AbortSignal } = {}) => {
      signals.push(opts.signal)
      return new Promise(() => {})
    })
    render(<TurnAttachments paths={[DOC]} onOpenFile={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /notes\.md/ }))
    expect(signals).toHaveLength(1)
    expect(signals[0]?.aborted).toBe(false)
    cleanup()
    expect(signals[0]?.aborted, 'a closed preview still holds a connection').toBe(true)
  })

  it('says a failed read failed, not that the file has no text', async () => {
    h.attachmentExtract.mockRejectedValue(new Error('upload is gone'))
    render(<TurnAttachments paths={[DOC]} onOpenFile={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /notes\.md/ }))
    expect((await screen.findByRole('alert')).textContent).toBe("Couldn't read this file's text — upload is gone")
    expect(screen.queryByText(/No extractable text content/)).toBeNull()
  })

  it('does not present extracted text as what the model saw when it was shown the image', async () => {
    render(<TurnAttachments paths={[IMG]} delivery={{ byPath: { [IMG]: 'image' } }} onOpenFile={() => {}} />)
    const chip = screen.getByRole('button', { name: /shot\.png/ })
    expect(chip.textContent).not.toContain('sent as text')
    fireEvent.click(chip)
    expect(await screen.findByText('The model was shown this image itself.')).toBeTruthy()
    expect(screen.queryByText(/what the agent saw/)).toBeNull()
    expect(h.attachmentExtract).not.toHaveBeenCalled()
  })

  it('reads the delivery back from the persisted user message on reload', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'what is this?', ts: 't1', meta: { files: [IMG], image_delivery: { [IMG]: 'text' }, image_delivery_reason: "gemma3:1b can't take images." } },
      { role: 'assistant', content: 'a chart', ts: 't2' },
    ]
    const [user] = hydrateTurns(msgs, false)
    expect(user.imageDelivery).toEqual({ byPath: { [IMG]: 'text' }, reason: "gemma3:1b can't take images." })
  })
})

describe('the image helpers', () => {
  it('sorts on the server allowlist of image extensions', () => {
    expect(isImagePath('/x/' + 'a'.repeat(32) + '_Shot.PNG')).toBe(true)
    expect(isImagePath('/x/photo.webp')).toBe(true)
    expect(isImagePath('/x/diagram.svg')).toBe(false)
    expect(isImagePath('/x/notes.md')).toBe(false)
  })

  it('names the images in the plural', () => {
    const input = { accepted: false, reason: "m can't take images.", model: 'm' }
    expect(imagesAsTextNote(input, 2, { read: true, noImageModel: false }))
      .toBe("m can't take images. It gets the text read from the images instead.")
    expect(imagesAsTextNote(input, 2, { read: false, noImageModel: true }))
      .toBe("m can't take images. No image model is set up, so it gets only each image's size and format.")
    expect(imagesAsTextNote(undefined, 1, { read: true, noImageModel: false })).toBeNull()
  })
})
