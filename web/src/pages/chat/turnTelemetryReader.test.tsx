import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { render, screen, fireEvent } from '@testing-library/react'
import { ContextLedger } from './ContextLedger'
import { insertActivity } from './coalesceReducers'
import type { ActivitySegment, Segment } from './chatTypes'

// ── PCS-7 — the FRONTEND reader of the per-turn cache telemetry ───────────────────────────
//
// The numbers half of PCS-7 is shipped and railed on the backend:
// `tests/test_turn_complete_cache_telemetry.py` pins the composed sentence literal by literal,
// `tests/test_stats_cache_hit_pct.py` pins the honest `None` on a zero denominator, and
// `pricing.cache_savings_usd` returns `None` (never `0.0`) for an unpriced model.
//
// Nothing covered the READER. `ChatPage` folds `activityKind === 'stats'` into its context
// ledger and gates the Telemetry row on it — four separate lines, none of them typed in a way
// a compiler would miss, so the whole surface could be deleted with every gate staying green
// and the only symptom would be a chip that quietly stopped saying anything.
//
// Two halves, deliberately:
//
//   A. THE RENDERING, mounted. `ContextLedger` is its own module precisely so it can be
//      mounted (see `contextLedgerReach.test.tsx`); these legs render it and open the
//      disclosure the way a user does.
//   B. THE FOLD, scanned. The fold lives inside `AssistantSegments` in `ChatPage.tsx` — a
//      ~4k-line page that owns a socket and a composer and is not mountable here. So it is
//      asserted as source, in the same JSX attribute/expression form
//      `skillsUsedChip.test.ts` uses for the neighbouring `learned` row, plus one
//      BEHAVIOURAL leg through the real `insertActivity` proving the `'stats'` discriminator
//      the fold matches on is the one the live stream actually produces.
//
// The honest-`None` cases carry the weight here. A missing price rendered as `$0.00`, or an
// unmeasurable hit rate rendered as `0%`, reads as "the cache saved you nothing" when the
// truth is "we do not know" — which is worse than a blank, because a blank prompts a question
// and a zero closes one.

// The exact lines `_turn_complete_line` composes, taken from a real call rather than
// hand-written, and matched literal-for-literal against the producer's own rail in
// `tests/test_turn_complete_cache_telemetry.py`. A fixture invented here would let this file
// guard a sentence the backend never emits — the one-sided-inventory failure.
const HEAD = 'Turn complete: 3 events, 1 tool calls, context 42% · $0.0123 · 1,200 in / 340 out tokens'
/** Every fact present: the split, the hit rate, a positive saving. */
const PRICED = `${HEAD} · cache 84% hit (12,400 read / 1,200 written) · saved $0.0231`
/** `cache_savings_usd` answered `None` — an unpriced model. */
const UNPRICED = `${HEAD} · cache 84% hit (12,400 read / 1,200 written) · saved unpriced`
/** `stats.cache_hit_pct` answered `None` — a zero denominator. The counts still render. */
const NO_HIT_PCT = `${HEAD} · cache (12,400 read / 1,200 written) · saved $0.0231`
/** A MEASURED zero hit rate. An empty cache is a real answer and must not read as absent. */
const MEASURED_ZERO_HIT = `${HEAD} · cache 0% hit (0 read / 1,200 written) · saved $0.0231`
/** A MEASURED zero saving — money, not a missing price. */
const MEASURED_ZERO_SAVED = `${HEAD} · cache 84% hit (12,400 read / 1,200 written) · saved $0.0000`
/** The ordinary first turn: it only WRITES the cache, so it cost more than an uncached one. */
const NEGATIVE = `${HEAD} · cache 0% hit (0 read / 1,200 written) · saved -$0.0004`

const open = (stats?: string) => {
  render(<ContextLedger fed="Recalled relevant context · 1,204 chars" stats={stats} />)
  fireEvent.click(screen.getByRole('button'))
}

/** The Telemetry row's own text. Scoped to that row on purpose: the turn line carries a
 *  `context 42%` fragment and a `$0.0123` cost, so a "no percent"/"no dollar" assertion made
 *  against the whole ledger would be measuring the wrong fragment. */
