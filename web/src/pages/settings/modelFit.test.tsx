import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { api, type AvailableModel, type DownloadJob, type HostModelFit } from '../../lib/api'
import { LocalModelManager } from './LocalModelManager'
import { budgetKnown, filterByFit, statedSizeMb, unrunnable } from './modelFit'

// ── "Will it run on THIS machine?" must never become "we could not tell, so we deleted it" ────────
//
// LMMV-8 gives every local model row a fit verdict and gives the catalog a filter that hides the
// ones the device cannot run. Two ways for that to go wrong, and they are not symmetric:
//
//   · Showing a model that turns out not to fit  → the download fails or swaps, and the user learns
//     something. Recoverable.
//   · HIDING models because the host could not be measured → the provider's catalog silently reads
//     as empty, the user concludes the provider is broken, and nothing on screen says otherwise.
//     There is no recovery from a list that never mentions what it removed.
//
// So the load-bearing assertion in this file is the third describe block: a `hide_unrunnable: true`
// preference with a `budget_mb: null` host must remove ZERO rows. It is asserted by COUNTING rows
// before and after, because an equal-length list is the only evidence that survives a refactor of
// the filter's internals.
//
// 🔑 THE VACUITY FLOOR IS THE SECOND BLOCK. If the filter never hid anything under ANY conditions,
// every "hides nothing" assertion would pass for free. So the measured-host case proves the filter
// really does remove the red row first; only then does the unmeasured case mean anything.

// Real shapes, not casts — a fixture that drifts from the wire type is how a contract test stops
// testing the contract.
const HOST_MEASURED: HostModelFit = {
  budget_mb: 6000, total_ram_mb: 16384, unified_memory: true,
  gpu_model: 'Apple M2', measured: true, hide_unrunnable: true,
}
// The dangerous host: the user's preference says "hide", and we know NOTHING about the machine.
// `budget_mb` is null and `measured` is false — either one alone must be enough to disarm the
// filter. Note 0 is NOT one of these: a measured 0 means "nothing fits", a real answer the filter
// must act on, and the block below asserts the two cases disagree.
const HOST_UNMEASURED: HostModelFit = {
  budget_mb: null, total_ram_mb: 0, unified_memory: false,
  gpu_model: '', measured: false, hide_unrunnable: true,
}

const ROWS: AvailableModel[] = [
  {
    id: 'tiny', name: 'tiny', capabilities: ['chat'], provider: 'ollama', provider_type: 'ollama',
    size_mb: 800, downloaded: false, fit: 'green', fit_reason: 'about 1.2 GB of 6.0 GB headroom',
    fit_need_mb: 1200,
  },
  {
    id: 'snug', name: 'snug', capabilities: ['chat'], provider: 'ollama', provider_type: 'ollama',
    size_mb: 4200, downloaded: false, fit: 'yellow', fit_reason: 'needs most of the 6.0 GB budget',
    fit_need_mb: 5400,
  },
  {
    id: 'huge', name: 'huge', capabilities: ['chat'], provider: 'ollama', provider_type: 'ollama',
    size_mb: 8000, downloaded: false, fit: 'red', fit_reason: 'needs 9.5 GB, budget is 6.0 GB',
    fit_need_mb: 9500,
  },
  {
    id: 'murky', name: 'murky', capabilities: ['chat'], provider: 'ollama', provider_type: 'ollama',
    size_mb: 2000, downloaded: false, fit: 'unknown', fit_reason: 'no size published for this tag',
  },
  // No `fit` key at all — the shape a NON-LOCAL row has. Distinct from `fit: 'unknown'`: the
  // question does not apply, so the answer is no chip rather than an "unknown" one.
  {
    id: 'unannotated', name: 'unannotated', capabilities: ['chat'], provider: 'ollama',
    provider_type: 'ollama', size_mb: 500, downloaded: false,
  },
]

/** `api.modelsAvailable` denormalizes the response's top-level `fit` onto every row; this mirrors
 *  it so the component is driven by exactly the props the real fetch produces. */
const withHost = (host?: HostModelFit): AvailableModel[] =>
  ROWS.map((m) => (host ? { ...m, host_fit: host } : m))

const NO_JOBS: DownloadJob[] = []

