import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type {
  DocumentBlock,
  DocumentLossReport,
  DocumentModelJson,
  DocumentPageSetup,
  DocumentParagraphStyle,
} from '../../lib/api'

// ── DFE-6: the layout controls, the values they show, and the preview ─────────────
//
// The atom's second clause is not "the controls exist" — it is that they **reflect the
// loaded document's real values, not defaults**. That distinction is the whole risk here:
// `PageSetup`'s zero values mean "the writer's template decides", so a size dropdown
// showing "Letter" for a document that named no size writes Letter into it on the first
// save. Every "shows its value" assertion below therefore carries a VACUITY LEG that mounts
// a DIFFERENT document and shows the control reading differently — without it, a hard-coded
// default would satisfy the positive case.
//
// The preview is asserted for two things the atom names: it reflects the configured size
// and margins, and it SAYS it is an approximation. No rasterizer exists (§6), so a preview
// that looked authoritative would be trusted about line breaks it cannot know.

const confirmSpy = vi.fn<(opts: unknown) => Promise<boolean>>()
vi.mock('../dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const artifactModel = vi.fn()
const saveArtifactModel = vi.fn()
vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      artifactModel: (s: string) => artifactModel(s),
      saveArtifactModel: (...a: unknown[]) => saveArtifactModel(...a),
    },
  }
})

const { DocumentEditor } = await import('./DocumentEditor')

const POINTS_PER_CM = 72 / 2.54

const block = (over: Partial<DocumentBlock>): DocumentBlock => ({
  kind: 'paragraph', text: '', level: 1, items: [], rows: [],
  artifact_slug: '', runs: [], cells: [], style: null, ...over,
})

const page = (over: Partial<DocumentPageSetup> = {}): DocumentPageSetup => ({
  size: '', orientation: '',
  margin_top_pt: 0, margin_bottom_pt: 0, margin_left_pt: 0, margin_right_pt: 0,
  header_text: '', footer_text: '', page_numbers: false, ...over,
})

const style = (over: Partial<DocumentParagraphStyle> = {}): DocumentParagraphStyle => ({
  align: '', space_before_pt: 0, space_after_pt: 0, line_spacing: 0,
  indent_left_pt: 0, indent_right_pt: 0, first_line_indent_pt: 0, keep_with_next: false, ...over,
})

const CLEAN: DocumentLossReport = { lossless: true, kinds: [], summary: 'Nothing was lost.', items: [] }

/** A4 landscape with 2cm margins — the atom's own example, as the browser sees it. */
const A4_LANDSCAPE_2CM = page({
  size: 'a4', orientation: 'landscape',
  margin_top_pt: 2 * POINTS_PER_CM, margin_bottom_pt: 2 * POINTS_PER_CM,
  margin_left_pt: 2 * POINTS_PER_CM, margin_right_pt: 2 * POINTS_PER_CM,
  header_text: 'Quarterly Review', footer_text: 'Internal', page_numbers: true,
})

/** A deliberately DIFFERENT document, so every "shows its value" test has a foil. */
const LETTER_PORTRAIT_1IN = page({
  size: 'letter', orientation: 'portrait',
  margin_top_pt: 72, margin_bottom_pt: 72, margin_left_pt: 72, margin_right_pt: 72,
  header_text: 'Draft', footer_text: '', page_numbers: false,
})

function model(over: Partial<DocumentModelJson> = {}): DocumentModelJson {
  return {
    title: 'Fidelity',
    blocks: [block({ kind: 'paragraph', text: 'body', runs: [] })],
    page: null,
    ...over,
  }
}

function mount(over: Partial<DocumentModelJson> = {}, props: { readOnly?: boolean } = {}) {
  artifactModel.mockResolvedValue({
    slug: 'report', kind: 'docx', version: 4, mime: 'x',
    model: structuredClone(model(over)), loss: CLEAN,
  })
  return render(<DocumentEditor slug="report" title="Report" mode="dark" {...props} />)
}

/** Open the page-layout disclosure and return once its controls are mounted. */
async function openPageLayout(over: Partial<DocumentModelJson> = {}) {
  mount(over)
  const toggle = await screen.findByRole('button', { name: /page layout/i })
  await userEvent.click(toggle)
  return screen.findByLabelText('Page size')
}