const telemetryRow = (): string => {
  const label = screen.getByText('Telemetry:')
  return label.parentElement?.textContent ?? ''
}
/** What the row says about the money — the substring the honest-`None` rules govern. */
const savedFragment = (): string => telemetryRow().split('saved')[1] ?? ''

describe('PCS-7 A — the telemetry row renders the turn line the backend composed', () => {
  it('VACUITY FLOOR — a turn with no stats renders NO Telemetry row at all', () => {
    // Without this leg every "contains" assertion below is satisfiable by a row that is
    // always on, and "the reader works" would be indistinguishable from "the reader is a
    // constant". It is also the honest degrade: `_turn_complete_line` is live-only, so a
    // reloaded transcript has no stats line and must not grow an empty telemetry chip.
    render(<ContextLedger fed="Recalled relevant context · 1,204 chars" />)
    const chip = screen.getByRole('button')
    // The collapsed summary does not advertise telemetry it does not have…
    expect(chip.textContent).not.toContain('telemetry')
    fireEvent.click(chip)
    // …and opening produces no row either.
    expect(screen.queryByText('Telemetry:')).toBeNull()
    // The other row IS there, so this is a scoped absence rather than an empty render.
    expect(screen.getByText('Fed this turn:')).toBeTruthy()
  })

  it('the collapsed chip advertises telemetry once there IS a stats line', () => {
    render(<ContextLedger stats={PRICED} />)
    expect(screen.getByRole('button').textContent).toContain('telemetry')
  })

  it('renders all three cache facts VERBATIM — the split, the hit rate, the saving', () => {
    open(PRICED)
    const row = telemetryRow()
    expect(row).toContain('cache 84% hit (12,400 read / 1,200 written)')
    expect(row).toContain('saved $0.0231')
    // The two counts are never re-summed on the way out either: 12,400 + 1,200 = 13,600 was
    // the pre-PCS-7 rendering, and a reader that re-derived a total would resurrect it.
    expect(row).not.toContain('13,600')
    // The whole sentence survives, not just the fragment this atom added.
    expect(row).toContain(HEAD)
  })
})

describe('PCS-7 B — an unknown number never renders as a measured zero', () => {
  it('an UNPRICED model says "unpriced" and shows no money at all', () => {
    open(UNPRICED)
    expect(telemetryRow()).toContain('saved unpriced')
    // The specific lie: a missing price rendered as $0.00 reads as "the cache saved nothing".
    expect(savedFragment()).not.toContain('$')
    expect(savedFragment()).not.toContain('0.00')
  })

  it('a ZERO DENOMINATOR shows no percentage, and does not suppress the counts', () => {
    open(NO_HIT_PCT)
    const row = telemetryRow()
    expect(row).not.toContain('% hit')
    expect(row).not.toContain('0% hit')
    // An unknown hit rate must not take the split down with it — the counts are still facts.
    expect(row).toContain('cache (12,400 read / 1,200 written)')
  })

  it('a MEASURED zero saving is money, not a missing price', () => {
    open(MEASURED_ZERO_SAVED)
    expect(telemetryRow()).toContain('saved $0.0000')
    expect(telemetryRow()).not.toContain('unpriced')
  })

  it('a MEASURED zero hit rate states the zero — an empty cache is a real answer', () => {
    open(MEASURED_ZERO_HIT)
    expect(telemetryRow()).toContain('cache 0% hit')
  })

  it('a NEGATIVE saving keeps its sign — the first turn really did cost more', () => {
    open(NEGATIVE)
    // Assert the SIGN, not merely that a number arrived: an abs()/clamp anywhere in the read
    // path prints "saved $0.0004" and passes a number-only assertion while stating the exact
    // opposite of the truth.
    expect(telemetryRow()).toContain('saved -$0.0004')
    expect(savedFragment().trim().startsWith('-')).toBe(true)
  })

  it('DISCRIMINATION — unknown and measured-zero are FOUR different renderings, not two', () => {
    // The load-bearing leg. Each assertion above is individually satisfiable by a reader that
    // collapses "we don't know" into "it was zero"; only comparing the renderings as a set
    // catches the collapse. Rendered one at a time and unmounted, so the four texts come from
    // four independent mounts rather than four reads of one DOM.
    const rendered = [UNPRICED, MEASURED_ZERO_SAVED, NO_HIT_PCT, MEASURED_ZERO_HIT].map((line) => {
      const view = render(<ContextLedger stats={line} />)
      fireEvent.click(screen.getByRole('button'))
      const text = telemetryRow()
      view.unmount()
      return text
    })
    expect(new Set(rendered).size).toBe(4)
    // And named, so a failure says WHICH pair collapsed rather than only that one did.
    const [unpriced, zeroSaved, noPct, zeroPct] = rendered
    expect(unpriced).not.toBe(zeroSaved)
    expect(noPct).not.toBe(zeroPct)
  })
})