beforeEach(() => {
  // EventSource does not exist in jsdom; `useModelDownloads` only needs it to not throw.
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = class {
    close() {}
    addEventListener() {}
    onerror: unknown = null
  }
  vi.spyOn(api, 'modelDownloads').mockResolvedValue(NO_JOBS)
  vi.spyOn(api, 'downloadStreamUrl').mockReturnValue('/api/models/downloads/x/stream')
})
afterEach(() => vi.restoreAllMocks())

const mount = (models: AvailableModel[]) =>
  render(<LocalModelManager provider="ollama" models={models} onChanged={vi.fn()} />)

/** One row renders exactly one action button, and every fixture is undownloaded, so the
 *  "Download …" buttons ARE the row count. Counting rendered rows (not filter internals) is what
 *  makes the hides-nothing clause resistant to a rewrite of the filter. */
const rowCount = () => screen.getAllByRole('button', { name: /^Download / }).length

/** The fit chips, by this repo's declared form for a state-carrying chip: `role="img"` with the
 *  verdict + reason as its accessible name (see `design/ariaProhibitedAttr`). */
const chips = () => screen.queryAllByRole('img')

describe('the fit chip', () => {
  it('renders one chip per verdict, named by the reason and not by its colour', () => {
    mount(withHost(HOST_MEASURED))
    // `showAll` starts by following the host preference, which is `hide_unrunnable: true`, so the
    // red row is filtered out here — it gets its own assertion below, off a shown list.
    expect(screen.getByRole('img', { name: /about 1\.2 GB of 6\.0 GB headroom/ })).toBeTruthy()
    expect(screen.getByRole('img', { name: /needs most of the 6\.0 GB budget/ })).toBeTruthy()
    expect(screen.getByRole('img', { name: /no size published for this tag/ })).toBeTruthy()
  })

  it("leads the accessible name with the verdict, so the name is not only the backend's sentence", () => {
    mount(withHost(HOST_MEASURED))
    expect(screen.getByRole('img', { name: /^Fits — / })).toBeTruthy()
    expect(screen.getByRole('img', { name: /^Tight — / })).toBeTruthy()
    expect(screen.getByRole('img', { name: /^Fit unknown — / })).toBeTruthy()
  })

  it('carries the reason in aria-label, not only in title', () => {
    // 🪤 FOUND BY FALSIFICATION. Deleting `aria-label` from the chip left every `getByRole('img',
    // { name })` query above GREEN, because `title` is the last-resort step of the accessible-name
    // algorithm and jsdom honours it. So the name assertions alone cannot tell the declared form
    // (`role="img"` + `aria-label`, per `design/ariaProhibitedAttr`) from a title-only chip whose
    // name several real AT/engine combinations do not expose at all.
    mount(withHost(HOST_MEASURED))
    for (const chip of chips()) {
      expect(chip.getAttribute('aria-label') ?? '', chip.textContent ?? '').toMatch(/\S/)
    }
    expect(chips().length).toBeGreaterThan(0) // vacuity floor for the loop above
  })

  it('renders NO chip for a row with no fit field, and does not invent an "unknown" one', () => {
    // Unmeasured host: nothing is hidden, so all five rows are on screen and the chip count is a
    // clean statement about which rows earned one.
    mount(withHost(HOST_UNMEASURED))
    expect(rowCount(), 'all five rows shown').toBe(5)
    // Four rows carry a `fit`; `unannotated` does not.
    expect(chips().length, 'one chip per fit-bearing row, none for the bare row').toBe(4)
    const names = chips().map((c) => c.getAttribute('aria-label') ?? '')
    expect(names.some((n) => /unannotated/.test(n)), 'no chip mentions the unannotated row').toBe(false)
  })

  it("shows the backend's red verdict even on an unmeasured host — it just refuses to ACT on it", () => {
    mount(withHost(HOST_UNMEASURED))
    expect(screen.getByRole('img', { name: /needs 9\.5 GB, budget is 6\.0 GB/ })).toBeTruthy()
  })
})

