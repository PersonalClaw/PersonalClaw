import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

// ── A trigger's background agent is listed by its trigger's name ─────────────────────────────────
//
// An agent a trigger starts has a task that opens with the unattended-run framing, and the list
// named each agent by its task's first line — so every automation's run read "You are running
// unattended. Your output is read later as a report…". The run now carries a title (its trigger's
// name, else the instruction it was given), and the list leads with it. An agent nobody named still
// reads by its task.

const FRAMED = '[AUTONOMOUS RUN — no user is present to reply]\nYou are running unattended. Your output is read later as a report.\n[END AUTONOMOUS RUN CONTEXT]\n\nRemind me to call the dentist'

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      // The card's system and sign-in halves are not what this is about; unread, the card shows the
      // connection line and the agents list below it.
      system: async () => { throw new Error('not read in this test') },
      authStatus: async () => { throw new Error('not read in this test') },
      spawnedAgents: async () => [
        { id: 'a1', task: FRAMED, title: 'Call the dentist', done: false, parent: 'cron:remind-dentist' },
        { id: 'b2', task: 'Summarize the open issues\nand group them', done: false },
      ],
    },
  }
})

const { SystemWidget } = await import('./SystemWidget')

describe('the background agents list', () => {
  it('🔴 names a trigger’s agent by its title, not by its framed task', async () => {
    render(<SystemWidget />)
    fireEvent.click(screen.getByRole('button', { name: /System status/ }))
    expect(await screen.findByText('Call the dentist')).toBeTruthy()
    expect(screen.queryByText(/You are running unattended/)).toBeNull()
  })

  it('and an agent nobody named by its task’s first line', async () => {
    render(<SystemWidget />)
    fireEvent.click(screen.getByRole('button', { name: /System status/ }))
    expect(await screen.findByText('Summarize the open issues')).toBeTruthy()
  })
})
