import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { KnowledgeDetail } from './KnowledgeDetail'
import { KnowledgeDetailPage } from './KnowledgeDetailPage'
import { ReaderInsights, hasReaderInsights } from './KnowledgeDetailPage'
import { api, type KnowledgeAnnotation, type KnowledgeItem } from '../../lib/api'
import * as store from './knowledgeStore'

// ── The reader's insight rail (KL-16) ─────────────────────────────────────────────────
//
// The clause: reading mode "no longer REPLACES the insights dock — related items, entities
// and highlights ride a rail SPLIT BY A CONTAINER QUERY ON THE READER PANE, not a viewport
// breakpoint, because the details panel and nav rail steal width without narrowing the
// viewport."
//
// Three ways that could look held while being absent, and one thing jsdom cannot show:
//
//  • MOUNTED BUT NEVER ASKED. A rail component that exists but is not rendered by the
//    reading-mode branch is invisible in production and green in a component-only test. So
//    the dock case renders the real `KnowledgeDetail` in reading mode, and the wiring case
//    renders the real `KnowledgeDetailPage`, letting each call its own code path.
//  • GREEN ON AN EMPTY RENDER. "The related item is on screen" passes trivially if nothing
//    rendered and the query was wrong. Every presence assertion here is paired with a
//    POSITIVE CONTROL that the article body rendered too.
//  • A VIEWPORT BREAKPOINT WEARING THE CLAUSE'S CLOTHES. `lg:flex-row` produces the same
//    two-column picture on a wide window and is exactly the bug the clause names. Two
//    assertions separate them: the split's responsive utilities must ALL be container
//    variants resolving against a `@container` ancestor that is the pane itself, and — the
//    behavioural half — changing the WINDOW's width must not change the rail's markup at
//    all, which fails immediately for any implementation reading a viewport width.
//
// 🪤 WHAT JSDOM CANNOT PROVE. jsdom has no layout engine and evaluates no container query,
// so nothing here can observe the rail actually MOVING beside the article at 58rem or the
// disclosure actually disappearing above it. Those are CSS facts, asserted at the level
// jsdom can reach: that the container context is established on the pane, that the rail and
// the disclosure are driven by `@min-[58rem]` variants of it, and (in the build) that
// Tailwind emits `@container (width>=58rem)` for them. The layout itself is a browser check.

const LONG_ARTICLE = [
  '# Widgets, considered',
  '',
  'The quick brown fox jumps over the lazy dog, at length, for several lines.',
  '',
  '## How widgets are made',
  '',
  'Another paragraph about the making of widgets.',
].join('\n')

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k1',
    title: 'Widgets, considered',
    content: LONG_ARTICLE,
    item_type: 'note',
    word_count: 440,
    entities: [{ id: 'e1', name: 'Widget Co', entity_type: 'org' }],
    ...over,
  } as KnowledgeItem
}

function annotation(over: Partial<KnowledgeAnnotation> = {}): KnowledgeAnnotation {
  return {
    id: 'a1',
    item_id: 'k1',
    quote: 'quick brown fox',
    occurrence: 0,
    note: '',
    created_at: '2026-08-19T00:00:00',
    ...over,
  }
}

const RELATED = [{ id: 'k2', title: 'A neighbouring note', shared_entities: 3 } as KnowledgeItem]

/** Everything `KnowledgeDetail` fetches on mount, stubbed so a render settles. Without this
 *  its other requests reject and the tree under assertion never stops updating. */
function stubDetailMount() {
  vi.spyOn(store, 'getKnowledge').mockResolvedValue(null)
  vi.spyOn(api, 'knowledgeItemIntents').mockResolvedValue({ outcomes: [] } as never)
  vi.spyOn(api, 'knowledgeItemGraph').mockRejectedValue(new Error('no graph'))
  vi.spyOn(api, 'knowledgeTags').mockResolvedValue([] as never)
  vi.spyOn(api, 'knowledgeStaleness').mockRejectedValue(new Error('no staleness'))
}

/** The reader as the page mounts it: reading mode on, and the rail node supplied exactly
 *  when `hasReaderInsights` says there is something to put in it (KnowledgeDetailPage's own
 *  condition, so a test can drive the empty case honestly). */
