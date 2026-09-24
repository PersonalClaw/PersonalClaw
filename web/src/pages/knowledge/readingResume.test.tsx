import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { ReadingView } from './ReadingView'
import { api, type KnowledgeItem } from '../../lib/api'
import { getReadingPosition, setReadingPosition } from './readingPosition'

// ── "Resumes at the persisted reading position" (`KL-8`'s own done-when clause) ─────────────
//
// 🔑 THIS FILE IS THE WRITER RAIL. `KL-7` shipped a progress ring that REPORTS the fraction;
// nothing persisted it. A continue-reading shelf reading a position nobody writes greps
// identically to a working one and is empty forever — so the assertions here are about the
// WRITE happening at a real call site and the RESTORE landing, not about the ring rendering.
//
// 🪤 The ordering trap this pins. The progress reader fires 0 on mount, and `setReadingPosition`
// treats a fraction under 2% as "not started" and DELETES the entry. So a persist that is not
// gated on the restore erases the position it is about to resume to — the surface would look
// completely correct and lose your place on every open. `restores … before the first write`
// is that test.

const KEY = 'knowledge-reading-positions'
const LONG_ARTICLE = [
  '# On long articles', '',
  'A paragraph long enough that the reader has somewhere to scroll to.', '',
  '## A second section', '',
  'Another paragraph, so the outline has two rows and the body has height.',
].join('\n')

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return { id: 'k1', title: 'On long articles', content: LONG_ARTICLE, item_type: 'note', word_count: 440, ...over } as KnowledgeItem
}

/** jsdom gives every element zero scroll metrics, so a scroll fraction is unobservable until
 *  they are supplied — the same fixture `readingView.test.tsx` uses. 2000/500 makes the span
 *  1500, so a 50% position is scrollTop 750 exactly. */
function stubScroll(el: HTMLElement, scrollTop: number, scrollHeight = 2000, clientHeight = 500) {
  Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true, configurable: true })
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
}

const article = () => screen.getByRole('group', { name: 'Article body' })

/** Let the mount's rAF (the restore) and the 400ms persist debounce both run. */
async function settle(ms = 500) {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)) })
}

/** Scroll the reader and let the write land.
 *
 *  🪤 TWO settles, not one, and the reason is React 18 rather than the component: the rAF that
 *  reads the fraction fires OUTSIDE `act`, so the re-render (and with it the effect that arms the
 *  400ms debounce) is flushed when the enclosing `act` EXITS. Arming and asserting inside one
 *  settle measures a timer that started 0ms ago — the write is real, the fixture just outran it.
 */
async function scrollTo(region: HTMLElement, top: number) {
  stubScroll(region, top)
  await act(async () => { region.dispatchEvent(new Event('scroll')) })
  await settle(50)   // flush the progress update + arm the debounce
  await settle()     // let the debounce fire
}

function mount(over: Partial<KnowledgeItem> = {}) {
  render(<ReadingView item={item(over)} annotations={[]} onAnnotationsChanged={() => {}} />)
  return article()
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  vi.spyOn(api, 'setKnowledgeReadState').mockResolvedValue({ ok: true, read_state: 'reading' })
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = () =>
      ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect
  }
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {},
    removeEventListener: () => {}, dispatchEvent: () => false,
  }))
})

describe('the reader persists where you stopped', () => {
  it('writes the fraction after the scroll settles', async () => {
    const region = mount()
    stubScroll(region, 0)
    await settle()
    expect(getReadingPosition('k1'), 'the top of an article is not a place to resume').toBeNull()

    await scrollTo(region, 750)

    expect(getReadingPosition('k1')?.pct).toBeCloseTo(0.5, 2)
  })

  it('restores the saved position before the first write can erase it', async () => {
    setReadingPosition('k1', 0.5)
    const region = mount()
    stubScroll(region, 0)
    await settle()

    // The scroller was moved to the saved place …
    expect(region.scrollTop).toBe(750)
    // … and the entry survived the mount's 0-progress read.
    expect(getReadingPosition('k1')?.pct).toBeCloseTo(0.5, 2)
    // The ring agrees with the scroller, so the reader is told where they are.
    expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '50')
  })

  it('forgets the position once the article is finished', async () => {
    setReadingPosition('k1', 0.5)
    const region = mount()
    stubScroll(region, 0)
    await settle()   // let the RESTORE land first — it moves the scroller, so stubbing the
                     // bottom before it runs would just be overwritten by the resume.
    await scrollTo(region, 1500)  // read to the bottom

    expect(getReadingPosition('k1'), 'a finished article has nothing left to resume to').toBeNull()
  })

  it('survives a corrupted store rather than throwing inside a render', async () => {
    localStorage.setItem(KEY, '{not json')
    const region = mount()
    stubScroll(region, 0)
    await settle()
    expect(region.scrollTop).toBe(0)
    expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toBeInTheDocument()
  })
})

describe('opening the reader is what puts an item on the shelf', () => {
  it('marks an UNREAD item as reading once the reader actually scrolls', async () => {
    const region = mount({ read_state: 'unread' })
    stubScroll(region, 0)
    await settle(50)
    expect(api.setKnowledgeReadState, 'the top of the page is not reading it').not.toHaveBeenCalled()

    await scrollTo(region, 300)

    expect(api.setKnowledgeReadState).toHaveBeenCalledWith('k1', 'reading')
  })

  it('never demotes a FINISHED item back onto the shelf', async () => {
    const region = mount({ read_state: 'read' })
    await scrollTo(region, 300)

    expect(api.setKnowledgeReadState).not.toHaveBeenCalled()
  })

  it('writes the state once, not once per scroll frame', async () => {
    const region = mount({ read_state: 'unread' })
    for (const top of [300, 400, 500]) await scrollTo(region, top)
    expect(vi.mocked(api.setKnowledgeReadState).mock.calls).toHaveLength(1)
  })
})
