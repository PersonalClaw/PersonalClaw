import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { Artifact } from '../../lib/api'
import { ArtifactCard } from './ArtifactCard'
import { PdfFilePreview, ImageFilePreview } from '../../ui/content/renderers'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'
import { resolveContentType } from '../../ui/content/contentTypes'
import { ARTIFACT_KINDS, artifactKindMeta } from '../files/fileMeta'

// ── DFE-1: the artifact library stops lying about generated documents ─────────────────
//
// Three separate user-visible lies, all on the same surface, all shipped since the
// generate tools landed:
//
//   1. a generated .docx was labelled "Widget"          (fixed upstream in #127)
//   2. its CARD rendered a broken-image glyph           (this change)
//   3. a generated .pdf could not be previewed at all   (this change)
//
// 🪤 THE EASY FAILURE IS FIXING (2) BY BREAKING THE IMAGE CASE. `ArtifactCard` had ONE
// flag — `isImage = !!ctype.binary` — doing two jobs: "skip the body fetch, the content
// is a URL ref" AND "draw this as a thumbnail". Narrowing it to real images alone fixes
// the glyph and silently sends office artifacts down the EXCERPT path, where the card
// would render the literal string "/api/artifacts/<slug>/raw" as if it were the
// document's text. So both halves are asserted here: office/pdf/video get an icon, and
// an image STILL thumbnails from /raw.
//
// 🪤 A `lazy()` PREVIEW CANNOT BE MOUNTED HERE. registerBuiltins wraps every renderer in
// `lazy()`, and those chunks pull in Monaco (no web workers under jsdom). The registry's
// job — routing kind:pdf to the pdf type rather than a second registration — is asserted
// by id; the renderer's own source resolution is asserted by importing it directly.

registerBuiltinContentTypes()

// The card fetches its own body lazily for TEXT kinds. Every kind under test here is
// binary, so a call to this at all is a defect: it means a binary artifact was routed
// down the excerpt path.
const fetched: string[] = []

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      artifact: async (slug: string) => {
        fetched.push(slug)
        return { slug, content: `/api/artifacts/${slug}/raw` } as unknown as Artifact
      },
    },
  }
})

const art = (kind: string, slug = `a-${kind}`): Artifact => ({
  slug, name: `Quarterly report.${kind}`, kind, source: 'chat', version: 1,
  created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:00Z',
} as unknown as Artifact)

/** The card's preview pane is the first child of the tile button. */
function previewPane(): HTMLElement {
  const tile = screen.getByRole('button')
  return tile.firstElementChild as HTMLElement
}

beforeEach(() => { fetched.length = 0 })

describe('a generated document is labelled by its real kind', () => {
  // Clause 1 lives in artifactKinds.test.ts (the closed-set rails against the backend's
  // ALLOWED_KINDS). What is pinned HERE is the two things a USER does with the label:
  // reads it on the card, and picks it in the toolbar filter.
  it('names the format instead of falling through to "Widget"', () => {
    const km = artifactKindMeta('docx')
    expect(km.label, 'a Word document read "Widget" from v0.1.0 to 0.1.3').not.toBe('Widget')
    expect(km.label).toMatch(/word/i)
  })

  it('renders that label on the card, not the fallback kind', () => {
    render(<ArtifactCard art={art('docx')} onOpen={() => {}} />)
    expect(screen.getByText(artifactKindMeta('docx').label)).toBeInTheDocument()
    expect(screen.queryByText('Widget')).toBeNull()
  })

  it('is selectable in the library toolbar filter', () => {
    // ArtifactsSection maps ARTIFACT_KINDS straight into the kind Segmented, so presence
    // in the table IS filterability — that is why the omission made these artifacts
    // unreachable rather than merely mislabelled.
    const keys = ARTIFACT_KINDS.map((k) => k.key)
    for (const kind of ['docx', 'xlsx', 'pptx', 'pdf', 'csv', 'video']) {
      expect(keys, `${kind} cannot be filtered for`).toContain(kind)
    }
  })
})