function renderReader(over: Partial<KnowledgeItem> = {}, annotations = [annotation()], related = RELATED) {
  const it_ = item(over)
  return render(
    <KnowledgeDetail
      item={it_}
      reading
      annotations={annotations}
      onChanged={() => {}}
      onDeleted={() => {}}
      insightRail={hasReaderInsights(it_, related, annotations) ? (
        <ReaderInsights item={it_} related={related} annotations={annotations}
          onRemoveAnnotation={() => {}} onOpenItem={() => {}} />
      ) : undefined}
    />,
  )
}

const articleBody = () => screen.getByRole('group', { name: 'Article body' })
// The rail is named for what is IN it ("Article outline & insights" on this fixture, whose
// body has headings and whose item has all three insight kinds) — a control that promised an
// outline on a heading-less body would reveal less than its own name.
const rail = () => screen.getByLabelText('Article outline & insights')

/** A Tailwind responsive prefix that measures the VIEWPORT (`lg:flex-row`) versus one that
 *  measures the nearest container (`@min-[58rem]:flex-row`, `@3xl:flex-row`). */
const VIEWPORT_VARIANT = /(?:^|\s)(?:max-)?(?:sm|md|lg|xl|2xl):/
const CONTAINER_VARIANT = /(?:^|\s)@(?:min-\[[^\]]+\]|max-\[[^\]]+\]|[a-z0-9]+):/

/** Every responsive utility on an element, split into the two families. */
function variants(el: HTMLElement) {
  const classes = el.className.split(/\s+/).filter(Boolean)
  return {
    container: classes.filter((c) => CONTAINER_VARIANT.test(c)),
    viewport: classes.filter((c) => VIEWPORT_VARIANT.test(c)),
  }
}

afterEach(() => { vi.restoreAllMocks() })

describe('reading mode no longer replaces the insights dock', () => {
  beforeEach(stubDetailMount)

  it('keeps highlights, entities and related items reachable inside the reader', async () => {
    renderReader()
    // The POSITIVE CONTROL first: if the article did not render, nothing below means
    // anything — an empty tree would satisfy "no missing content" vacuously.
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    expect(articleBody().textContent, 'the article body is what reading mode is FOR')
      .toContain('The quick brown fox jumps over the lazy dog')

    // All three of the clause's nouns, on the same surface as the prose.
    expect(rail().textContent, 'the user\'s own highlights').toContain('quick brown fox')
    expect(rail().textContent, 'the entities extracted from it').toContain('Widget Co')
    expect(rail().textContent, 'and what it is related to').toContain('A neighbouring note')
  })

  it('renders the dock\'s OWN section components, not a second copy of them', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    // The section labels are `KnowledgeExtras`'s, carrying its counts — the tell that the
    // rail consumes the extracted sections rather than reimplementing the same markup with
    // its own wording. A reader-only rewrite would drift from these the first time either
    // side changed.
    expect(rail().textContent).toContain('Highlights · 1')
    expect(rail().textContent).toContain('Entities · 1')
    expect(rail().textContent).toContain('Related · 1')
  })

  it('offers no rail at all when there is nothing to put in it', async () => {
    // No headings either — the outline shares the rail, so an item with no insights but a
    // sectioned body still has a rail to show, and only both being empty removes it.
    renderReader({ entities: [], content: 'One paragraph, no headings, nothing extracted.' }, [], [])
    // The positive control matters most in the absence case: "no rail" and "nothing
    // rendered" look identical to a query that returns null.
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    expect(articleBody().textContent).toContain('One paragraph, no headings')
    // An always-on column with nothing in it is worse than no column, so the decision is
    // made where the data is (`hasReaderInsights`) and travels as the node's absence.
    expect(hasReaderInsights(item({ entities: [] }), [], [])).toBe(false)
    expect(screen.queryByLabelText(/^Article (outline|insights)/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Insights/ }), 'nor a fold-out for nothing').toBeNull()
  })
})

