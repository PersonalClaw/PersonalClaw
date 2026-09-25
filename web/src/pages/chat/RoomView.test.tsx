/**
 * `AGENT-ROOMS` AR-8 — the room surface, driven.
 *
 * The clause is "a room renders in the web/ UI showing attributed member messages, the round-budget
 * pause card, and per-member status, without inventing a second chat UI". These tests drive the
 * surface with a mocked wire and read what a user would see:
 *
 *  · the three voices render as three different things, and two members in a row stay two blocks;
 *  · the pause card appears only when the room is paused, and clears when it is not;
 *  · sending posts to the ROOM route and reports the queue the backend built;
 *  · the feature being switched OFF is not a failed read (`rooms_disabled` is a deliberate 403 on
 *    the reads too), and a room that is gone is an empty state rather than an error.
 *
 * `vi.doMock` + `vi.resetModules()` + a dynamic import of the subject is this repo's shape for
 * mocking `lib/api` (see `triggers/triggersLoadError.test.tsx`).
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError, type RoomDetail, type RoomMemberRecord, type RoomRecord } from '../../lib/api'
import { RoomView } from './RoomView'

/** The mock's mutable handles. `vi.hoisted` because `vi.mock`'s factory runs during this file's own
 *  top-level imports, before a plain `const` would be initialised. */
const H = vi.hoisted(() => ({
  postRoomMessage: vi.fn(),
  archiveRoom: vi.fn(),
  addRoomMember: vi.fn(),
  room: { fn: null as null | (() => Promise<unknown>) },
  // Indirected like `room` so a test can make the AGENT read reject. It used to be a fixed
  // `{ agents: [] }`, which meant no test could reach the failed-read branch of the member picker.
  agents: { fn: null as null | (() => Promise<unknown>) },
}))

vi.mock('../../lib/api', async () => {
  const real = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  return {
    ...real,
    api: {
      ...real.api,
      room: () => H.room.fn!(),
      agents: () => H.agents.fn!(),
      postRoomMessage: H.postRoomMessage,
      archiveRoom: H.archiveRoom,
      addRoomMember: H.addRoomMember,
      removeRoomMember: vi.fn(),
      setRoomRoundBudget: vi.fn(),
    },
  }
})

// jsdom implements no layout and therefore no `scrollIntoView`; the surface's follow-the-stream
// effect calls it on every new line. Stubbed here rather than in the shared setup, because it is
// this file's dependency and the shared file's stubs are the app-wide ones.
beforeAll(() => { Element.prototype.scrollIntoView = vi.fn() })

function member(name: string, extra: Partial<RoomMemberRecord> = {}): RoomMemberRecord {
  return { name, role_blurb: '', listen_policy: 'all', profile_narrowing: {}, ...extra }
}

function room(extra: Partial<RoomRecord> = {}): RoomRecord {
  return {
    id: 'pricing-debate',
    title: 'Should we raise prices?',
    created_at: '2026-09-23T00:00:00',
    archived: false,
    paused: false,
    rounds_used: 0,
    round_budget: 0,
    pending_queue: [],
    members: [member('analyst', { role_blurb: 'argues from the numbers' }), member('skeptic')],
    effective_round_budget: 6,
    max_round_budget: 100,
    max_members: 8,
    transcript_path: '/rooms/pricing-debate/transcript.jsonl',
    ...extra,
  }
}

function detail(extra: Partial<RoomDetail> = {}): RoomDetail {
  const r = extra.room ?? room()
  return {
    room: r,
    member_posture: r.members.map((m) => ({
      name: m.name, declared: {}, approval: 'ask', tool_grants: 'read', tool_allowlist: [],
    })),
    member_bindings: r.members.map((m) => ({
      name: m.name, configured: true, model: 'gemma4:12b', provider: 'native',
    })),
    messages: [],
    ...extra,
  }
}

/** The real `ApiError`, because `hasApiCode` is an `instanceof` check on purpose — a look-alike
 *  would make this test pass against a predicate the app does not use.
 *
 *  🪤 AND THAT IS WHY THE MOCK IS HOISTED RATHER THAN PER-TEST. `vi.resetModules()` + `vi.doMock`
 *  inside a helper gives the subject a FRESH `lib/api` instance whose `ApiError` is a different
 *  class object from the one this file imported — so every `instanceof` was false and both
 *  "switched off" branches silently fell through to the generic error. One module registry, one
 *  class. */
function apiErr(code: string, status: number) {
  return new ApiError(code, status, code)
}

beforeEach(() => {
  H.postRoomMessage.mockReset()
  H.archiveRoom.mockReset()
  H.addRoomMember.mockReset()
  H.room.fn = async () => detail()
  H.agents.fn = async () => ({ agents: [], default_agent: '' })
})
afterEach(cleanup)

function mount(navigate = vi.fn()) {
  render(<RoomView roomId="pricing-debate" navigate={navigate} setQuery={() => {}} />)
  return navigate
}

