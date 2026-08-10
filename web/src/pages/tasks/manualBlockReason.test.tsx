import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { TaskDetail } from './TaskDetail'
import { blockKindMeta } from './taskMeta'
import type { TaskItem } from '../../lib/api'

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: { updateTask: vi.fn(), deleteTask: vi.fn(), taskComments: () => Promise.resolve([]), taskNotes: () => Promise.resolve([]) },
}))

// ── A manually-blocked task explained itself NOWHERE ───────────────────────────
//
// `blocked_reason_kind` ("" | "auto" | "manual") is written by the reconciler and read by nothing.
// The two kinds are not cosmetic — they behave differently:
//
//   auto    an unfinished prerequisite the reconciler tracks; clears ITSELF when that prerequisite
//           reaches a terminal status.
//   manual  a person blocked it for a reason outside the graph. `reconcile_blocked_status`
//           explicitly `continue`s on it — "never auto-touch a manual block" — so it sits there
//           until someone unblocks it by hand.
//
// The bug is worse than a missing label, and it comes from the GATE. `block_reason` is derived
// PURELY from unfinished prerequisites, so a manual block has none and it reports:
//
//     {is_blocked: false, blocking_task_ids: [], blocking_task_titles: [], message: ""}
//
// The panel was gated on `is_blocked`, so it rendered NOTHING. The task showed status "Blocked"
// with no explanation on any surface, and no hint that only a human could clear it. Measured on the
// real validation store: 2 blocked tasks — the `auto` one explained itself, the `manual` one did not.
//
// Third cycle running where an unread wire field led to a render-gate bug worth more than the field
// (see the runtime-counters, health-score and MCP-pool changes below it in this stack).

const base: TaskItem = { id: 't1', title: 'Rebuild the stall layout', status: 'blocked' }

const AUTO: TaskItem = {
  ...base,
  blocked_reason_kind: 'auto',
  block_reason: {
    is_blocked: true,
    blocking_task_ids: ['t9'],
    blocking_task_titles: ['Deliver the gallery'],
    message: 'Waiting on: Deliver the gallery',
  },
}

/** The real shape of a manual block: kind stamped, block_reason EMPTY. */
const MANUAL: TaskItem = {
  ...base,
  blocked_reason_kind: 'manual',
  block_reason: { is_blocked: false, blocking_task_ids: [], blocking_task_titles: [], message: '' },
}

const mount = (task: TaskItem) => render(
  <TaskDetail task={task} onSaved={() => {}} onDeleted={() => {}} editing={false} onEditingChange={() => {}} />,
)

describe('blockKindMeta', () => {
  it('distinguishes a self-clearing block from one only a person can clear', () => {
    expect(blockKindMeta('auto')?.label).toBe('Waiting on a prerequisite')
    expect(blockKindMeta('manual')?.label).toBe('Blocked by you')
  })

  it('says of an auto block that it unblocks itself', () => {
    // The actionable difference: the user does nothing and it resolves.
    expect(blockKindMeta('auto')?.hint).toMatch(/unblocks itself/i)
  })

  it('says of a manual block that it waits for the user', () => {
    expect(blockKindMeta('manual')?.hint).toMatch(/until you unblock it/i)
  })

  it('returns null for an unstamped or unknown kind', () => {
    // A legacy payload with no kind must fall back to `block_reason`, not invent a reason.
    expect(blockKindMeta('')).toBeNull()
    expect(blockKindMeta(undefined)).toBeNull()
    expect(blockKindMeta('something_new')).toBeNull()
  })
})

describe('a manually-blocked task explains itself', () => {
  it('renders the blocked panel even though is_blocked is false', () => {
    // THE defect: the old gate hid this panel entirely for every manual block.
    const text = mount(MANUAL).container.textContent ?? ''
    expect(text).toContain('Blocked by you')
  })

  it('states that it stays blocked until the user acts', () => {
    expect(mount(MANUAL).container.textContent).toMatch(/until you unblock it/i)
  })

  it('never renders a bare "Waiting on" with nothing after it', () => {
    // The old fallback was `Waiting on ${titles.join(', ')}` with an EMPTY list — the string a
    // manual block would have produced had the panel rendered at all.
    const text = mount(MANUAL).container.textContent ?? ''
    expect(text).not.toMatch(/Waiting on\s*$/)
    expect(text).not.toContain('Waiting on ,')
  })
})

describe('an auto-blocked task keeps naming its prerequisite', () => {
  it('still shows which task it is waiting on', () => {
    // The regression guard: the new kind label must not displace the prerequisite titles, which are
    // the only thing identifying WHAT to go finish.
    expect(mount(AUTO).container.textContent).toContain('Deliver the gallery')
  })

  it('labels it as waiting on a prerequisite rather than the generic word', () => {
    expect(mount(AUTO).container.textContent).toContain('Waiting on a prerequisite')
  })
})

describe('the panel stays honest in the edge cases', () => {
  it('renders no blocked panel for a task that is not blocked', () => {
    const text = mount({ ...base, status: 'open', blocked_reason_kind: 'manual' }).container.textContent ?? ''
    // A stale kind on a reopened task must not keep explaining a block that no longer exists.
    expect(text).not.toContain('Blocked by you')
    expect(text).not.toContain('Waiting on a prerequisite')
  })

  it('falls back to block_reason when no kind is stamped', () => {
    // Legacy payload: kind absent, block_reason populated. The old behaviour, preserved.
    const text = mount({ ...AUTO, blocked_reason_kind: undefined }).container.textContent ?? ''
    expect(text).toContain('Blocked')
    expect(text).toContain('Deliver the gallery')
  })

  it('renders nothing when neither the kind nor block_reason says blocked', () => {
    const text = mount({ ...base, status: 'blocked' }).container.textContent ?? ''
    // No kind, no block_reason — nothing to claim, so claim nothing.
    expect(text).not.toContain('Blocked by you')
    expect(text).not.toMatch(/Waiting on/)
  })
})
