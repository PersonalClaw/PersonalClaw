// @module-tag tree-scan
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { TurnHead, MIN_HOLD_MS, SHOW_DELAY_MS, FADE_MS } from './TurnHead'
import { WORK_PHASES, WORK_PHASE_LOOKS, phaseFamily, phaseLabel, type WorkPhase } from '../../../lib/workPhase'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../../test/sourceTree'

// ── The head of a reply: the orb and one static line, for the turn that is running ──────────────
//
// The chat showed "Thinking" with seven rotating stock phrases ("Connecting the dots…", "Almost
// there…") until the reply's first word, and then nothing at all while the reply streamed, its
// tools ran, or it waited on her OK. Now one orb and one line sit at the head of the reply for the
// whole turn. The line is the gateway's own (`work_phase`'s `line`), or the phase's word when it says
// none, so it always names the exact phase with the orb hidden. The orb shows after 150 ms and holds
// at least 300 ms, so a fast reply never flashes it, and when the turn ends it settles to the
// ending's still mark and fades within 300 ms.

type Props = Parameters<typeof TurnHead>[0]
const running = (phase: WorkPhase | null, line = ''): Props => ({ running: true, phase, line, ending: null })

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

const head = () => document.querySelector('[data-turn-head]') as HTMLElement | null
const lineOf = () => document.querySelector('[data-turn-line]') as HTMLElement
const orb = () => document.querySelector('[data-agent-orb]') as HTMLElement
const pass = (ms: number) => act(() => { vi.advanceTimersByTime(ms) })

