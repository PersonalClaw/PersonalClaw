import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── The two new config panels write the paths they claim ───────────────────────────────────────
//
// Issues #752 and #2801 are one defect: a config section that is PATCH-editable, read by the
// backend, `_meta`-labelled — and reachable from NO Settings control, so the only way to change it
// is a raw `PATCH /api/config/personalclaw` or a hand edit of `config.json`. #2801 measured the
// class at nine sections / 67 keys and named `workflows` (21) as the largest block.
//
// 🪤 WHY THIS IS A RENDER RAIL AND NOT A GREP. The house source-text form (`readFileSync` +
// `toContain`) proves a STRING exists, never that a user can reach a control: it passes with the
// rendered element deleted. And the specific failure this class invites is not a missing element at
// all — it is a control that renders, flips, and writes the WRONG SECTION PREFIX, because the fastest
// way to write a settings panel is to copy the neighbour. A wrong prefix is a 400 the optimistic UI
// rolls back after the user has already walked away. So every assertion below is on the exact dotted
// path the click sends, in the shape `browserControlToggle.test.tsx` already uses for `browse.*`.
//
// The per-key CENSUS — every allowlisted key in the nine sections has some writer — lives in
// `tests/test_settings_control_coverage.py`, where the allowlist can be imported rather than parsed.

const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))

const CONFIG = {
  workflows: {
    enabled: true,
    self_schedule_max_outstanding: 20,
    max_concurrent_llm_nodes: 4,
    max_concurrent_io_nodes: 2,
    default_node_timeout_total_secs: 900,
    default_node_timeout_stall_secs: 300,
    lease_ttl_secs: 900,
    model_tier_reasoning: 'reasoning',
    model_tier_standard: 'orchestration',
    model_tier_fast: 'background',
    surface_mode_default: 'off',
    match_threshold: 0.62,
    max_materialized_per_foreach: 20,
    retention_per_def: 100,
    confirmation_ttl_secs: 604800,
    default_quiet_windows: '',
    duty_gate_default: '',
    workspace_default_mode: 'scratch',
    workspace_teardown_on_expiry: true,
  },
  loops: {
    judge_use_case: 'reasoning',
    stagnation_window: 5,
    check_work_stages: false,
    worktree_sparse: true,
  },
}

vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => Promise.resolve(CONFIG),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
  },
}))

beforeEach(() => { patchConfig.mockClear() })

describe('Settings › Workflows writes workflows.* (issue #2801, the 21-key block)', () => {
  it('the master switch PATCHes workflows.enabled', async () => {
    const { WorkflowsPanel } = await import('./WorkflowsPanel')
    render(<WorkflowsPanel />)
    const toggle = await screen.findByRole('switch', { name: /workflow engine/i })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    await userEvent.click(toggle)
    expect(patchConfig).toHaveBeenCalledWith('workflows.enabled', false)
  })

  it('a number row PATCHes its own key, with the allowlist bounds on the control', async () => {
    const { WorkflowsPanel } = await import('./WorkflowsPanel')
    render(<WorkflowsPanel />)
    const lane = await screen.findByRole('spinbutton', { name: /lane cap — model calls/i })
    // The bounds must match `_EDITABLE_CONFIG`'s: a control that offers a value the save path
    // refuses is the same defect as no control, one step further along.
    expect(lane).toHaveAttribute('min', '1')
    expect(lane).toHaveAttribute('max', '32')
    // `NumberField` commits on blur or Enter, never per keystroke — so a half-typed number is never
    // sent to a bounds check that would refuse it.
    await userEvent.clear(lane)
    await userEvent.type(lane, '6{Enter}')
    expect(patchConfig).toHaveBeenCalledWith('workflows.max_concurrent_llm_nodes', 6)
  })

  it('the surfacing pills PATCH workflows.surface_mode_default and name their dimension', async () => {
    const { WorkflowsPanel } = await import('./WorkflowsPanel')
    render(<WorkflowsPanel />)
    // `<dimension>: <value>` + aria-pressed is the app's declared form for an exclusive choice —
    // 26 unnamed groups on one page is the measurement that made it required.
    const off = await screen.findByRole('button', { name: 'New workflow surfacing: Off' })
    expect(off).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'New workflow surfacing: Suggest' }))
    expect(patchConfig).toHaveBeenCalledWith('workflows.surface_mode_default', 'suggest')
  })

  it('a model tier is a select over the real use cases, not a free-text box', async () => {
    const { WorkflowsPanel } = await import('./WorkflowsPanel')
    render(<WorkflowsPanel />)
    const tier = await screen.findByRole('combobox', { name: /reasoning tier/i })
    await userEvent.selectOptions(tier, 'background')
    expect(patchConfig).toHaveBeenCalledWith('workflows.model_tier_reasoning', 'background')
  })

  it('a text row commits on Enter — never per keystroke', async () => {
    const { WorkflowsPanel } = await import('./WorkflowsPanel')
    render(<WorkflowsPanel />)
    const quiet = await screen.findByRole('textbox', { name: /default quiet hours/i })
    await userEvent.type(quiet, '22:00-08:00')
    // 🔑 THE POINT OF THE ROW: a per-keystroke PATCH would have sent eleven requests by now, and
    // `22:0` would have been REFUSED mid-typing — an error toast for a value not yet finished.
    expect(patchConfig).not.toHaveBeenCalled()
    await userEvent.type(quiet, '{Enter}')
    expect(patchConfig).toHaveBeenCalledWith('workflows.default_quiet_windows', '22:00-08:00')
  })
})

describe('Settings › Autonomous loops writes loops.* (issue #2801)', () => {
  it('the judge axis is a select over the enum the allowlist declares', async () => {
    const { LoopsPanel } = await import('./LoopsPanel')
    render(<LoopsPanel />)
    const judge = await screen.findByRole('combobox', { name: /judge model axis/i })
    // The six values are `_EDITABLE_CONFIG`'s enum for this key, so a value the save path refuses
    // cannot be entered at all — which is what a select buys over the free-text box the issue's
    // "intended solution" explicitly ruled out.
    expect([...judge.querySelectorAll('option')].map((o) => o.getAttribute('value')))
      .toEqual(['reasoning', 'chat', 'code_tools', 'background', 'orchestration', 'loops'])
    await userEvent.selectOptions(judge, 'loops')
    expect(patchConfig).toHaveBeenCalledWith('loops.judge_use_case', 'loops')
  })

  it('the stagnation window floors at 2 — a window of 1 compares a cycle with itself', async () => {
    const { LoopsPanel } = await import('./LoopsPanel')
    render(<LoopsPanel />)
    const win = await screen.findByRole('spinbutton', { name: /stagnation window/i })
    expect(win).toHaveAttribute('min', '2')
    expect(win).toHaveAttribute('max', '50')
  })

  it('both loop booleans PATCH their own key', async () => {
    const { LoopsPanel } = await import('./LoopsPanel')
    render(<LoopsPanel />)
    const check = await screen.findByRole('switch', { name: /check work after stage gates/i })
    await userEvent.click(check)
    expect(patchConfig).toHaveBeenCalledWith('loops.check_work_stages', true)
    const sparse = screen.getByRole('switch', { name: /sparse task worktrees/i })
    await userEvent.click(sparse)
    expect(patchConfig).toHaveBeenCalledWith('loops.worktree_sparse', false)
  })
})
