import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'

// ── Nine numbers with nothing saying what they count ────────────────────────────────────────────
//
// The rail island's metric labels were gated `hidden @min-[1520px]:inline`. That is a CONTAINER
// width, not a viewport one — the island tracks `--content-width`, so it runs ~230px narrower than
// the window. Measured on a demo-seeded gateway at `#/dashboard`, four viewports, the island's own
// width and how many of the nine labels actually painted:
//
//     viewport   island    labels painted
//       1280      1052         0            "3m 37s · v0.1.3 · 59% · 33.0/48GB · ↓5.7MB/s
//       1440      1212         0             ↑7.2MB/s · 303/926GB · 13.44 · 4 · 0"
//       1728      1500         0            ← 20px short of the gate, on a 16" MacBook
//       1920      1692         9            ← the ONLY width that ever read as English
//
// So on every laptop this product says it is for, the load average rendered as `innerText` "13.44"
// and the trigger count as "4". The component's comment claimed the loss was covered: *"the full
// 'value label' stays on the title tooltip so nothing is lost"*. Measured, all nine metrics dumped
// as `{tag:"DIV", aria:null, role:null, focusable:false}` — a `title` on a non-interactive div is
// not an accessible name, is unreachable by keyboard, and does not exist on touch. That fallback
// WAS the shipped default at every real width, not a graceful degradation.
//
// AFTER, same gateway and same four widths: island unchanged (1052 / 1212 / 1500 / 1692), labels
// painted 9 / 9 / 9 / 9, zero overlapping metric boxes, `document.scrollWidth === innerWidth` at all
// four. At 1280 and 1440 the strip uses its existing `flex-wrap` and takes two rows; the island's
// height does not change (74px before and after — two 20px rows plus `gap-y-s` fit the padding it
// already reserved), so nothing below it moves.
//
// 🪤 WHAT JSDOM CANNOT SEE, AND WHY THESE ASSERTIONS ARE ABOUT CLASSES. The labels were always in
// the DOM with their text — `hidden` is CSS, and jsdom evaluates no container query. A
// `getByText('uptime')` rail would therefore have passed against the broken build too. The
// observable jsdom can reach is the class list: a label must carry no `hidden` and no responsive
// variant that could re-gate it, the container variants having been the whole defect. The painted
// counts above are the browser half of the evidence.

const SYSTEM = {
  platform: 'darwin', hostname: 'mb', os: 'Darwin', python: '3.12', arch: 'arm64', pid: 1, cwd: '/',
  cpu_count: 18, cpu_pct: 22, mem_total_gb: 48, mem_used_gb: 31.8, mem_free_gb: 16.2, proc_mem_mb: 120,
  load_1m: 6.07, load_5m: 5, load_15m: 4, disk_total_gb: 926, disk_free_gb: 623,
  net_rx_kbs: 9728, net_tx_kbs: 10956,
}
const STATUS = {
  uptime: '5m 46s', version: '0.1.3', platform: 'darwin', cron_jobs: 4, subagents: 0,
  update_available: false,
}

function mockApi() {
  vi.doMock('../../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve(STATUS),
      system: () => Promise.resolve(SYSTEM),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ tips: [] }),
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ entries: [] }),
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

/** A metric as the component builds it: an icon, a `title-m` value, and the word-label. */
function metrics(): HTMLElement[] {
  const values = [...document.querySelectorAll<HTMLElement>('span[data-type="title-m"]')]
  return values
    .map((v) => v.parentElement as HTMLElement)
    .filter((d) => d && d.querySelector(':scope > svg') && d.querySelector(':scope > span[data-type="title-m"]'))
}

/** Any responsive utility — container (`@min-[1520px]:inline`, `@3xl:flex`) or viewport (`lg:flex`). */
const RESPONSIVE = /(?:^|\s)(?:@(?:min-\[[^\]]+\]|max-\[[^\]]+\]|[a-z0-9]+)|(?:max-)?(?:sm|md|lg|xl|2xl)):/

async function mountStrip() {
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const { SystemHealth } = await import('./SystemHealth')
  render(
    <DashboardLiveProvider>
      <SystemHealth navigate={vi.fn()} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
    </DashboardLiveProvider>,
  )
  // The live slice arrives asynchronously; without it only uptime/version/triggers/subagents exist.
  await waitFor(() => expect(metrics().length).toBe(9))
  return metrics()
}

describe('every dashboard metric says what it is measuring, at every width', () => {
  beforeEach(mockApi)

  it('renders nine labelled metrics — the positive control for everything below', async () => {
    const found = await mountStrip()
    // Vacuity guard: the class assertions are worthless against an empty strip, and a payload
    // missing `net_rx_kbs`/`disk_total_gb`/`load_1m` silently renders six metrics, not nine.
    expect(found.length, 'nine metrics must render on a full /api/system payload').toBe(9)
    const labels = found.map((m) => m.querySelector('span[data-type="body-m"]')?.textContent)
    expect(labels).toEqual([
      'uptime', 'darwin', 'cpu', 'mem', 'net', 'disk', 'load · 18cpu', 'triggers', 'subagents',
    ])
  })

  it('gates no label behind a width — this is the assertion that fails on the old build', async () => {
    const found = await mountStrip()
    for (const m of found) {
      const label = m.querySelector<HTMLElement>('span[data-type="body-m"]')!
      const classes = label.className.split(/\s+/).filter(Boolean)
      expect(classes, `"${label.textContent}" must not be display:none by default`).not.toContain('hidden')
      // Symmetrical half: the old container variant only mattered because `hidden` preceded it,
      // and a max-width container variant hiding the label would re-open the hole from the other
      // side. 🪤 Do not spell either one out with a placeholder inside the brackets: Tailwind v4
      // scans COMMENTS for candidates, and a bracket it cannot parse compiles to
      // `@container (width < …)`, which fails `vite build` in lightningcss — not vitest, so the
      // rail stays green while the bundle stops building.
      expect(classes.filter((c) => RESPONSIVE.test(c)),
        `"${label.textContent}" must not be gated on a container or viewport width`).toEqual([])
    }
  })

  it('keeps no title tooltip standing in for the label it no longer hides', async () => {
    const found = await mountStrip()
    for (const m of found) {
      // Measured: `{tag:"DIV", aria:null, role:null, focusable:false}` ×9. A tooltip here was never
      // reachable, and with the label visible it only repeated the words already on screen.
      expect(m.getAttribute('title'), `${m.textContent} must not re-state itself in a tooltip`).toBeNull()
      expect(m.getAttribute('aria-label'), 'the visible text is the whole name').toBeNull()
    }
  })

  it('keeps the wrap escape hatch that makes always-on labels safe', async () => {
    const found = await mountStrip()
    const strip = found[0].closest('[class*="flex-wrap"]')
    // 🪤 Without `flex-wrap` the labelled strip has nowhere to go at 1280/1440 — it would either
    // squeeze metrics mid-word or push "Details →" off the island. Measured: 2 rows at both, 1 row
    // from 1728 up, zero overlaps and no horizontal document scroll at any of the four.
    expect(strip, 'the metric strip must stay a wrapping flex row').not.toBeNull()
    expect(found.every((m) => m.className.split(/\s+/).includes('shrink-0')),
      'and each metric stays unsqueezed so the wrap, not the word, gives way').toBe(true)
  })
})