/** Open the layout disclosure for the first paragraph. */
async function openParagraphLayout(over: Partial<DocumentModelJson> = {}) {
  mount(over)
  // ALL, then the first: a multi-paragraph fixture renders one of these per block, and
  // `findByRole` throws on more than one match.
  const toggles = await screen.findAllByRole('button', { name: /layout for this paragraph/i })
  await userEvent.click(toggles[0])
  return screen.findByLabelText('Paragraph alignment')
}

beforeEach(() => {
  confirmSpy.mockReset()
  confirmSpy.mockResolvedValue(true)
  artifactModel.mockReset()
  saveArtifactModel.mockReset()
  saveArtifactModel.mockResolvedValue({ slug: 'report', version: 5, mime: 'x' })
})

// ── the page controls read the DOCUMENT ──────────────────────────────────────

describe('the page controls reflect the loaded document, not a default', () => {
  it('shows A4 / landscape / 2cm / the real header for an A4 landscape document', async () => {
    const size = await openPageLayout({ page: A4_LANDSCAPE_2CM })

    expect((size as HTMLSelectElement).value).toBe('a4')
    expect((screen.getByLabelText('Orientation') as HTMLSelectElement).value).toBe('landscape')
    for (const edge of ['top', 'bottom', 'left', 'right']) {
      const field = screen.getByLabelText(`${edge} margin in centimetres`) as HTMLInputElement
      expect(field.value).toBe('2')
    }
    expect((screen.getByLabelText('Header text') as HTMLInputElement).value).toBe('Quarterly Review')
    expect((screen.getByLabelText('Footer text') as HTMLInputElement).value).toBe('Internal')
    expect(screen.getByRole('switch', { name: /number the pages/i })).toHaveAttribute('aria-checked', 'true')
  })

  it('VACUITY: a Letter portrait document reads differently in every one of those controls', async () => {
    const size = await openPageLayout({ page: LETTER_PORTRAIT_1IN })

    expect((size as HTMLSelectElement).value).toBe('letter')
    expect((screen.getByLabelText('Orientation') as HTMLSelectElement).value).toBe('portrait')
    // 1in = 2.54cm, so this control cannot be showing the 2 the other document had.
    expect((screen.getByLabelText('left margin in centimetres') as HTMLInputElement).value).toBe('2.54')
    expect((screen.getByLabelText('Header text') as HTMLInputElement).value).toBe('Draft')
    expect((screen.getByLabelText('Footer text') as HTMLInputElement).value).toBe('')
    expect(screen.getByRole('switch', { name: /number the pages/i })).toHaveAttribute('aria-checked', 'false')
  })

  it('a document that named NO page setup shows the unset state, not Letter', async () => {
    // The trap this guards: `page: null` is "the template decides". Rendering it as Letter
    // would write Letter into the file on the next unrelated save.
    const size = await openPageLayout({ page: null })

    expect((size as HTMLSelectElement).value).toBe('')
    expect((screen.getByLabelText('Orientation') as HTMLSelectElement).value).toBe('')
    expect((screen.getByLabelText('top margin in centimetres') as HTMLInputElement).value).toBe('0')
  })

  it('offers exactly the page sizes the model accepts', async () => {
    const size = (await openPageLayout({ page: A4_LANDSCAPE_2CM })) as HTMLSelectElement

    expect([...size.options].map((o) => o.value)).toEqual(['', 'letter', 'a4', 'legal', 'tabloid'])
  })
})

// ── the paragraph controls read the BLOCK ────────────────────────────────────