describe('a card draws a thumbnail only when a browser can decode one', () => {
  for (const kind of ['docx', 'xlsx', 'pptx', 'pdf', 'video']) {
    it(`${kind} shows its kind icon, not a broken image`, async () => {
      render(<ArtifactCard art={art(kind)} onOpen={() => {}} />)
      const pane = previewPane()
      expect(pane.querySelector('img'), 'a browser cannot decode these bytes as an image')
        .toBeNull()
      // lucide renders an inline <svg>; the tile is the only svg in the preview pane.
      expect(pane.querySelector('svg'), 'no kind icon rendered').toBeTruthy()
      // Nothing to fetch: the body is bytes and the icon needs none of it.
      await waitFor(() => expect(fetched).toEqual([]))
    })
  }

  it('an image artifact STILL thumbnails from its raw URL', () => {
    render(<ArtifactCard art={art('image', 'a-chart')} onOpen={() => {}} />)
    const img = previewPane().querySelector('img')
    expect(img, 'the image thumbnail is the regression rail on this change').toBeTruthy()
    expect(img!.getAttribute('src')).toBe('/api/artifacts/a-chart/raw')
  })

  it('a text kind still reads its body as an excerpt', () => {
    // The third branch: proves the binary split did not swallow the text path (a card
    // that quietly stopped fetching would look like a clean pass).
    render(<ArtifactCard art={art('markdown', 'a-notes')} onOpen={() => {}} />)
    return waitFor(() => expect(fetched).toEqual(['a-notes']))
  })
})

describe('a pdf previews from either source', () => {
  it('the registry routes a kind:pdf artifact to the pdf type', () => {
    // Not a second registration — registerBuiltins deliberately reuses the file
    // renderer for the artifact, which is why the renderer had to learn both sources.
    expect(resolveContentType({ kind: 'pdf' }).id).toBe('pdf')
    expect(resolveContentType({ name: 'report.pdf' }).id).toBe('pdf')
  })

  it('a GENERATED pdf artifact previews inline from its raw ref', () => {
    const { container } = render(
      <PdfFilePreview content="/api/artifacts/a-pdf/raw" mode="dark" title="Report" />
    )
    const obj = container.querySelector('object')
    expect(obj, 'a generated pdf previewed nothing at all before this').toBeTruthy()
    expect(obj!.getAttribute('data')).toBe('/api/artifacts/a-pdf/raw')
  })

  it('a pdf FILE still previews from its path', () => {
    // The regression rail: the file half was the ONLY half that worked, so it is the
    // half a source-agnostic rewrite is most likely to break.
    const { container } = render(
      <PdfFilePreview path="docs/report.pdf" content="" mode="dark" title="Report" />
    )
    const obj = container.querySelector('object')
    expect(obj, 'the pdf FILE half stopped previewing — the half that already worked')
      .toBeTruthy()
    expect(obj!.getAttribute('data')).toBe('/api/file-raw?path=docs%2Freport.pdf&resolve=1')
  })

  it('says so when it has neither, rather than linking to nowhere', () => {
    // An empty `data` made the object fall back to "PDF preview not supported in this
    // browser" with an Open-in-browser link to "" — the wrong failure, twice.
    render(<PdfFilePreview content="" mode="dark" title="Report" />)
    expect(screen.getByText(/no longer available/i)).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('resolves its source the same way the image renderer already did', () => {
    // Both are binary artifacts whose content is a URL ref; divergent resolution here is
    // how one of them worked and the other did not.
    const pdf = render(<PdfFilePreview content="/api/artifacts/p/raw" mode="dark" title="p" />)
    expect(pdf.container.querySelector('object')!.getAttribute('data'))
      .toBe('/api/artifacts/p/raw')
    const img = render(<ImageFilePreview content="/api/artifacts/i/raw" mode="dark" title="i" />)
    expect(img.container.querySelector('img')!.getAttribute('src')).toBe('/api/artifacts/i/raw')
  })
})
