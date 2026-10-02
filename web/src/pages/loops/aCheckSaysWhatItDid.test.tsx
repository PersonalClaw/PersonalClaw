import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop, LoopVerdict } from '../../lib/api'

// ── A Verifiable loop's page says what its check did, on every cycle ────────────────────────────
//
// A Verifiable loop is decided by a check the supervisor runs after each cycle. Its page used to
// carry nothing about it: no cycle said whether the check passed, failed or could not run, or in
// which folder it ran, and a check that could not run was a banner shown only to a page that was
// open at that moment, in words about "quality assessment" that are not what a check does. Each
// cycle's check is now a verdict the loop stores (`loop/supervisor._record_check`), and the page
// reads it: the cycle list marks it, the cycle's detail shows the command, its folder, its exit
// code and the end of what it printed, and a cycle that could not be decided says why at the top.

const { STORE } = vi.hoisted(() => ({ STORE: { loop: null as Loop | null } }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => (STORE.loop ? Promise.resolve(STORE.loop) : Promise.reject(new Error('no such loop'))),
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

const WORK = '/home/user/personalclaw-workspace'
const CHECK = 'test -f RELEASE_NOTES.md && wc -l RELEASE_NOTES.md'
const NOT_RUN = `The check could not run in ${WORK}: a program it runs is not installed here (exit 127). The loop keeps going and runs it again after its next cycle.`

function verdict(cycle: number, outcome: 'passed' | 'failed' | 'refused' | 'not_run', over: Partial<LoopVerdict> = {}): LoopVerdict {
  const exit = outcome === 'passed' ? 0 : outcome === 'failed' ? 1 : 127
  return {
    cycle, done: false, regressed: false, verdict: 'RETRY',
    done_reason: outcome === 'failed' ? `The check failed in ${WORK} (exit 1).` : `The check passed in ${WORK}.`,
    check: { command: CHECK, dir: WORK, outcome, exit_code: exit, output: '', not_run: '' },
    ...over,
  } as LoopVerdict
}

function verifiable(verdicts: LoopVerdict[], over: Partial<Loop> = {}): Loop {
  return {
    id: 'g1', kind: 'goal', name: 'Release notes', task: 'Draft the release notes from the changelog',
    execution: 'solo', agent: 'personalclaw-loop', model: '', attended: false, max_cycles: 30,
    idle_secs: 60, success_criteria: null, status: 'running', total_cycles: verdicts.length,
    error_message: null, created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null,
    kind_config: { goal_type: 'verifiable', verify_command: CHECK }, work_dir: WORK,
    findings: verdicts.map((v) => ({ cycle: v.cycle as number, summary: `drafted the notes, pass ${v.cycle}` })),
    verdicts,
    ...over,
  } as Loop
}

async function mount(query: Record<string, string> = {}): Promise<void> {
  render(<LoopCockpitPage id="g1" onBack={() => {}} query={query} setQuery={() => {}} />)
  await waitFor(() => screen.getByRole('button', { name: 'Details' }))
}

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = null
})

describe('a cycle the loop could not decide says why, in its own words', () => {
  it('names what went wrong and what the loop does next', async () => {
    STORE.loop = verifiable([verdict(1, 'not_run', { cannot_judge: NOT_RUN })])
    await mount()
    const said = await screen.findByText(NOT_RUN)
    expect(said.closest('[role="status"]')).not.toBeNull()
  })

  it('stops saying it once a later cycle’s check ran', async () => {
    STORE.loop = verifiable([verdict(1, 'not_run', { cannot_judge: NOT_RUN }), verdict(2, 'failed')])
    await mount()
    expect(screen.queryByText(NOT_RUN)).toBeNull()
  })

  it('is not said about a loop that has ended', async () => {
    STORE.loop = verifiable([verdict(1, 'not_run', { cannot_judge: NOT_RUN })], { status: 'stopped' })
    await mount()
    expect(screen.queryByText(NOT_RUN)).toBeNull()
  })
})

describe('each cycle shows its check', () => {
  it('marks the check’s outcome in the cycle list', async () => {
    STORE.loop = verifiable([
      verdict(1, 'failed'),
      verdict(2, 'passed'),
      verdict(3, 'refused'),
      verdict(4, 'passed', { judge: { outcome: 'no_answer', why: 'its model could not be reached' } }),
      verdict(5, 'passed', { done: true, verdict: 'PASS' }),
    ])
    await mount({ details: '1' })
    expect(await screen.findByRole('img', { name: 'Check failed' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Check passed, goal not met yet' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Check refused' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Check passed, the judge gave no answer' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Check passed' })).toBeTruthy()
  })

  it('shows the command, the folder it ran in, its exit code and what it printed, as text', async () => {
    const printed = '<b>not markup</b>\n0 RELEASE_NOTES.md'
    STORE.loop = verifiable([
      verdict(1, 'failed', { check: { command: CHECK, dir: WORK, outcome: 'failed', exit_code: 1, output: printed, not_run: '' } }),
    ])
    await mount({ details: '1', sel: 'cycle-1' })
    expect(await screen.findByText('Check')).toBeTruthy()
    expect(screen.getByText(`The check failed in ${WORK} (exit 1).`)).toBeTruthy()
    expect(screen.getByText(CHECK)).toBeTruthy()
    expect(screen.getByText((_, el) => el?.tagName === 'P' && el.textContent === `in ${WORK} · exit 1`)).toBeTruthy()
    const output = screen.getByLabelText('What the check printed')
    expect(output.textContent).toBe(printed)
    expect(output.querySelector('b')).toBeNull()
    // A check is not a judge: the cycle has no "Judge verdict" of its own to show.
    expect(screen.queryByText('Judge verdict')).toBeNull()
  })
})