describe('the rail is split by the READER PANE, not the viewport', () => {
  beforeEach(stubDetailMount)

  it('puts the rail on the same axis as the article, under one split element', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const split = rail().parentElement!
    expect(split.contains(articleBody()), 'article and rail must be siblings of one split')
      .toBe(true)
    expect(rail().tagName, 'a landmark a screen-reader user can jump to').toBe('ASIDE')
  })

  it('decides the split with a container variant resolving against the PANE', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const split = rail().parentElement!

    // 1. Direction is container-scoped, and nothing on this axis measures the viewport.
    const v = variants(split)
    expect(v.container, `the split must be governed by a container query: ${split.className}`)
      .toContain('@min-[58rem]:flex-row')
    expect(v.viewport, 'a viewport breakpoint here is the bug the clause names').toEqual([])
    expect(variants(rail()).viewport, 'and the rail itself must not read one either').toEqual([])

    // 2. The variant has something to resolve against, and that something is the PANE —
    //    the element holding the whole reader, not a sub-box of it. If it were only the
    //    split, the query would still measure a child of the pane; the point of the clause
    //    is that the pane is what the nav rail and the details panel narrow.
    let pane: HTMLElement | null = split.parentElement
    while (pane && !pane.className.split(/\s+/).includes('@container')) pane = pane.parentElement
    expect(pane, 'no @container ancestor — the container variants above resolve against nothing')
      .not.toBeNull()
    expect(pane!.contains(split), 'the pane contains the split').toBe(true)
    expect(pane!.textContent, 'and the whole reader, progress indicator included').toMatch(/% read/)
  })

  it('does not read the window width — the behavioural half of the same claim', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const before = rail().className

    // A viewport-driven implementation (a `lg:` prefix is CSS, but a `useIsMobile()`-style
    // hook is not) re-renders here. A container-driven one cannot see this at all: the
    // window changed and the reader pane did not.
    for (const w of [400, 2000]) {
      Object.defineProperty(window, 'innerWidth', { value: w, writable: true, configurable: true })
      window.dispatchEvent(new Event('resize'))
      await new Promise((r) => setTimeout(r, 0))
      expect(rail().className, `the rail changed when only the WINDOW did (${w}px)`).toBe(before)
    }
  })
})

describe('the narrow-pane fold-out', () => {
  beforeEach(stubDetailMount)

  it('announces its expanded state and flips the rail between hidden and shown', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const toggle = screen.getByRole('button', { name: /Insights/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(rail().className.split(/\s+/), 'folded away in a one-column pane').toContain('hidden')

    fireEvent.click(toggle)
    await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'true'))
    expect(rail().className.split(/\s+/)).toContain('flex')
    expect(rail().className.split(/\s+/)).not.toContain('hidden')
  })

  it('is itself container-gated, and the rail stays unconditional above the threshold', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    // jsdom evaluates no container query, so the toggle is visible here regardless; what is
    // assertable is that the utility hiding it is a container variant of the pane. Above
    // 58rem the rail is already on screen, and a toggle promising a fold that does not
    // happen is worse than no toggle.
    const gate = screen.getByRole('button', { name: /Insights/ }).parentElement!
    expect(variants(gate).container).toContain('@min-[58rem]:hidden')
    expect(variants(gate).viewport).toEqual([])

    // And the rail's own reveal is container-scoped too — which is why the fold-out flag is
    // NOT allowed to gate the render. `{railOpen && <aside/>}` would remove the rail from
    // the DOM in exactly the wide pane that has room for it, where CSS cannot bring it back.
    expect(variants(rail()).container).toContain('@min-[58rem]:flex')
  })
})

describe('the page actually supplies the rail — the last mile', () => {
  beforeEach(() => {
    stubDetailMount()
    vi.spyOn(api, 'knowledgeItem').mockResolvedValue(item() as never)
    vi.spyOn(api, 'knowledgeExtracted').mockResolvedValue({ contents: [] } as never)
    vi.spyOn(api, 'knowledgeItemRelated').mockResolvedValue(RELATED as never)
    vi.spyOn(api, 'knowledgeAnnotations').mockResolvedValue([annotation()] as never)
    vi.spyOn(api, 'knowledgeDuplicates').mockRejectedValue(new Error('no duplicates'))
  })

  it('mounts the rail from the real page, with the real fetched data', async () => {
    // `?read=1` is how reading mode is entered — the URL, not a prop — so this drives the
    // surface the way a user's link does.
    render(
      <KnowledgeDetailPage id="k1" onBack={() => {}} onOpenItem={() => {}}
        query={{ read: '1' }} setQuery={() => {}} />,
    )
    await waitFor(() => expect(screen.getByRole('group', { name: 'Article body' })).toBeInTheDocument())
    // Positive control: the reader is open on the fetched body, not an empty shell.
    expect(articleBody().textContent).toContain('The quick brown fox jumps over the lazy dog')
    // …and the fetched related item / annotation reached the rail through the page's own
    // wiring, not a test-supplied node.
    await waitFor(() => expect(rail().textContent).toContain('A neighbouring note'))
    expect(rail().textContent).toContain('quick brown fox')
  })
})
