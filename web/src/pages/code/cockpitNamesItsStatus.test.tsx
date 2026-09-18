/** The Code cockpit names its loop's status, and the blocked explanation is announced (#671).
 *
 *  On a project the API reported as `blocked`, the word "blocked" appeared NOWHERE on the page:
 *
 *      GET /api/loops/712b7824 -> status: 'blocked'
 *      rendered page: /\bblocked\b/i -> false
 *
 *  `CockpitMeta` — whose own comment calls it "the single place to read where the run is" — stated
 *  elapsed, stage, workspace, project and cycles, and never the lifecycle status. The header adds a
 *  Streaming dot, and only while the loop is active. So the one thing that says whether the run is
 *  running, parked or finished was absent from the surface built to answer it, while the Code LIST
 *  one screen back renders a status pill (`CodeSection`'s `statusPill`) — the state was legible
 *  until you opened the thing it described.
 *
 *  The second half was the explanation. The blocked banner had gained a warn tint by the time I
 *  measured (the issue was filed against an earlier main), but no ARIA role — while its immediate
 *  sibling, the `gateFail` banner, is `role="alert"`. Two adjacent banners, one announced.
 *
 *  Both halves go through the ONE registry (`lib/loopStatus`), so this surface cannot become the
 *  fifth loop-status vocabulary — the drift `PP-16` collapsed for four others.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { CodeProject } from '../../lib/api'

vi.mock('../../lib/api', async (importOriginal) => {
  // PARTIAL: the module carries the types and helpers the cockpit imports at module scope, so a
  // factory returning only `api` breaks the import graph before any assertion runs.
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, project: () => Promise.resolve({ id: 'p1', name: 'Bells' }) } }
})

const { CockpitMeta } = await import('./CodeCockpitPage')

const project = (over: Partial<CodeProject> = {}): CodeProject =>
  ({
    id: '712b7824',
    name: 'bell_times tier gap utility',
    status: 'blocked',
    kind: 'code',
    stages: [],
    total_cycles: 8,
    max_cycles: 60,
    elapsed_seconds: 4920,
    ...over,
  }) as CodeProject

describe('the cockpit status strip', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('🔑 names a blocked project as Blocked — the word was on no part of the page', async () => {
    render(<CockpitMeta project={project()} />)
    expect(await screen.findByText('Blocked')).toBeTruthy()
  })

  it('names every state, not just the parked ones', async () => {
    // A running project reads "Running": the stage trail says WHICH stage and the streaming dot
    // says whether the feed is live, and neither answers whether the run is running at all.
    render(<CockpitMeta project={project({ status: 'running' })} />)
    expect(await screen.findByText('Running')).toBeTruthy()
  })

  it('a budget-exhausted finish reads "Ended early", not a green "Completed"', async () => {
    // `effectiveLoopStatus`, the same synthetic every other surface applies: a `complete` project
    // carrying an `error_message` did not finish genuinely, and one word decides whether the user
    // goes and looks.
    render(<CockpitMeta project={project({ status: 'complete', error_message: 'cycle budget reached' })} />)
    expect(await screen.findByText('Ended early')).toBeTruthy()
    expect(screen.queryByText('Completed')).toBeNull()
  })

  it('a genuine completion still reads Completed', async () => {
    // 🪤 The floor for the case above: a fix that always said "Ended early" would pass it.
    render(<CockpitMeta project={project({ status: 'complete', error_message: '' })} />)
    expect(await screen.findByText('Completed')).toBeTruthy()
  })

  it('carries the status TONE, so the state is legible without reading', async () => {
    render(<CockpitMeta project={project()} />)
    const chip = await screen.findByText('Blocked')
    // warn, per the registry's own tone language (warn = stalled or a non-genuine finish).
    expect(chip.getAttribute('style') ?? '').toContain('--color-warn')
  })

  it('🪤 the strip renders for a bare pre-run draft, which used to return null', async () => {
    // The old guard hid the strip when there was "nothing worth a bar" — no workspace, project,
    // cycles or elapsed. A draft's status IS worth a bar, and it is the case where the user most
    // needs to be told the thing has not started.
    render(<CockpitMeta project={project({
      status: 'ready', total_cycles: 0, max_cycles: 0, elapsed_seconds: 0,
      workspace_dir: '', project_id: '', tasks_project_id: '',
    })} />)
    expect(await screen.findByText('Ready')).toBeTruthy()
  })

  it('still states the facts it always stated', async () => {
    // The chip is an addition, not a replacement: a fix that dropped the rest of the strip would
    // pass every assertion above.
    render(<CockpitMeta project={project()} />)
    expect(await screen.findByText('Blocked')).toBeTruthy()
    expect(screen.getByText('8 / 60 cycles')).toBeTruthy()
    expect(screen.getByText(/1h 22m/)).toBeTruthy()
  })
})

// ── the announced explanation, and the one vocabulary ────────────────────────────────────────

const SRC = readFileSync(join(process.cwd(), 'src/pages/code/CodeCockpitPage.tsx'), 'utf8')

/** Source with comment-only lines stripped.
 *
 *  🪤 Learned the hard way on two earlier fixes: the fix's own comment QUOTES the copy it replaced
 *  ("Paused — needs you") to explain what changed, so a raw-text search reports the defect as still
 *  present. A rail that reds on an accurate explanation is worse than no rail. */
const CODE = SRC.split('\n')
  .filter((l) => {
    const t = l.trim()
    return !t.startsWith('//') && !t.startsWith('*') && !t.startsWith('/*')
  })
  .join('\n')

describe('the blocked explanation is reachable', () => {
  it('🔑 the persisted blocked banner carries a role', () => {
    // Asserted on source because the banner lives inside the full cockpit body, which cannot be
    // mounted without the chat socket and the run stream. The relationship that broke is textual:
    // the banner and its role.
    const banner = CODE.slice(CODE.indexOf("project.status === 'blocked' && project.error_message"))
    expect(banner.slice(0, 200)).toMatch(/role="status"/)
  })

  it('role="status", NOT alert — it is persisted, so an assertive role re-interrupts on reload', () => {
    const banner = CODE.slice(CODE.indexOf("project.status === 'blocked' && project.error_message"))
    expect(banner.slice(0, 200)).not.toMatch(/role="alert"/)
  })

  it('the live gate-failure banner keeps its assertive role — the two are different cases', () => {
    // 🪤 The floor: a change that swapped every role for "status" would pass the test above while
    // demoting a banner that fires DURING a run and does need to interrupt.
    const gate = CODE.slice(CODE.indexOf("gateFail && project.status === 'running'"))
    expect(gate.slice(0, 200)).toMatch(/role="alert"/)
  })

  it('the banner calls the state by its registry name, not a fifth word for it', () => {
    // It read "Paused — needs you" for status `blocked`. `paused` is a DIFFERENT status, one the
    // user chose deliberately, and the registry's word for this one is "Blocked".
    expect(CODE).toMatch(/loopStatusLabel\('blocked'\)\s*\}\s*— needs you/)
    expect(CODE).not.toContain('Paused — needs you')
  })

  it('every status word on this page comes from the registry', () => {
    // The invariant that keeps this surface from becoming the vocabulary PP-16 deleted four times.
    // A literal label for a status is what "a fifth table" looks like on arrival.
    for (const word of ['Stalled', 'Needs you', 'Ended early', 'Analyzing']) {
      expect(CODE).not.toContain(`>${word}<`)
    }
    expect(CODE).toContain("from '../../lib/loopStatus'")
  })
})