describe('the room transcript', () => {
  it('renders the human, each member and the room as three different things', async () => {
    H.room.fn = async () => detail({
      messages: [
        { role: 'user', content: 'What do you think?', speaker: '', ts: '1' },
        { role: 'assistant', content: 'Margins are thin.', speaker: 'analyst', ts: '2' },
        { role: 'assistant', content: 'I disagree.', speaker: 'skeptic', ts: '3' },
        { role: 'system', content: 'analyst was refused Write — read-only', speaker: 'analyst', ts: '4' },
      ],
    })
    mount()
    await screen.findByText('Margins are thin.')
    // Every member's words are preceded by its NAME, and the role blurb rides along so its
    // position is legible next to the role it argues from.
    expect(screen.getByText('analyst')).toBeTruthy()
    expect(screen.getByText('argues from the numbers')).toBeTruthy()
    expect(screen.getByText('skeptic')).toBeTruthy()
    // 🔴 Two members in a row stay TWO blocks. The session transcript would have merged them.
    expect(screen.getByText('I disagree.')).toBeTruthy()
    // The room's own note is attributed to the ROOM and names the member it is about, so a
    // refusal never reads as that member having said it.
    expect(screen.getByText(/Room note/)).toBeTruthy()
    expect(screen.getByText('analyst was refused Write — read-only')).toBeTruthy()
  })

  it('is a named, focusable log region — a scroll container owes a tab stop and a name', async () => {
    mount()
    const log = await screen.findByRole('log', { name: 'Should we raise prices? transcript' })
    expect(log.getAttribute('tabindex')).toBe('0')
    // The geometry rail this repo cannot assert (jsdom has no layout): the class list is what makes
    // it scroll rather than grow the page, so the class list is what is pinned.
    expect(log.className).toContain('overflow-y-auto')
    expect(log.className).toContain('min-h-0')
  })

  it('tells a member-less room to add members rather than inviting a message nobody hears', async () => {
    H.room.fn = async () => detail({ room: room({ members: [] }) })
    mount()
    expect(await screen.findByText('Add two members and start the argument')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Open members/ })).toBeTruthy()
  })
})

describe('the pause card', () => {
  it('appears when the room is paused, with the queue it still owes', async () => {
    H.room.fn = async () => detail({
      room: room({ paused: true, rounds_used: 6, pending_queue: ['skeptic'] }),
    })
    mount()
    expect(await screen.findByRole('group', { name: 'Should we raise prices? is paused' })).toBeTruthy()
    expect(screen.getByText('6 of 6 exchanges')).toBeTruthy()
    expect(screen.getByRole('listitem').textContent).toContain('skeptic')
  })

  it('is absent when the room is not paused', async () => {
    mount()
    await screen.findByRole('log')
    expect(screen.queryByRole('group', { name: /is paused/ })).toBeNull()
  })

  it('focuses the composer — the reply IS the resume, so there is nowhere else to send them', async () => {
    H.room.fn = async () => detail({ room: room({ paused: true, rounds_used: 6 }) })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Write a reply/ }))
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: /Message Should we raise prices\?/ }))
  })
})