describe('PCS-7 C — the fold is wired (the reader is not an unreachable component)', () => {
  it('the live stream produces exactly the `stats` kind the fold matches on', () => {
    // Behavioural, not scanned: the WS handler calls `insertActivity(segs, text, kind, …)`
    // with the backend's `kind` verbatim, and the fold compares `activityKind === 'stats'`.
    // If those two strings ever disagree the telemetry silently stops folding, and every
    // source scan below still passes.
    const segs = insertActivity([], PRICED, 'stats', false)
    const seg = segs.find((s: Segment) => s.kind === 'activity') as ActivitySegment
    expect(seg.activityKind).toBe('stats')
    // Carried VERBATIM: the frontend formats none of these numbers, which is why the
    // backend's honesty rules are the ones that hold end to end.
    expect(seg.text).toBe(PRICED)
    // Vacuity partner: the helper is not stamping one constant kind for every caller.
    const other = insertActivity([], 'Recalled relevant context', 'context', false)
    expect((other.find((s: Segment) => s.kind === 'activity') as ActivitySegment).activityKind)
      .toBe('context')
  })

  describe('the call sites in ChatPage.tsx', () => {
    const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
    const chatPage = read('../ChatPage.tsx')
    const ledger = read('./ContextLedger.tsx')

    it('VACUITY FLOOR — the scan actually reached the page', () => {
      // A `readFileSync` pointed at the wrong relative path throws, but a page that had been
      // gutted would make every `not.toContain` below pass on an empty string.
      expect(chatPage.length).toBeGreaterThan(100_000)
      expect(chatPage).toContain('function AssistantSegments(')
      expect(ledger).toContain('export function ContextLedger(')
    })

    it('folds the stats activity into the ledger', () => {
      expect(chatPage).toContain("else if (ak === 'stats') ledger.stats = (s as ActivitySegment).text")
    })

    it('a stats-only turn still OPENS the ledger (the gate counts telemetry)', () => {
      // Dropping `ledger.stats` from `hasLedger` leaves the fold intact and the row
      // unreachable on any turn that fed no context and learned nothing.
      expect(chatPage).toContain('Boolean(ledger.fed || ledger.learned || ledger.stats)')
    })

    it('hands the folded text to the component that renders it', () => {
      expect(chatPage).toContain('stats={ledger.stats}')
      expect(chatPage).toContain("import { ContextLedger } from './chat/ContextLedger'")
    })

    it('keeps the stats line OUT of the inline step flow, so it renders once', () => {
      // `isProcess` excludes the three folded kinds. Dropping `'stats'` here would count the
      // telemetry line as a work step and render it inline as well as in the ledger — the
      // duplicate the ledger was built to remove.
      expect(chatPage).toContain("!['context', 'learned', 'stats'].includes(")
    })

    it('the component gates the Telemetry row on the prop it was handed', () => {
      expect(ledger).toContain('{stats && (')
      expect(ledger).toContain('label="Telemetry"')
      // The collapsed summary reads the same prop, so the chip cannot advertise a row that
      // is not there (and vice versa).
      expect(ledger).toContain("stats && 'telemetry'")
    })
  })
})