describe('the browse filter DOES hide — the vacuity floor for everything below', () => {
  it('a measured host honouring hide_unrunnable removes the red row and says how many', () => {
    mount(withHost(HOST_MEASURED))
    expect(rowCount(), 'the red row is gone').toBe(4)
    expect(screen.queryByRole('button', { name: 'Download huge' })).toBeNull()
    // A silently short list reads as "this provider has no models", so the count is on screen.
    expect(screen.getByText('1 hidden')).toBeTruthy()
  })

  it('offers the switch as the way back, and shows the row again when turned off', () => {
    mount(withHost(HOST_MEASURED))
    const sw = screen.getByRole('switch', { name: /Hide models this device can't run/ })
    expect(sw.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(sw)
    expect(rowCount(), 'all five rows return').toBe(5)
    expect(screen.getByRole('button', { name: 'Download huge' })).toBeTruthy()
  })

  it('a measured host whose preference is OFF hides nothing but still names the count', () => {
    mount(withHost({ ...HOST_MEASURED, hide_unrunnable: false }))
    expect(rowCount()).toBe(5)
    expect(screen.getByText("1 won't fit")).toBeTruthy()
  })
})

describe('an unknown or unmeasured budget hides NOTHING', () => {
  // The whole point of the atom. Each case pairs a `hide_unrunnable: true` preference with a host
  // we could not measure, and asserts the rendered row count is untouched.
  const shown = (host?: HostModelFit) => { mount(withHost(host)); return rowCount() }

  it('measured: false hides nothing', () => {
    expect(shown(HOST_UNMEASURED)).toBe(ROWS.length)
  })

  it('a null budget_mb hides nothing, even when the host claims to be measured', () => {
    expect(shown({ ...HOST_MEASURED, budget_mb: null })).toBe(ROWS.length)
  })

  it('a MEASURED budget of 0 hides the unrunnable rows — 0 is "nothing fits", not "unknown"', () => {
    // 0 is not a leak: `usable_memory_bytes` returns it, distinctly from None, for a machine
    // smaller than the reserve. Collapsing it into "unknown" disabled the filter on exactly the
    // machines that need it — driving a 48 GB host with the reserve at 64 GB chipped all six
    // models "Won't fit" and then hid none of them.
    expect(shown({ ...HOST_MEASURED, budget_mb: 0 })).toBeLessThan(ROWS.length)
  })

  it('a measured 0 and an unknown budget give OPPOSITE answers', () => {
    // Asserted on the pure functions so each case is independent of the render helper. If these
    // ever agree, the budget's MAGNITUDE has been made to carry the meaning `measured` carries.
    expect(budgetKnown({ ...HOST_MEASURED, budget_mb: 0 })).toBe(true)
    expect(budgetKnown({ ...HOST_MEASURED, budget_mb: null })).toBe(false)
    const hiddenAtZero = unrunnable(ROWS, { ...HOST_MEASURED, budget_mb: 0 })
    const hiddenAtUnknown = unrunnable(ROWS, { ...HOST_MEASURED, budget_mb: null })
    expect(hiddenAtZero.length).toBeGreaterThan(0)
    expect(hiddenAtUnknown).toEqual([])
  })

  it('no host fit object at all hides nothing', () => {
    expect(shown(undefined)).toBe(ROWS.length)
  })

  it('does not even offer the filter when it provably cannot hide anything', () => {
    mount(withHost(HOST_UNMEASURED))
    expect(screen.queryByRole('switch'), 'a control that cannot act must not be shown').toBeNull()
  })

  it('and the pure helper agrees, including on array identity', () => {
    const rows = withHost(HOST_UNMEASURED)
    expect(budgetKnown(HOST_UNMEASURED)).toBe(false)
    expect(budgetKnown(HOST_MEASURED)).toBe(true)
    // Same array back, not merely the same length — the "no filtering happened" case is total.
    expect(filterByFit(rows, HOST_UNMEASURED, true)).toBe(rows)
    expect(filterByFit(rows, undefined, true)).toBe(rows)
    expect(filterByFit(rows, HOST_MEASURED, true)).toHaveLength(4)
  })
})

describe('hiding everything still explains itself', () => {
  it('an emptied catalog says the rows are hidden, not that the provider has none', () => {
    const onlyRed = ROWS.filter((m) => m.fit === 'red').map((m) => ({ ...m, host_fit: HOST_MEASURED }))
    mount(onlyRed)
    expect(screen.queryByRole('button', { name: /^Download / }), 'nothing left to show').toBeNull()
    expect(screen.getByText(/The only listed model is hidden/)).toBeTruthy()
    expect(screen.queryByText('No downloadable models listed.'), 'never this lie').toBeNull()
    // And the way back is still on screen.
    expect(screen.getByRole('switch', { name: /Hide models this device can't run/ })).toBeTruthy()
  })
})

describe('the row states its OWN size — the number its verdict was judged on', () => {
  // `quoted_size_mb` is the family MEDIAN; the verdict is judged on the row's own `size_mb`. So a
  // tagged row printing the quote would sit a 6000 MB number beside a chip computed from 16000 MB
  // — self-contradictory, and worse than showing no chip. The quote is still shown, LABELLED.
  it('states its own size and labels the family median as the family’s', () => {
    expect(statedSizeMb({ ...ROWS[2], quoted_size_mb: 6000 }))
      .toEqual({ mb: 8000, familyMedianMb: 6000 })
  })

  it('states the row size alone when there is no quote', () => {
    expect(statedSizeMb(ROWS[0])).toEqual({ mb: 800, familyMedianMb: null })
  })

  it('does not repeat the number when the quote equals the row size', () => {
    expect(statedSizeMb({ ...ROWS[0], quoted_size_mb: 800 })).toEqual({ mb: 800, familyMedianMb: null })
  })

  it('falls back to the quote for a row that publishes no size of its own', () => {
    // Then the quote IS what the backend judged, so there is only one number and no label needed.
    const { size_mb: _drop, ...noSize } = ROWS[0]
    expect(statedSizeMb({ ...noSize, quoted_size_mb: 6000 }))
      .toEqual({ mb: 6000, familyMedianMb: null })
  })

  it('rounds the wire floats', () => {
    expect(statedSizeMb({ ...ROWS[0], size_mb: 800.4, quoted_size_mb: 6000.6 }))
      .toEqual({ mb: 800, familyMedianMb: 6001 })
  })

  it('renders both numbers on the row, with the quote attributed to the family', () => {
    mount([{ ...ROWS[2], quoted_size_mb: 6000, host_fit: { ...HOST_MEASURED, hide_unrunnable: false } }])
    expect(screen.getByText(/· 8000 MB · family median ~6000 MB/)).toBeTruthy()
  })
})

describe('a red row offers the step-down variant instead of only refusing', () => {
  const RED_WITH_STEP: AvailableModel = {
    ...ROWS[2], fit_step_down: 'qwen3:6b',
    host_fit: { ...HOST_MEASURED, hide_unrunnable: false },
  }

  it('names the variant that fits, and downloading it starts THAT job — not a silent substitution', async () => {
    const start = vi.spyOn(api, 'startModelDownload').mockResolvedValue({ id: 'j1' } as never)
    mount([RED_WITH_STEP])
    const offer = screen.getByRole('button', { name: /Download qwen3:6b instead/ })
    fireEvent.click(offer)
    // The offer is the UI's; the request carries the substitute's OWN name, so the job, its stream
    // key and its byte progress all describe the model that will actually arrive.
    // RTL's `waitFor`, not vitest's: the download hook's post-resolve `setJobs` is a React state
    // update, and only the RTL wrapper flushes it inside `act`.
    await waitFor(() => expect(start).toHaveBeenCalledWith('ollama', 'qwen3:6b'))
  })

  it('leaves the row’s own download reachable — the verdict is advice, not a block', () => {
    mount([RED_WITH_STEP])
    const own = screen.getByRole('button', { name: 'Download huge' })
    expect(own.hasAttribute('disabled')).toBe(false)
  })

  it('offers nothing when the backend named no step-down, and never on a non-red row', () => {
    mount([{ ...ROWS[2], host_fit: { ...HOST_MEASURED, hide_unrunnable: false } }])
    expect(screen.queryByRole('button', { name: /instead/ })).toBeNull()
    // A green row carrying a stale step-down name must not advertise it either.
    mount([{ ...ROWS[0], fit_step_down: 'qwen3:6b', host_fit: HOST_MEASURED }])
    expect(screen.queryByRole('button', { name: /instead/ })).toBeNull()
  })
})
