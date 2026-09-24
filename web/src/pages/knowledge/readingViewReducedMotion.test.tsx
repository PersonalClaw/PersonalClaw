// ── Reading progress under `prefers-reduced-motion` (KNOWLEDGE-LIBRARY T3.1) ─────
//
// Its OWN file, and the reason is a real hazard rather than tidiness: framer-motion reads
// the reduced-motion media query ONCE and caches it in a module-level singleton
// (`initPrefersReducedMotion`, guarded by `hasReducedMotionListener`). Any render earlier
// in the same file initializes it, after which a `matchMedia` stub is inert — the test
// then passes or fails for reasons unrelated to the code under test. Measured: with the
// assertion inside readingView.test.tsx it reported 25 distinct offsets under a stub
// claiming reduce. Vitest isolates module state per FILE, so the stub has to be installed
// at module scope, before any import-time or render-time read.
//
// The measurement is the OUTCOME, not the mechanism: a `reduce ? { duration: 0 } : spring`
// branch can be present and still be handed to a prop framer-motion is not animating,
// which reads as correct code and behaves exactly like the copy that springs. Distinct
// `stroke-dashoffset` samples is what actually distinguishes them.
//
// Two-sided with `readingView.test.tsx`'s "the arc TWEENS…" case: motion allowed → more
// than two samples, motion reduced → exactly one. Neither alone would catch an arc that
// never animates at all.
import { expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'

vi.stubGlobal('matchMedia', (q: string) => ({
  matches: q.includes('prefers-reduced-motion'),
  media: q,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => false,
}))

// Dynamically imported AFTER the stub: a static import is hoisted above it, and
// framer-motion may read the media query the moment its module graph loads.
const { ReadingView } = await import('./ReadingView')

function stubScroll(el: HTMLElement, scrollTop: number) {
  Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true, configurable: true })
  Object.defineProperty(el, 'scrollHeight', { value: 2000, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: 500, configurable: true })
}

it('the reading-progress arc is set directly, never sprung, under reduced motion', async () => {
  render(
    <ReadingView
      item={{ id: 'k1', title: 'A', content: 'Body text long enough to read.', item_type: 'note' } as never}
      annotations={[]}
      onAnnotationsChanged={() => {}}
    />,
  )
  const region = screen.getByRole('group', { name: 'Article body' })
  stubScroll(region, 0)
  await act(async () => {
    region.dispatchEvent(new Event('scroll'))
    await new Promise((r) => setTimeout(r, 30))
  })
  expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '0')

  const arc = () => document.querySelectorAll('circle')[1] as SVGCircleElement
  const seen = new Set<string>()
  stubScroll(region, 1500)
  await act(async () => { region.dispatchEvent(new Event('scroll')) })
  for (let i = 0; i < 25; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 16)) })
    const v = arc().getAttribute('stroke-dashoffset')
    if (v != null) seen.add(Number(v).toFixed(2))
  }

  // The circumference at size=22 — the offset for 0% — and 0 for 100%. A jump visits
  // ONLY those two; a spring visits a dozen values between them. Asserting "no
  // intermediate value" rather than "exactly N samples" keeps the test about motion
  // instead of about how many frames elapsed before the first sample.
  const empty = 2 * Math.PI * (22 / 2 - 2.5)
  const intermediates = [...seen]
    .map(Number)
    .filter((v) => Math.abs(v - empty) > 0.5 && Math.abs(v) > 0.5)
  expect(
    intermediates,
    `reduced motion must jump, not sweep. Samples: ${[...seen].join(', ')}`,
  ).toEqual([])
  // And it must ARRIVE — an indicator that simply stopped updating would also show no
  // intermediates, and that is a different bug wearing this one's clothes.
  expect(Number([...seen].pop())).toBeCloseTo(0, 1)
  expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '100')
})
