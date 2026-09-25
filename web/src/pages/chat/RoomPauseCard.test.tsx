/**
 * `AGENT-ROOMS` AR-8 — the round-budget pause card.
 *
 * The card exists because the round budget was previously state with no reader: `rounds_used` and
 * `paused` were persisted and published and nothing rendered them. So these tests are about what
 * the card SAYS, and each one pins a claim the atom's own clause makes:
 *
 *  · `rounds_used` against `effective_round_budget` — never the declared `round_budget`, which is 0
 *    for a room that inherits the configured default. "0 of 0" is the inert reading;
 *  · `pending_queue` LISTED, in order — a pause suspends the queue rather than cancelling it, and a
 *    bare count cannot tell "my analyst never answered" from "the room just stopped";
 *  · only the actions the backend supports — there IS no resume route, so there must be no Resume
 *    button: any human message resets the budget, which is why the primary action is to write one.
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RoomPauseCard } from './RoomPauseCard'
import type { RoomRecord } from '../../lib/api'

function room(extra: Partial<RoomRecord> = {}): RoomRecord {
  return {
    id: 'pricing-debate',
    title: 'Should we raise prices?',
    created_at: '2026-09-23T00:00:00',
    archived: false,
    paused: true,
    rounds_used: 6,
    round_budget: 0,
    pending_queue: [],
    members: [],
    effective_round_budget: 6,
    max_round_budget: 100,
    max_members: 8,
    transcript_path: '/rooms/pricing-debate/transcript.jsonl',
    ...extra,
  }
}

describe('the round-budget pause card', () => {
  it("carries the pause sentence the inbox row carries, verbatim", () => {
    // Product tone rather than an incidental string, and the SAME words `rooms.arbiter.PAUSE_TITLE`
    // puts on the attention item — a user who arrives from the inbox must read the same sentence.
    render(<RoomPauseCard room={room()} onReply={() => {}} onArchive={() => {}} />)
    expect(screen.getByRole('heading', { name: 'Your agents have been talking for a while.' })).toBeTruthy()
  })

  it('states the budget against the RESOLVED ceiling, not the declared 0', () => {
    render(<RoomPauseCard room={room({ rounds_used: 6, round_budget: 0, effective_round_budget: 6 })}
      onReply={() => {}} onArchive={() => {}} />)
    expect(screen.getByText('6 of 6 exchanges')).toBeTruthy()
    // The tell for the inert reading this atom removes: the declared field is 0 here, so a card
    // reading it would print "6 of 0".
    expect(screen.queryByText('6 of 0 exchanges')).toBeNull()
  })

  it("LISTS the members still owed a turn, in the queue's order", () => {
    render(<RoomPauseCard room={room({ members: [
      { name: 'analyst', role_blurb: '', listen_policy: 'all', profile_narrowing: {} },
      { name: 'skeptic', role_blurb: '', listen_policy: 'mention', profile_narrowing: {} },
    ], pending_queue: ['skeptic', 'analyst'] })} onReply={() => {}} onArchive={() => {}} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    // FIFO order is the product fact: these two were enqueued before the reply the user is about
    // to write, so they are ahead of it — and skeptic was enqueued first.
    expect(items[0].textContent).toContain('skeptic')
    expect(items[1].textContent).toContain('analyst')
    expect(items[0].textContent).toContain('1.')
  })

  it('drops a parked member who has left the room, rather than promising it a turn', () => {
    // The arbiter's `resume_queue` filters the park to the current roster, so a member removed
    // while the room was paused does not speak.
    render(<RoomPauseCard room={room({ members: [
      { name: 'analyst', role_blurb: '', listen_policy: 'all', profile_narrowing: {} },
    ], pending_queue: ['departed', 'analyst'] })} onReply={() => {}} onArchive={() => {}} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.queryByText('departed')).toBeNull()
  })

  it('says the queue will run, when there is one', () => {
    const { rerender } = render(<RoomPauseCard room={room({ members: [
      { name: 'analyst', role_blurb: '', listen_policy: 'all', profile_narrowing: {} },
    ], pending_queue: ['analyst'] })} onReply={() => {}} onArchive={() => {}} />)
    expect(screen.getByText(/the members below speak first/)).toBeTruthy()
    // With nothing parked the copy must NOT claim a queue exists.
    rerender(<RoomPauseCard room={room()} onReply={() => {}} onArchive={() => {}} />)
    expect(screen.queryByText(/the members below speak first/)).toBeNull()
    expect(screen.getByText(/archive the room if the thread is finished/)).toBeTruthy()
  })

  it('offers reply and archive, and NO resume — there is no resume route', () => {
    render(<RoomPauseCard room={room()} onReply={() => {}} onArchive={() => {}} />)
    expect(screen.getByRole('button', { name: /Write a reply/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Archive the room/ })).toBeTruthy()
    // `reset_round_budget` is called by ANY human message, so a Resume button would be a second
    // way to do one thing — and the one that did not also say anything would restart the round
    // with nothing new to discuss.
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /continue/i })).toBeNull()
  })

  it('runs its two handlers', async () => {
    const onReply = vi.fn()
    const onArchive = vi.fn()
    render(<RoomPauseCard room={room()} onReply={onReply} onArchive={onArchive} />)
    await userEvent.click(screen.getByRole('button', { name: /Write a reply/ }))
    expect(onReply).toHaveBeenCalledOnce()
    await userEvent.click(screen.getByRole('button', { name: /Archive the room/ }))
    expect(onArchive).toHaveBeenCalledOnce()
  })

  it('is a named group and NOT an alert — the inbox item already announced this', () => {
    // The pause raises exactly one attention item, whose deep link lands on this room. A live
    // region here would tell the user twice about one event.
    render(<RoomPauseCard room={room()} onReply={() => {}} onArchive={() => {}} />)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('group', { name: 'Should we raise prices? is paused' })).toBeTruthy()
  })
})