describe('speaking in the room', () => {
  it('posts to the ROOM route and reports the queue the backend built, in order', async () => {
    H.postRoomMessage.mockResolvedValue({ messages: [], speaking: ['analyst', 'skeptic'] })
    mount()
    const box = await screen.findByRole('textbox', { name: /Message Should we raise prices\?/ })
    await userEvent.type(box, 'Make the case.')
    await userEvent.click(screen.getByRole('button', { name: /^Send/ }))
    expect(H.postRoomMessage).toHaveBeenCalledWith('pricing-debate', 'Make the case.')
    // The FIFO order is the product fact and it is on screen, not merely in a count.
    await waitFor(() => expect(screen.getByText('Answering: analyst → skeptic')).toBeTruthy())
  })

  it('announces the outstanding queue in a polite live region, not an alert', async () => {
    H.postRoomMessage.mockResolvedValue({ messages: [], speaking: ['analyst'] })
    mount()
    await userEvent.type(await screen.findByRole('textbox', { name: /Message/ }), 'Go')
    await userEvent.click(screen.getByRole('button', { name: /^Send/ }))
    // A member answering is progress, not news that changes what the screen means.
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('still to answer: analyst'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('refuses to send an empty message, and says why', async () => {
    mount()
    const send = await screen.findByRole('button', { name: /^Send/ })
    expect(send.getAttribute('aria-disabled')).toBe('true')
    expect(send.getAttribute('title')).toContain('Write something first')
    await userEvent.click(send)
    expect(H.postRoomMessage).not.toHaveBeenCalled()
  })

  it('offers no composer on an archived room, and says the transcript is still readable', async () => {
    H.room.fn = async () => detail({ room: room({ archived: true }) })
    mount()
    expect(await screen.findByText(/This room is archived/)).toBeTruthy()
    expect(screen.queryByRole('textbox', { name: /Message/ })).toBeNull()
  })
})

describe('the states that are not failures', () => {
  it('reads a `rooms_disabled` 403 as the feature being OFF, not as a broken room', async () => {
    H.room.fn = async () => { throw apiErr('rooms_disabled', 403) }
    const navigate = mount()
    expect(await screen.findByText('Agent Rooms is switched off')).toBeTruthy()
    // A LoadError here would tell the user their room is broken when it is switched off.
    expect(screen.queryByText(/Couldn't load/)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Open chat settings/ }))
    expect(navigate).toHaveBeenCalledWith('settings/chat')
  })

  it('reads a missing room as an empty state with a way back', async () => {
    H.room.fn = async () => { throw apiErr('room_not_found', 404) }
    const navigate = mount()
    expect(await screen.findByText('This room no longer exists')).toBeTruthy()
    // The empty state's own action, whose name is deliberately NOT the header's "Back to rooms":
    // two controls with one accessible name on one screen is the duplicate-name defect.
    await userEvent.click(screen.getByRole('button', { name: /See your rooms/ }))
    expect(navigate).toHaveBeenCalledWith('chat/history?origin=room')
  })

  it('still reports a genuine read failure as a retryable error', async () => {
    H.room.fn = async () => { throw apiErr('internal_error', 500) }
    mount()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry|Try again/i })).toBeTruthy()
  })
})

/** The member picker's own read is a SECOND load, and it used to drop its rejection.
 *
 *  `agents` is `undefined` both while `/api/agents` is in flight and after it rejected, so the
 *  picker keyed its placeholder off `agents === undefined` and said `Loading…` — forever, for a
 *  request that had already failed. That is the (B) half of the `useQuery` error class:
 *  `ui/loadErrorState.test.tsx`'s `UNBOUND_ERROR_BUDGET` counts exactly this shape, and
 *  `RoomView.tsx` carries no budget entry, so the census's zero default is the rail. */
describe('the member picker when the agent list cannot be read', () => {
  async function openAddForm() {
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Open members/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Add a member/ }))
  }

  it('says the read FAILED and offers a retry, rather than `Loading…` forever', async () => {
    H.room.fn = async () => detail({ room: room({ members: [] }) })
    H.agents.fn = async () => { throw apiErr('internal_error', 500) }
    await openAddForm()
    expect(await screen.findByText(/Couldn't load your agents/)).toBeTruthy()
    // The whole defect in one assertion: a failed read must not wear the loading state.
    expect(screen.queryByText('Loading…')).toBeNull()
    expect(screen.getByRole('button', { name: /^Retry$/ })).toBeTruthy()
  })

  it('still says `No agents configured` when the read SUCCEEDS and is empty', async () => {
    H.room.fn = async () => detail({ room: room({ members: [] }) })
    await openAddForm()
    // The control for the test above: an empty list is not a failure, and must not claim one.
    expect(await screen.findByText('No agents configured')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load your agents/)).toBeNull()
    expect(screen.queryByRole('button', { name: /^Retry$/ })).toBeNull()
  })
})

/** The action banner's Retry used to be `refresh` — a re-read of the room — for EVERY failed write.
 *  It never re-ran the write, and for a refusal nothing could succeed: at 8/8 the validator saw
 *  "This room already holds the configured maximum of 8 members. Retry", a control that could only
 *  ever earn the same 400. A Retry now exists only when the same write could succeed, and it
 *  re-runs THAT write. */
describe('🔴 a failed write offers a Retry only when running it again could succeed', () => {
  async function tryToAdd() {
    H.room.fn = async () => detail({ room: room({ members: [] }) })
    H.agents.fn = async () => ({ agents: [{ name: 'writer', provider: 'native', model: 'x' }], default_agent: '' })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Open members/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Add a member/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Add to the room/ }))
  }

  it('a refusal says why and offers NO Retry', async () => {
    // The race the panel's own ceiling cannot see: another tab filled the room first.
    H.addRoomMember.mockRejectedValue(
      new ApiError('This room already holds the configured maximum of 8 members.', 400, 'room_member_limit'),
    )
    await tryToAdd()
    expect((await screen.findByRole('alert')).textContent).toMatch(/maximum of 8 members/)
    expect(screen.queryByRole('button', { name: /^Retry$/ })).toBeNull()
    expect(H.addRoomMember).toHaveBeenCalledTimes(1)
  })

  it('a transient failure offers a Retry that RE-RUNS the same add', async () => {
    H.addRoomMember
      .mockRejectedValueOnce(new ApiError('The gateway hit an error.', 503, 'unavailable'))
      .mockResolvedValueOnce({ room: room() })
    await tryToAdd()
    await userEvent.click(await screen.findByRole('button', { name: /^Retry$/ }))
    await waitFor(() => expect(H.addRoomMember).toHaveBeenCalledTimes(2))
    expect(H.addRoomMember.mock.calls[1]).toEqual(H.addRoomMember.mock.calls[0])
  })
})