describe('the line names the exact phase', () => {
  it('every phase of the contract has words of its own: a planted phase without them fails here', () => {
    for (const phase of WORK_PHASES) expect(WORK_PHASE_LOOKS[phase].label.trim(), phase).not.toBe('')
  })

  it.each([...WORK_PHASES])('%s with no line from the gateway reads its own word, never a stock phrase', (phase) => {
    render(<TurnHead {...running(phase)} />)
    pass(SHOW_DELAY_MS)
    expect(lineOf().textContent).toBe(phaseLabel(phase))
    expect(orb().dataset.orbPhase).toBe(phase)
    // With the orb gone, what is left still names the phase: colour and motion never carry it.
    orb().remove()
    expect(head()!.textContent).toBe(phaseLabel(phase))
  })

  it('the gateway’s line is the words when it says one', () => {
    render(<TurnHead {...running('tool', 'Terminal git log --oneline')} />)
    pass(SHOW_DELAY_MS)
    expect(lineOf().textContent).toBe('Terminal git log --oneline')
    expect(screen.getByRole('img')).toHaveAccessibleName(phaseLabel('tool'))
  })

  it('a turn this page sent, before the gateway has said anything of it, is waiting to start', () => {
    render(<TurnHead {...running(null)} />)
    pass(SHOW_DELAY_MS)
    expect(orb().dataset.orbPhase).toBe('queued')
    expect(lineOf().textContent).toBe(phaseLabel('queued'))
  })

  it('no stock phrase, rotation or guessed phase is left in the app', () => {
    const files = filesUnder(join(process.cwd(), 'src'), (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
    for (const file of files) {
      const src = readSource(file)
      expect(src, file).not.toMatch(/CURATED_PHRASES|Connecting the dots|derivePhase/)
    }
    expect(files.some((f) => f.endsWith('StreamingIndicator.tsx')), 'the old indicator is gone').toBe(false)
  })
})

describe('the settled rows', () => {
  it('queued breathes, in the active family', () => {
    render(<TurnHead {...running('queued', 'Waiting to start')} />)
    pass(SHOW_DELAY_MS)
    expect(orb().dataset.orbDrawing).toBe('breathing')
    expect(orb().dataset.orbFamily).toBe('active')
    expect(orb().dataset.agentOrb).toBe('animated')
  })

  it('input is a still frame with an info-tone "?" badge, in the waiting family, beside her question', () => {
    render(<TurnHead {...running('input', 'Asks: Which branch?')} />)
    pass(SHOW_DELAY_MS)
    expect(orb().dataset.agentOrb).toBe('still')
    expect(orb().dataset.orbFamily).toBe('waiting')
    const badge = document.querySelector('[data-orb-badge]') as HTMLElement
    expect(badge.textContent).toBe('?')
    expect(badge.style.color).toBe('var(--color-info)')
    expect(lineOf().textContent).toBe('Asks: Which branch?')
  })

  it('streaming reads "Writing"', () => {
    render(<TurnHead {...running('streaming')} />)
    pass(SHOW_DELAY_MS)
    expect(lineOf().textContent).toBe('Writing')
    expect(orb().dataset.orbDrawing).toBe('composing')
  })

  it.each([
    ['done', 'Done in 12 seconds', 'quiet', null],
    ['error', 'Stopped: the model refused the request', 'blocked', '×'],
  ] as const)('%s shows the turn’s ending line on a still mark', (phase, line, family, glyph) => {
    const { rerender } = render(<TurnHead {...running('streaming')} />)
    pass(SHOW_DELAY_MS + MIN_HOLD_MS)
    // The turn's last frame names no turn, and says how it ended.
    rerender(<TurnHead running phase={null} line="" ending={{ phase, line }} />)
    expect(lineOf().textContent).toBe(line)
    expect(orb().dataset.orbPhase).toBe(phase)
    expect(orb().dataset.orbFamily).toBe(family)
    expect(orb().dataset.agentOrb).toBe('still')
    expect(document.querySelector('[data-orb-badge]')?.textContent ?? null).toBe(glyph)
  })
})

describe('timing: shown after 150 ms, held at least 300 ms, faded within 300 ms', () => {
  it('a turn that ends before 150 ms never shows the orb', () => {
    const { rerender } = render(<TurnHead {...running('thinking')} />)
    pass(SHOW_DELAY_MS - 50)
    expect(head()).toBeNull()
    rerender(<TurnHead running={false} phase={null} line="" ending={{ phase: 'done', line: 'Done in 1 second' }} />)
    pass(2_000)
    expect(head()).toBeNull()
  })

  it('a shown orb stays at least 300 ms, then settles and fades out', () => {
    const { rerender } = render(<TurnHead {...running('thinking')} />)
    pass(SHOW_DELAY_MS)
    expect(head()).not.toBeNull()
    pass(100)
    rerender(<TurnHead running={false} phase={null} line="" ending={{ phase: 'done', line: 'Done in 1 second' }} />)
    pass(MIN_HOLD_MS - 100 - 1)
    expect(head()!.dataset.turnHeadStage).toBe('shown')
    expect(lineOf().textContent).toBe('Done in 1 second')
    pass(1)
    expect(head()!.dataset.turnHeadStage).toBe('leaving')
    expect(head()!.style.opacity).toBe('0')
    pass(FADE_MS)
    expect(head()).toBeNull()
  })

  it('fades within 300 ms', () => {
    expect(FADE_MS).toBeLessThanOrEqual(300)
    expect(SHOW_DELAY_MS).toBe(150)
    expect(MIN_HOLD_MS).toBe(300)
  })

  it('a turn that comes back while the head fades shows it again at once', () => {
    const { rerender } = render(<TurnHead {...running('thinking')} />)
    pass(SHOW_DELAY_MS + MIN_HOLD_MS)
    rerender(<TurnHead running={false} phase={null} line="" ending={null} />)
    pass(0)
    expect(head()!.dataset.turnHeadStage).toBe('leaving')
    rerender(<TurnHead {...running('tool', 'Read notes.md')} />)
    expect(head()!.dataset.turnHeadStage).toBe('shown')
    expect(lineOf().textContent).toBe('Read notes.md')
  })

  it('with nothing running and nothing ending, there is no head, and no orb', () => {
    render(<TurnHead running={false} phase={null} line="" ending={null} />)
    pass(2_000)
    expect(head()).toBeNull()
    expect(document.querySelector('[data-agent-orb]')).toBeNull()
  })
})

describe('what a screen reader hears', () => {
  it('the line is the turn’s polite live region, and the orb is an image outside it, said once', () => {
    render(<TurnHead {...running('searching', 'Searching your knowledge')} />)
    pass(SHOW_DELAY_MS)
    expect(lineOf().getAttribute('aria-live')).toBe('polite')
    const img = screen.getByRole('img')
    expect(img.closest('[aria-live]'), 'the orb sits inside a live region').toBeNull()
    expect(img).toHaveAccessibleName(phaseLabel('searching'))
  })

  it('every phase keeps its family on the orb and its words on the line', () => {
    for (const phase of WORK_PHASES) {
      const { unmount } = render(<TurnHead {...running(phase)} />)
      pass(SHOW_DELAY_MS)
      expect(orb().dataset.orbFamily, phase).toBe(phaseFamily(phase))
      expect(lineOf().textContent, phase).toBe(phaseLabel(phase))
      unmount()
    }
  })

  it('the ending is not said by the line: the transcript’s turn narration says it, once', () => {
    const { rerender } = render(<TurnHead {...running('streaming')} />)
    pass(SHOW_DELAY_MS)
    rerender(<TurnHead running phase={null} line="" ending={{ phase: 'done', line: 'Done in 4 seconds' }} />)
    expect(lineOf().getAttribute('aria-live')).toBe('off')
  })
})
