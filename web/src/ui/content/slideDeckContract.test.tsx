/** DFE-8 — the deck editor's contract at its own surface.
 *
 *  Four things are asserted here that the pure-helper suite cannot reach, because they are
 *  about what the COMPONENT does rather than what a function returns:
 *
 *    1. a `.pptx` type mounts THIS editor, not the block editor — the registration is the
 *       call site, and an editor nobody routes to is a page with a green suite;
 *    2. the lossy-edit gate is a MECHANISM: while a loss report is unacknowledged every
 *       field is disabled and carries a reason, so it cannot be typed past;
 *    3. the save PAYLOAD keeps a bullet's DEPTH and carries the loaded version as
 *       `If-Match` — otherwise the backend's read-back proof is testing a deck the browser
 *       never sends;
 *    4. the depth is reachable as a FIELD, and changing it changes what is saved.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DeckModelJson, DocumentLossReport } from '../../lib/api'

const confirmSpy = vi.fn<(opts: unknown) => Promise<boolean>>()
vi.mock('../dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const artifactDeckModel = vi.fn()
const saveArtifactDeckModel = vi.fn()
vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      artifactDeckModel: (s: string) => artifactDeckModel(s),
      saveArtifactDeckModel: (...a: unknown[]) => saveArtifactDeckModel(...a),
    },
  }
})

const { SlideDeck } = await import('./SlideDeck')
const { ApiError } = await import('../../lib/api')
const { inheritedBox } = await import('./deckModelEdit')

const MODEL: DeckModelJson = {
  title: 'Quarterly Review',
  width_in: 0,
  height_in: 0,
  slides: [
    {
      title: 'Pipeline',
      bullets: [{ text: 'Enterprise', level: 0 }, { text: 'Two renewals at risk', level: 1 }],
      notes: 'lead with the renewals',
      artifact_slug: '',
      layout: 'Title and Content',
      title_box: inheritedBox(),
      body_box: inheritedBox(),
    },
  ],
}

const LOSSLESS: DocumentLossReport = { lossless: true, kinds: [], summary: 'no losses', items: [] }
const LOSSY: DocumentLossReport = {
  lossless: false,
  kinds: ['slide_shape'],
  summary: 'slide_shape×1',
  items: [{ kind: 'slide_shape', detail: 'TextBox 3 (TEXT_BOX (17)) is not carried by the model', where: 'slide 1', block_index: -1, paragraph_ordinal: -1 }],
}

const clone = (m: DeckModelJson): DeckModelJson => JSON.parse(JSON.stringify(m))

function load(loss: DocumentLossReport = LOSSLESS, model: DeckModelJson = MODEL) {
  artifactDeckModel.mockResolvedValue({ slug: 'review', kind: 'pptx', version: 4, mime: 'x', model: clone(model), loss })
}

beforeEach(() => {
  vi.clearAllMocks()
  confirmSpy.mockResolvedValue(true)
  saveArtifactDeckModel.mockResolvedValue({ slug: 'review', version: 5, mime: 'x' })
})

// ── clause 1: the registration — a .pptx gets the SLIDE editor ───────────────

describe('the pptx content type mounts the deck editor', () => {
  it('registers SlideDeck for pptx, and not for the other two office types', async () => {
    const { registerBuiltinContentTypes } = await import('./registerBuiltins')
    const { getContentType } = await import('./contentTypes')
    const { setDocumentEditing } = await import('./documentEditing')
    const { DocumentEditor } = await import('./DocumentEditor')
    const { SheetGrid } = await import('./SheetGrid')

    registerBuiltinContentTypes()
    setDocumentEditing(true)

    expect(getContentType('pptx')?.edit?.render).toBe(SlideDeck)
    // The vacuity leg: the table has to be capable of a DIFFERENT answer, or "pptx gets
    // the slide editor" would hold for a registration that gave every type the same one.
    expect(getContentType('docx')?.edit?.render).toBe(DocumentEditor)
    expect(getContentType('xlsx')?.edit?.render).toBe(SheetGrid)
  })
})

// ── clause 2: the lossy-edit gate is a mechanism ──────────────────────────────

describe('the lossy-edit gate', () => {
  it('disables every field and says why until it is acknowledged', async () => {
    load(LOSSY)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    await screen.findByRole('alert')

    const bullet = await screen.findByLabelText('Bullet 1 on slide 1')
    expect(bullet).toBeDisabled()
    // The reason is the only thing a keyboard user can reach on a natively disabled
    // control — a gate that merely dims is a notice.
    expect(bullet).toHaveAttribute('title', expect.stringContaining('edit anyway'))
    expect(await screen.findByLabelText('Deck title')).toBeDisabled()
    expect(await screen.findByLabelText('Indent level of bullet 1 on slide 1')).toBeDisabled()
    expect(await screen.findByLabelText('Speaker notes for slide 1')).toBeDisabled()
    // …and the report names what would be lost, rather than saying "some formatting".
    expect(screen.getByText(/TextBox 3/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /edit anyway/i }))
    expect(await screen.findByLabelText('Bullet 1 on slide 1')).not.toBeDisabled()
  })

  it('needs no acknowledgement for a deck the model can hold fully', async () => {
    load(LOSSLESS)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    expect(await screen.findByLabelText('Bullet 1 on slide 1')).not.toBeDisabled()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('blocks a read-only version with its own reason', async () => {
    load(LOSSLESS)
    render(<SlideDeck slug="review" title="Review" mode="light" readOnly />)
    const bullet = await screen.findByLabelText('Bullet 1 on slide 1')
    expect(bullet).toBeDisabled()
    expect(bullet).toHaveAttribute('title', expect.stringContaining('read-only'))
  })
})

// ── clauses 3 and 4: depth is a field, and the save carries it ───────────────

describe('the save payload', () => {
  it('sends the edited depth and the loaded version', async () => {
    load(LOSSLESS)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    const level = await screen.findByLabelText('Indent level of bullet 2 on slide 1')
    // The depth is a SELECT, not a typing behaviour: no tab-to-indent, no dashes parsed
    // out of prose. That is the whole posture of a structural editor.
    expect((level as HTMLSelectElement).value).toBe('1')
    await userEvent.selectOptions(level, '3')

    await userEvent.type(await screen.findByLabelText('Bullet 1 on slide 1'), ' pipeline')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(saveArtifactDeckModel).toHaveBeenCalledTimes(1))
    const [slug, version, model] = saveArtifactDeckModel.mock.calls[0] as [string, number, DeckModelJson]
    expect(slug).toBe('review')
    expect(version).toBe(4)
    expect(model.slides[0].bullets).toEqual([
      { text: 'Enterprise pipeline', level: 0 },
      { text: 'Two renewals at risk', level: 3 },
    ])
    // The notes the editor never asked about are still in the payload — a save must not
    // drop what it did not show.
    expect(model.slides[0].notes).toBe('lead with the renewals')
    expect(model.title).toBe('Quarterly Review')
  })

  it('confirms before re-rendering a lossy deck, and does not save when declined', async () => {
    load(LOSSY)
    confirmSpy.mockResolvedValue(false)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    await userEvent.click(await screen.findByRole('button', { name: /edit anyway/i }))
    await userEvent.type(await screen.findByLabelText('Bullet 1 on slide 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    expect(saveArtifactDeckModel).not.toHaveBeenCalled()
  })

  it('explains a 409 as a collision rather than losing the edits', async () => {
    load(LOSSLESS)
    saveArtifactDeckModel.mockRejectedValue(new ApiError('conflict', 409))
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    await userEvent.type(await screen.findByLabelText('Bullet 1 on slide 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    expect(await screen.findByText(/changed somewhere else/i)).toBeInTheDocument()
    // The edit is still on screen: a collision must not discard what the user typed.
    expect((await screen.findByLabelText('Bullet 1 on slide 1')).getAttribute('value')).toContain('!')
  })
})

// ── the surfaces a deck can arrive in ────────────────────────────────────────

describe('a deck with no slides', () => {
  it('says so and offers to add one rather than rendering an empty frame', async () => {
    load(LOSSLESS, { title: '', slides: [], width_in: 0, height_in: 0 })
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    expect(await screen.findByText(/no slides/i)).toBeInTheDocument()
    // Exactly ONE control adds a slide here. The slide-list toolbar is hidden while the
    // deck is empty, because a second identically-named "Add slide" beside this one is an
    // ambiguity a screen-reader user cannot resolve — the a11y suite caught that, and the
    // fix was to delete the duplicate rather than to rename around it.
    expect(screen.getAllByRole('button', { name: /add slide/i })).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: /^add slide$/i }))
    expect(await screen.findByLabelText('Title of slide 1')).toBeInTheDocument()
    // …and once there IS a slide, the toolbar control appears, named for what it does
    // (insert after the current slide) rather than sharing the empty state's name.
    expect(screen.getByRole('button', { name: 'Add slide after this one' })).toBeInTheDocument()
  })
})

describe('a moved shape', () => {
  it('says the shape was moved, and offers the layout’s position back', async () => {
    load(LOSSLESS, {
      ...MODEL,
      slides: [{ ...MODEL.slides[0], title_box: { left_in: 1.25, top_in: 0.5, width_in: 6, height_in: 1.5 } }],
    })
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    expect(await screen.findByText(/moved out of their layout/i)).toBeInTheDocument()
    expect(screen.getByText(/1.25 × 0.5 in from the top-left/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /use the layout’s positions/i }))
    await waitFor(() => expect(screen.queryByText(/moved out of their layout/i)).toBeNull())
    // …and it is a real model change, so it is saveable.
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(saveArtifactDeckModel).toHaveBeenCalledTimes(1))
    const [, , model] = saveArtifactDeckModel.mock.calls[0] as [string, number, DeckModelJson]
    expect(model.slides[0].title_box).toEqual(inheritedBox())
  })
})