describe('the paragraph layout controls reflect the block, not a default', () => {
  const LAID_OUT = {
    blocks: [
      block({
        kind: 'paragraph', text: 'body',
        style: style({
          align: 'center', space_before_pt: 18, space_after_pt: 6, line_spacing: 1.5,
          indent_left_pt: 36, indent_right_pt: 24, first_line_indent_pt: -18, keep_with_next: true,
        }),
      }),
    ],
  }

  it('shows every one of the block’s own eight values', async () => {
    const align = await openParagraphLayout(LAID_OUT)

    expect((align as HTMLSelectElement).value).toBe('center')
    const value = (name: string) => (screen.getByLabelText(name) as HTMLInputElement).value
    expect(value('Space before, in points')).toBe('18')
    expect(value('Space after, in points')).toBe('6')
    expect(value('Line spacing multiple')).toBe('1.5')
    expect(value('Left indent, in points')).toBe('36')
    expect(value('Right indent, in points')).toBe('24')
    expect(
      (screen.getByLabelText(/first line indent, in points/i) as HTMLInputElement).value,
    ).toBe('-18')
    expect(screen.getByRole('switch', { name: /keep with next paragraph/i }))
      .toHaveAttribute('aria-checked', 'true')
  })

  it('VACUITY: a block with no style reads as all-unset in the same controls', async () => {
    const align = await openParagraphLayout({ blocks: [block({ kind: 'paragraph', text: 'body' })] })

    expect((align as HTMLSelectElement).value).toBe('')
    expect((screen.getByLabelText('Space before, in points') as HTMLInputElement).value).toBe('0')
    expect((screen.getByLabelText(/first line indent, in points/i) as HTMLInputElement).value).toBe('0')
    expect(screen.getByRole('switch', { name: /keep with next paragraph/i }))
      .toHaveAttribute('aria-checked', 'false')
  })

  it('does NOT clamp the first-line indent at zero — a hanging indent is a real request', async () => {
    await openParagraphLayout(LAID_OUT)
    const field = screen.getByLabelText(/first line indent, in points/i) as HTMLInputElement

    // A `min={0}` here would make a hanging indent unexpressible from the UI while the
    // model, the writer and the file format all support it.
    expect(field).not.toHaveAttribute('min')
  })
})

// ── the geometry preview ─────────────────────────────────────────────────────

describe('the page-geometry preview', () => {
  it('reflects the configured size and margins', async () => {
    await openPageLayout({ page: A4_LANDSCAPE_2CM })
    const preview = screen.getByRole('img', { name: /approximate a4 landscape page/i })

    // A4 landscape: 297 wide by 210 tall, so the aspect ratio is > 1.
    expect(Number(preview.dataset.aspect)).toBeGreaterThan(1)
    expect(Number(preview.dataset.aspect)).toBeCloseTo(297 / 210, 2)
    const inset = preview.firstElementChild as HTMLElement
    // 2cm of a 210mm height is ~9.5%; of a 297mm width, ~6.7%. The two must DIFFER, or the
    // preview is drawing one margin share on both axes.
    const top = parseFloat(inset.style.top)
    const left = parseFloat(inset.style.left)
    expect(top).toBeCloseTo(9.52, 1)
    expect(left).toBeCloseTo(6.73, 1)
    expect(top).not.toBeCloseTo(left, 1)
  })

  it('VACUITY: a portrait Letter page draws a different box', async () => {
    await openPageLayout({ page: LETTER_PORTRAIT_1IN })
    const preview = screen.getByRole('img', { name: /approximate letter portrait page/i })

    expect(Number(preview.dataset.aspect)).toBeLessThan(1)
    expect(Number(preview.dataset.aspect)).toBeCloseTo(8.5 / 11, 2)
    const inset = preview.firstElementChild as HTMLElement
    // 1in of 11in tall = 9.09%; of 8.5in wide = 11.76%. Inverted relative to A4 landscape.
    expect(parseFloat(inset.style.top)).toBeCloseTo(9.09, 1)
    expect(parseFloat(inset.style.left)).toBeCloseTo(11.76, 1)
  })

  it('says it is an approximation, in words, beside the shape', async () => {
    await openPageLayout({ page: A4_LANDSCAPE_2CM })

    const label = screen.getByText(/approximate page geometry/i)
    expect(label).toBeInTheDocument()
    expect(label.textContent).toMatch(/not a preview of how the text will lay out/i)
  })

  it('draws no page at all when the document names no size', async () => {
    await openPageLayout({ page: null })

    expect(screen.queryByRole('img', { name: /approximate/i })).toBeNull()
    expect(screen.getByText(/choose a page size to preview/i)).toBeInTheDocument()
  })
})

// ── a layout edit reaches the server ─────────────────────────────────────────

