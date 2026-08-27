import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SystemHealth } from './SystemHealth'
import type { DashboardStatus, SystemInfo } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'

// ── The system strip's nine metric labels are not optional chrome ────────────
//
// The rail metric used to ship its word-label as `hidden @min-[1520px]:inline`. That is a
// CONTAINER query, and the container is the dashboard rail island (it tracks
// `--content-width`), not the viewport — so 1520px of container needs a viewport well past
// it. Measured against a seeded gateway on a live dev server, `#/dashboard`, four viewports,
// counting the nine metric rows whose label span is laid out:
//
//   viewport 1280 → container 1018px → 0 of 9 labelled
//   viewport 1440 → container 1178px → 0 of 9 labelled   ← 14" MacBook
//   viewport 1728 → container 1466px → 0 of 9 labelled   ← 16" MacBook
//   viewport 1920 → container 1658px → 9 of 9 labelled
//
// So on both of the laptops this product names as its context, the strip read
// `2m 47s · v0.1.3 · 12% · 32.0/48GB · ↓1.0MB/s ↑5.2MB/s · 302/926GB · 5.45 · 4 · 0` —
// nine numbers with no nouns. `18.21` is a load average; `4` is a trigger count; `0` is a
// subagent count. Nothing on screen said so.
//
// The old comment claimed the `title={`${value} ${label}`}` tooltip meant "nothing is lost".
// It is not a fallback: the row is a plain non-interactive `<div>` with no `role` and no
// `aria-label`, so all nine dumped as `{tag:"DIV", aria:null, role:null}`. A `title` on that
// is not an accessible name, is not reachable by keyboard, and does not exist on touch. The
// tooltip was not covering an edge case — at every width a user actually has, it WAS the
// shipped default.
//
// AFTER: 9 of 9 labelled at all four viewports, 0 overlapping metric boxes, 0 page errors.
// The strip is `flex-wrap`, so the widths that no longer fit one line wrap to a second
// (container height 28 → 56px at 1728). Two lines of legible metrics beat one line of
// anonymous numbers.
//
// Asserted on the class string, not on computed style: jsdom has no container-query engine
// and loads no CSS, so `display` is not the observable here — the variant IS. Precedent:
// `pages/knowledge/readerInsightRail.test.tsx` pins its `@min-[58rem]:` variants the same
// way. Both halves are needed — the text assertion alone would pass if the label were
// re-gated behind a different breakpoint, and the class assertion alone would pass if the
// label span were deleted outright.

const STATUS: DashboardStatus = {
  uptime: '2m 47s', version: '0.1.3', platform: 'darwin', cron_jobs: 4, subagents: 0,
}

const SYSTEM: SystemInfo = {
  hostname: 'h', os: 'Darwin', platform: 'darwin', python: '3.12', arch: 'arm64', pid: 1,
  cpu_count: 18, cwd: '/', mem_total_gb: 48, proc_mem_mb: 100, mem_free_gb: 16,
  mem_used_gb: 32, load_1m: 5.45, load_5m: 5, load_15m: 5, cpu_pct: 12,
  disk_total_gb: 926, disk_free_gb: 624, net_rx_kbs: 1024, net_tx_kbs: 5324,
}

vi.mock('../DashboardLive', () => ({
  useDashboardLive: () => ({ status: STATUS, system: SYSTEM, doctor: null, doctorErr: null }),
}))

/** The nine metric rows: a plain `<div>` wrapper carrying a `title`, holding the
 *  `title-m` value span. HeroPulse's counters use the same value span but sit in a
 *  `<button>`, which is how they are excluded. */
function metricRows(): HTMLElement[] {
  return [...document.querySelectorAll('span[data-type="title-m"].tabular-nums')]
    .map((v) => v.parentElement as HTMLElement)
    .filter((row) => row.tagName === 'DIV' && row.hasAttribute('title'))
}

/** The word-label span inside a metric row. */
function labelSpan(row: HTMLElement): HTMLElement {
  const el = row.querySelector('span[data-type="body-m"]')
  expect(el, 'every metric row must carry a body-m word-label span').toBeTruthy()
  return el as HTMLElement
}

/** The strip only reads `navigate` (its Doctor / Updates jumps); the rest of RouteProps is
 *  inert here, supplied so the widget is typed exactly as the page mounts it. */
const ROUTE: RouteProps = {
  sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn(),
}

/** Every label a fully-populated strip must name, in render order. */
const EXPECTED_LABELS = [
  'uptime', 'darwin', 'cpu', 'mem', 'net', 'disk', 'load · 18cpu', 'triggers', 'subagents',
]

describe('the dashboard system strip labels its metrics', () => {
  it('renders all nine word-labels as text', () => {
    render(<SystemHealth {...ROUTE} />)
    const rows = metricRows()
    expect(rows.length, 'the seeded strip renders nine metrics').toBe(9)
    expect(rows.map((r) => labelSpan(r).textContent)).toEqual(EXPECTED_LABELS)
    // Each label must be findable as page text — the reading a user actually gets.
    for (const label of EXPECTED_LABELS) {
      expect(screen.getAllByText(label).length, `"${label}" must be on the strip`).toBeGreaterThan(0)
    }
  })

  it('gates no label behind a width, container-query or otherwise', () => {
    render(<SystemHealth {...ROUTE} />)
    const rows = metricRows()
    expect(rows.length).toBe(9)
    for (const row of rows) {
      const label = labelSpan(row)
      const classes = new Set(label.className.trim().split(/\s+/).filter(Boolean))
      // `hidden` was the shipped default at every real width — the whole defect.
      expect(classes.has('hidden'), `"${label.textContent}" must not be display:none by default`).toBe(false)
      // …and no responsive variant may re-introduce it. `@min-[1520px]:inline` and `@6xl:inline`
      // are the same bug wearing a different threshold; `sm:`/`md:`/`lg:` would be too.
      for (const c of classes) {
        expect(c, `"${label.textContent}" carries a width-gated class: ${c}`)
          .not.toMatch(/^(@|(sm|md|lg|xl|2xl):)/)
      }
    }
  })

  it('keeps the value and its label in the SAME row, so a wrap cannot separate them', () => {
    render(<SystemHealth {...ROUTE} />)
    for (const row of metricRows()) {
      // `shrink-0` on the row is what makes wrapping safe: the strip breaks BETWEEN
      // metrics, never mid-metric, so "5.45" never lands on a line without "load".
      expect(row.className, 'a metric must not be squeezed mid-word before the strip wraps')
        .toMatch(/\bshrink-0\b/)
      const value = row.querySelector('span[data-type="title-m"]')!.textContent
      const label = labelSpan(row).textContent
      expect(row.textContent).toBe(`${value}${label}`)
    }
  })
})
