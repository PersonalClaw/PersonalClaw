/**
 * `AGENT-ROOMS` AR-8 — the Rooms scope: the list itself, and the wiring that makes it reachable.
 *
 * Two halves, and the second one is derived over the source rather than rendered. `ChatHistoryPage`
 * is 700 lines inside a 5,200-line file with three network reads and a windowed list; rendering it
 * to assert "the Rooms tab exists" would be a fixture exercise that tests the mock. The wiring
 * claims are STRUCTURAL — a union member, a tab gate, a route branch, a hardcoded reserved-sub list
 * in the shell — and each is a one-line edit somebody could undo without noticing, which is exactly
 * what a source rail is for. This repo already reads `ChatPage`'s source this way
 * (`chatListNoMatch.test.ts`).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ApiError, type RoomRecord } from '../../lib/api'
import { RoomsScope } from './RoomsScope'

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

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
    speaking: '',
    owed: [],
    round_running: false,
    members: [],
    effective_round_budget: 6,
    max_round_budget: 100,
    max_members: 8,
    transcript_path: '/rooms/pricing-debate/transcript.jsonl',
    ...extra,
  }
}

afterEach(cleanup)

describe('the rooms list', () => {
  it('shows each room with its member count, its budget and its state', () => {
    render(<RoomsScope
      rooms={[room({
        paused: true, rounds_used: 6, pending_queue: ['skeptic'], owed: ['skeptic'],
        members: [
          { name: 'analyst', role_blurb: '', listen_policy: 'all', profile_narrowing: {} },
          { name: 'skeptic', role_blurb: '', listen_policy: 'mention', profile_narrowing: {} },
        ],
      })]}
      error={null} loading={false} onRefresh={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Should we raise prices?')).toBeTruthy()
    expect(screen.getByText('2 members')).toBeTruthy()
    expect(screen.getByText('6 of 6 exchanges')).toBeTruthy()
    expect(screen.getByText('Paused')).toBeTruthy()
    // The parked queue on the ROW: "paused" alone does not say whether anyone is still waiting to
    // speak, which is the difference between replying and archiving.
    expect(screen.getByText('1 still owed a turn')).toBeTruthy()
  })

  it('says a room whose round was cut off is INTERRUPTED, on the row', () => {
    // The user may not be looking at the room when the gateway restarts; the list is where they
    // find out that an answer they asked for never arrived.
    render(<RoomsScope
      rooms={[room({ speaking: 'analyst', owed: ['analyst'], round_running: false })]}
      error={null} loading={false} onRefresh={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Interrupted')).toBeTruthy()
    expect(screen.getByText('1 still owed a turn')).toBeTruthy()
  })

  it('promises no turn on an archived room — nobody may speak in it', () => {
    render(<RoomsScope
      rooms={[room({ archived: true, owed: ['analyst'] })]}
      error={null} loading={false} onRefresh={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Archived')).toBeTruthy()
    expect(screen.queryByText(/still owed a turn/)).toBeNull()
  })

  it('opens a room by its own id', async () => {
    const navigate = vi.fn()
    render(<RoomsScope rooms={[room()]} error={null} loading={false} onRefresh={() => {}} navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: 'Open the room Should we raise prices?' }))
    expect(navigate).toHaveBeenCalledWith('chat/room/pricing-debate')
  })

  it('offers a way to make the FIRST room — otherwise the tab is a dead end', async () => {
    render(<RoomsScope rooms={[]} error={null} loading={false} onRefresh={() => {}} navigate={() => {}} />)
    expect(screen.getByText('No rooms yet')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /New room/ }))
    expect(screen.getByRole('textbox', { name: 'Room title' })).toBeTruthy()
  })

  it("reads `rooms_disabled` as the feature being off, and routes to the switch", async () => {
    const navigate = vi.fn()
    render(<RoomsScope rooms={undefined} error={new ApiError('off', 403, 'rooms_disabled')}
      loading={false} onRefresh={() => {}} navigate={navigate} />)
    expect(screen.getByText('Agent Rooms is switched off')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load/)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Turn it on in chat settings/ }))
    expect(navigate).toHaveBeenCalledWith('settings/chat')
  })

  it('still reports a genuine read failure as retryable', () => {
    const onRefresh = vi.fn()
    render(<RoomsScope rooms={undefined} error={new ApiError('boom', 500, 'internal_error')}
      loading={false} onRefresh={onRefresh} navigate={() => {}} />)
    expect(screen.getByRole('alert')).toBeTruthy()
  })
})

describe('🔴 the wiring that makes a room reachable at all', () => {
  const chat = read('pages/ChatPage.tsx')
  const app = read('app/App.tsx')

  it("'room' is a member of the chat-history origin union, in both of its two declarations", () => {
    // The union is declared inline TWICE (the narrowed value and the setter), with no name, so a
    // member added to one and not the other type-checks and then silently never resolves.
    const decls = chat.match(/'manual' \| 'loop' \| 'code' \| 'channel' \| 'room' \| 'all'/g) ?? []
    expect(decls.length, "both inline declarations must carry 'room'").toBe(2)
    expect(chat, 'and the raw value must be narrowed to it').toContain("originRaw === 'room'")
  })

  it('the Rooms tab appears the moment `/api/rooms` answers — including at ZERO rooms', () => {
    // A fresh install with rooms ON has no rooms, and the tab is how the first one gets made. A
    // `roomCount > 0` gate would make the feature reachable only to someone who already used it.
    expect(chat, 'the tab is gated on the read succeeding, not on the count')
      .toMatch(/originCounts\.channel > 0 \|\| roomsAvailable\)/)
    expect(chat, 'and the whole control strip survives an empty chat list for the same reason')
      .toMatch(/sessions\.length > 0 \|\| roomsAvailable\)/)
    expect(chat, 'the tab option itself').toMatch(/key: 'room', label: `Rooms\$\{roomCount/)
  })

  it('the Rooms scope swaps the list BODY rather than filtering sessions', () => {
    // A room is not a chat session: it is read from `/api/rooms`, and each member's own provider
    // session is filtered out of `/api/chat/sessions` by the backend. So the scope cannot be a
    // predicate over `sessions`, and the body branch must come BEFORE the session load states — a
    // failed `/api/chat/sessions` must not replace the rooms list with a chat LoadError.
    expect(chat).toMatch(/const roomsScope = origin === 'room'/)
    const bodyIdx = chat.indexOf('{roomsScope ? (')
    const sessionErrIdx = chat.indexOf('sessions === null && sessionsError ?')
    expect(bodyIdx, 'the rooms body branch must exist').toBeGreaterThan(0)
    expect(bodyIdx, 'and must precede the session load states').toBeLessThan(sessionErrIdx)
    expect(chat, 'it renders the rooms list, not a narrowed session list').toContain('<RoomsScope rooms={roomsData}')
  })

  it('`#/chat/room/<id>` resolves to the room, ABOVE the session-key fallthrough', () => {
    // `ChatPage`'s dispatcher treats any unrecognised segment as a session key, so a branch placed
    // after the fallthrough would try to resume a session called "room".
    const roomBranch = chat.indexOf("if (seg === 'room')")
    const newBranch = chat.indexOf("if (!seg || seg === 'new')")
    expect(roomBranch, 'the room branch must exist').toBeGreaterThan(0)
    expect(roomBranch, 'and must sit above the bare/new branch').toBeLessThan(newBranch)
    expect(chat).toContain('<RoomView key={roomId}')
  })

  it('the shell does not mistake `room/<id>` for a chat session key', () => {
    // `activeChatSession` hardcodes the reserved subs. Treating `room/<id>` as a session key would
    // suppress every out-of-context approval toast while a room is open, by claiming a session
    // named "room/<id>" is on screen.
    expect(app).toMatch(/!sub\.startsWith\('room\/'\) && sub !== 'room'/)
  })

  it("the room's cache namespace is DECLARED, so it gets a policy rather than the default", () => {
    const keys = read('lib/data/keys.ts')
    expect(keys, 'declared in the one registry').toMatch(/\n  rooms: LIVE,/)
    // LIVE and not COLLECTION: a room's contents change behind the app's back by design, since a
    // human message starts a background round whose replies land with nothing in this tab writing
    // them.
    expect(keys).not.toMatch(/\n  rooms: (COLLECTION|CONFIG),/)
  })

  it('an inbox pause row deep-links to the room it is about', () => {
    // The pause raises ONE attention item stamping `refs.room`; this is the link that makes the
    // pause card its destination rather than a second notice.
    const meta = read('pages/inbox/inboxMeta.ts')
    expect(meta).toMatch(/if \(refs\.room\) return `chat\/room\/\$\{encodeURIComponent\(refs\.room\)\}`/)
    expect(meta).toMatch(/if \(refs\.room\) return 'Go to the room'/)
  })
})
