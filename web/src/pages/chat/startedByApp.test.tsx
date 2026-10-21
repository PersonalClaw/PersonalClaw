import { describe, it, expect, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { ExecutionBoundary } from './composer/ExecutionBoundary'
import { AppPermissionNotice, StartedByApp, appPermissionSentence, startedByName } from './StartedByApp'

// A conversation an app started sits in your history beside yours, and a turn in it runs under
// the APP's permissions whoever sends it — your Trust and YOLO never reach it, and the app approves
// none of its calls. The server names the app (`created_by_app`, `created_by_app_name`); these pin
// that every surface prints that and nothing it does not know.

describe('Started by <app>', () => {
  it('names the app as install consent showed it', () => {
    render(<StartedByApp s={{ created_by_app: 'probe-chat', created_by_app_name: 'Probe Chat' }} />)
    expect(screen.getByText('Started by Probe Chat')).toBeTruthy()
  })

  it('falls back to the app id when the server sent no name', () => {
    expect(startedByName({ created_by_app: 'probe-chat' })).toBe('probe-chat')
    render(<StartedByApp s={{ created_by_app: 'probe-chat' }} />)
    expect(screen.getByText('Started by probe-chat')).toBeTruthy()
  })

  it('renders nothing on one of your chats', () => {
    const { container } = render(<StartedByApp s={{}} />)
    expect(container.innerHTML).toBe('')
    expect(startedByName({ created_by_app: '', created_by_app_name: '' })).toBe('')
  })

  it('says whose permissions the turn runs under, and that each call needing approval asks you', () => {
    const sentence = appPermissionSentence('Probe Chat')
    expect(sentence).toContain("runs with Probe Chat's permissions")
    expect(sentence).toContain("Your Trust and YOLO settings don't apply in this chat.")
    expect(sentence).toContain('asks you')
    expect(sentence).not.toContain('without asking')
  })

  it('shows the note above the composer', () => {
    render(<AppPermissionNotice name="Probe Chat" />)
    expect(screen.getByText(appPermissionSentence('Probe Chat'))).toBeTruthy()
  })
})

describe('the permission mode in an app’s conversation', () => {
  const reading = {
    sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
    reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
  }
  const why = "Probe Chat's permissions decide this chat, not yours"

  it('stays reachable, says why it is unavailable, and changes nothing', async () => {
    const onApproval = vi.fn(async () => null)
    render(<ExecutionBoundary approval="trust" onApproval={onApproval} lockedReason={why} memoryMode="persistent"
      folder="the workspace" reading={reading} />)
    fireEvent.click(screen.getByRole('button', { name: 'Execution boundary: Trust' }))
    const group = await screen.findByRole('group', { name: 'Permission mode' })
    expect(group).toHaveAccessibleDescription(why)
    const rows = within(group).getAllByRole('menuitemradio')
    for (const row of rows) {
      expect(row.hasAttribute('disabled'), 'the native attribute would drop it from the menu').toBe(false)
      expect(row.getAttribute('aria-disabled')).toBe('true')
    }
    fireEvent.click(rows[0])
    expect(onApproval).not.toHaveBeenCalled()
  })

  it('is the ordinary live control in one of your chats', async () => {
    const onApproval = vi.fn(async () => null)
    render(<ExecutionBoundary approval="normal" onApproval={onApproval} memoryMode="persistent"
      folder="the workspace" reading={reading} />)
    fireEvent.click(screen.getByRole('button', { name: 'Execution boundary: Normal' }))
    const group = await screen.findByRole('group', { name: 'Permission mode' })
    const rows = within(group).getAllByRole('menuitemradio')
    expect(rows).toHaveLength(4)
    for (const row of rows) expect(row.getAttribute('aria-disabled')).toBeNull()
    await act(async () => { fireEvent.click(rows[2]) })
    expect(onApproval).toHaveBeenCalledWith('trust')
  })
})
