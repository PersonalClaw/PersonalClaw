import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// ── The `feedback.*` controls, plus what a config panel does when a write or a read FAILS ───────
//
// #752 (`feedback.*`, 4 keys) and #2801 (`loops.*`, 4 keys) are the same defect from the same
// census: nine sections, 67 of the `_EDITABLE_CONFIG` keys, with no `patchConfig` writer of either
// form. Both sections were complete on the backend — dataclass, `_meta`, `load()`, `to_dict()`,
// PATCH allowlist and bounds — so the ONLY missing wiring point was the control. `feedback.enabled`
// is the sharpest case: a kill switch with a rail on both sides (every `/api/feedback` route 404s
// when it is off, and `FeedbackThumbs` self-hides on the first 404), and no way to throw it.
//
// 🔑 THE ASSERTION IS THE CONFIG PATH, NOT "a control exists" — a panel can render four pretty
// controls wired to nothing, or to the wrong key, and a render-only test sees neither.
//
// 🪤 WHY THIS FILE EXISTS BESIDE `configSectionControls.test.tsx`, WHICH ALSO MOUNTS THESE PANELS.
// The two ask different questions and need opposite harnesses. That file drives the REAL `useQuery`
// so its claim ("this control PATCHes this dotted path with these bounds") is made against the real
// read path; it therefore cannot exhibit a FAILED read, because the query it would have to fail is
// the one it depends on. This file mocks `useQuery`, which is the only way to hand a panel a
// rejection and a resolved sibling read at the same time — the split-failure posture below. Loops'
// path-and-bounds coverage is NOT duplicated here; only its save/read posture is.
//
// The per-key CENSUS — every allowlisted key in the nine sections has some writer — lives in
// `tests/test_settings_control_coverage.py`, where the allowlist can be imported rather than parsed.

const patchConfig = vi.fn()
const notify = vi.fn()
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))

const useQuery = vi.fn()
vi.mock('../../lib/data', () => ({
  useQuery: (...a: unknown[]) => useQuery(...a),
  invalidateKeys: vi.fn(),
}))
vi.mock('../../lib/api', async (orig) => {
  const real = (await orig()) as { api: Record<string, unknown> }
  return {
    ...real,
    api: { ...real.api, patchConfig: (...a: unknown[]) => patchConfig(...a) },
  }
})

const { LoopsPanel } = await import('./LoopsPanel')
const { FeedbackPanel } = await import('./FeedbackPanel')

/** Every `loops.*` field at its declared default (`config/learning.py` LoopsConfig). */
const LOOPS = {
  judge_use_case: 'reasoning',
  stagnation_window: 5,
  check_work_stages: false,
  worktree_sparse: true,
}

/** Every `feedback.*` field at its declared default (`config/learning.py` FeedbackConfig). */
const FEEDBACK = { enabled: true, retire_threshold: 0.4, min_n: 5, window_days: 90 }

/** A resolved `useQuery` result.
 *
 * 🪤 CALL THIS ONCE PER MOUNT AND REUSE THE OBJECT. Both panels seed editable form state with
 * `useEffect(() => { if (data) setCfg(data) }, [data])`, so `data`'s IDENTITY is the dependency. A
 * `mockImplementation` that built a fresh result on every call handed every render a new object,
 * fired the effect again, and rendered forever — measured as a vitest run that sat at 42 minutes on
 * two files with no output rather than failing. The real `useQuery` returns a stable object across
 * renders (which is why `LegibilityPanel` has used this pattern safely for five panels), so the
 * loop was the harness, not the component; a mock that is less stable than the thing it stands in
 * for can only manufacture a defect. */
const ok = (data: unknown) => ({ data, loading: false, error: null, refresh: vi.fn(), stale: false })

/** Type into a `NumberRow` and COMMIT. `ui/forms.NumberField` holds the keystrokes in local state
 *  and fires `onChange` on blur (or Enter) — so `fireEvent.change` alone sets the text and saves
 *  nothing. Same two-step `updatesPanelControls` uses on `updates.check_interval_hours`. */