describe('a layout edit is what gets saved', () => {
  it('sends the changed page setup, with the loaded version as If-Match', async () => {
    await openPageLayout({ page: LETTER_PORTRAIT_1IN })

    await userEvent.selectOptions(screen.getByLabelText('Page size'), 'a4')
    await userEvent.selectOptions(screen.getByLabelText('Orientation'), 'landscape')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(saveArtifactModel).toHaveBeenCalledTimes(1))
    const [slug, version, sent] = saveArtifactModel.mock.calls[0] as [string, number, DocumentModelJson]
    expect(slug).toBe('report')
    expect(version).toBe(4)
    expect(sent.page).toMatchObject({ size: 'a4', orientation: 'landscape' })
    // The margins it did NOT touch travel unchanged — a patch, not a replacement.
    expect(sent.page?.margin_left_pt).toBe(72)
  })

  it('sends a changed paragraph style on the right block', async () => {
    await openParagraphLayout({
      blocks: [
        block({ kind: 'paragraph', text: 'first' }),
        block({ kind: 'paragraph', text: 'second' }),
      ],
    })

    await userEvent.selectOptions(screen.getByLabelText('Paragraph alignment'), 'center')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(saveArtifactModel).toHaveBeenCalledTimes(1))
    const [, , sent] = saveArtifactModel.mock.calls[0] as [string, number, DocumentModelJson]
    expect(sent.blocks[0].style).toMatchObject({ align: 'center' })
    // The block whose disclosure was never opened keeps its null style — an edit that
    // styled every block would reformat the document.
    expect(sent.blocks[1].style).toBeNull()
  })

  it('the save button stays dead until a layout control actually changes something', async () => {
    await openPageLayout({ page: LETTER_PORTRAIT_1IN })

    // `aria-disabled`, not the native attribute: `Button` given a `disabledReason` stays
    // FOCUSABLE on purpose, so a keyboard user can reach it and hear why it is unavailable.
    const save = screen.getByRole('button', { name: /^save$/i })
    expect(save).toHaveAttribute('aria-disabled', 'true')
    expect(save).toHaveAttribute('title', 'No changes to save yet.')
  })

  it('a read-only host shows no layout controls at all, and says why', async () => {
    // ABSENT, not disabled — the call DFE-5 recorded for the editor itself. A dead dropdown
    // states nothing; a keyboard user tabs onto it and learns nothing.
    mount({ page: A4_LANDSCAPE_2CM }, { readOnly: true })
    await userEvent.click(await screen.findByRole('button', { name: /page layout/i }))

    expect(screen.queryByLabelText('Page size')).toBeNull()
    expect(screen.queryByLabelText('Orientation')).toBeNull()
    expect(screen.queryByRole('switch', { name: /number the pages/i })).toBeNull()
    expect(screen.getByText(/read-only/i)).toBeInTheDocument()
    // The PREVIEW stays: reading a historical version's geometry is not editing it.
    expect(screen.getByRole('img', { name: /approximate a4 landscape page/i })).toBeInTheDocument()
  })

  it('VACUITY: an editable host really does render those controls', async () => {
    await openPageLayout({ page: A4_LANDSCAPE_2CM })

    expect(screen.getByLabelText('Page size')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /number the pages/i })).toBeInTheDocument()
  })
})

// ── the margin unit conversion ───────────────────────────────────────────────

describe('margins are shown in centimetres and stored in points', () => {
  it('typing 2 stores 2cm worth of points, not 2 points', async () => {
    await openPageLayout({ page: LETTER_PORTRAIT_1IN })
    const field = screen.getByLabelText('top margin in centimetres') as HTMLInputElement

    fireEvent.change(field, { target: { value: '2' } })
    fireEvent.blur(field)
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(saveArtifactModel).toHaveBeenCalledTimes(1))
    const [, , sent] = saveArtifactModel.mock.calls[0] as [string, number, DocumentModelJson]
    // 2cm = 56.69pt. Storing 2 would be a margin of a third of a centimetre.
    expect(sent.page?.margin_top_pt).toBeCloseTo(56.69, 1)
    expect(sent.page?.margin_top_pt).not.toBeCloseTo(2, 1)
  })
})