const commitNumber = (label: string, value: string) => {
  const input = screen.getByLabelText(label)
  fireEvent.change(input, { target: { value } })
  fireEvent.blur(input)
  return input
}

beforeEach(() => {
  patchConfig.mockReset(); patchConfig.mockResolvedValue({})
  notify.mockReset()
  useQuery.mockReset()
})

describe('every feedback control round-trips to its own config field (#752)', () => {
  // The panel holds two independent reads — the producers table and the config section. Keyed
  // dispatch rather than one return value, because a single mock would make the config section
  // render the producers payload.
  const mount = (over: Record<string, unknown> = {}, cfgFails = false) => {
    // Both results are built ONCE, outside the implementation, so each key returns the same object
    // on every render — see `ok`'s note on why an unstable result renders forever here.
    const cfgResult = cfgFails
      ? { data: undefined, loading: false, error: new Error('gateway down'), refresh: vi.fn() }
      : ok({ ...FEEDBACK, ...over })
    const producersResult = ok({ producers: [], window_days: 90, min_n: 5 })
    useQuery.mockImplementation((key: unknown) =>
      key === 'settings:feedback' ? cfgResult : producersResult)
    return render(<FeedbackPanel />)
  }

  it('the collect toggle writes feedback.enabled', async () => {
    mount({ enabled: true })
    fireEvent.click(screen.getByRole('switch', { name: /Collect feedback/i }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('feedback.enabled', false))
  })

  it('the retire threshold writes feedback.retire_threshold at a FRACTIONAL step', async () => {
    // Step matters here: `retire_threshold` is a rate in 0.1…0.9, and at the default step of 1 the
    // only reachable values are 0 and 1 — a control that cannot express the value it displays.
    mount()
    const input = commitNumber('Retire threshold', '0.55')
    expect(input.getAttribute('step')).toBe('0.05')
    expect(input.getAttribute('min')).toBe('0.1')
    expect(input.getAttribute('max')).toBe('0.9')
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('feedback.retire_threshold', 0.55))
  })

  it('minimum verdicts writes feedback.min_n within its bounds', async () => {
    mount()
    const input = commitNumber('Minimum verdicts', '12')
    expect(input.getAttribute('min')).toBe('3')
    expect(input.getAttribute('max')).toBe('50')
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('feedback.min_n', 12))
  })

  it('the attribution window writes feedback.window_days within its bounds', async () => {
    mount()
    const input = commitNumber('Attribution window (days)', '30')
    expect(input.getAttribute('min')).toBe('7')
    expect(input.getAttribute('max')).toBe('365')
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('feedback.window_days', 30))
  })

  it('a failed CONFIG read kills only the control section, not the producers table', () => {
    // The two reads answer different questions and fail independently. The config read backs
    // controls that assert saved state, so it must surface; the producers read is tolerant so one
    // dead subsystem does not take the panel down. Collapsing them either way loses one guarantee.
    mount({}, true)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.getByText(/Judgment sources/i)).toBeTruthy()
  })
})

describe('a config panel that cannot save, or cannot read, says so', () => {
  const mountLoops = (over: Record<string, unknown> = {}) => {
    useQuery.mockReturnValue(ok({ ...LOOPS, ...over }))
    return render(<LoopsPanel />)
  }

  it('a rejected save rolls back and NAMES the control, not its config key', async () => {
    patchConfig.mockRejectedValue(new Error('must be between 2 and 50'))
    mountLoops({ check_work_stages: false })
    const sw = screen.getByRole('switch', { name: /Check work after stage gates/i })
    fireEvent.click(sw)
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(String(notify.mock.calls[0][0])).toContain('Check work after stage gates')
    expect(String(notify.mock.calls[0][0])).not.toContain('check_work_stages')
    // Rolled back: a swallowed rejection would leave the switch showing a state nothing saved.
    expect(sw.getAttribute('aria-checked')).toBe('false')
  })

  it('a failed config read shows a retryable error and NO control', () => {
    useQuery.mockReturnValue({ data: undefined, loading: false, error: new Error('gateway down'), refresh: vi.fn() })
    render(<LoopsPanel />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
  })
})
