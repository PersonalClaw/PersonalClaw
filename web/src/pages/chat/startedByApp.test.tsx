import { describe, it, expect, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { HeaderModePill } from '../../ui/HeaderActions'
import { AppPermissionNotice, StartedByApp, appPermissionSentence, startedByName } from './StartedByApp'

// A conversation an app started sits in your history beside yours, and a turn in it runs under
// the APP's grant whoever sends it — your Trust and YOLO never reach it. The server names the app
// (`created_by_app`, `created_by_app_name`) and says how its grant decides (`app_auto_approves`);
// these pin that every surface prints that and nothing it does not know.

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

  it('says whose permissions the turn runs under, for both answers the grant can give', () => {
    const auto = appPermissionSentence('Probe Chat', true)
    const asks = appPermissionSentence('Probe Chat', false)
    for (const sentence of [auto, asks]) {
      expect(sentence).toContain("runs with Probe Chat's permissions")
      expect(sentence).toContain("Your Trust and YOLO settings don't apply in this chat.")
    }
    expect(auto).toContain('run without asking you')
    expect(asks).toContain('asks you')
    expect(asks).not.toContain('without asking')
  })

  it('shows the note above the composer', () => {
    render(<AppPermissionNotice name="Probe Chat" autoApproves={false} />)
    expect(screen.getByText(appPermissionSentence('Probe Chat', false))).toBeTruthy()
  })
})

describe('the Permission pill in an app’s conversation', () => {
  const options = [{ key: 'normal', label: 'Normal' }, { key: 'trust', label: 'Trust' }, { key: 'yolo', label: 'YOLO' }]

  it('stays reachable, says why it is unavailable, and changes nothing', () => {
    const onChange = vi.fn()
    render(
      <HeaderModePill ariaLabel="Permission mode" value="trust" onChange={onChange} options={options}
        disabled disabledReason="Probe Chat's permissions decide this chat, not yours" />,
    )
    const trigger = screen.getByRole('button', { name: 'Permission mode: Trust' })
    expect(trigger.hasAttribute('disabled'), 'the native attribute would drop the tab stop').toBe(false)
    expect(trigger.getAttribute('aria-disabled')).toBe('true')
    expect(trigger.getAttribute('title')).toBe("Probe Chat's permissions decide this chat, not yours")
    trigger.focus()
    expect(document.activeElement).toBe(trigger)
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    fireEvent.click(trigger)
    expect(screen.queryAllByRole('menuitemradio')).toHaveLength(0)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('is the ordinary live control in one of your chats', () => {
    render(<HeaderModePill ariaLabel="Permission mode" value="normal" onChange={vi.fn()} options={options} />)
    const trigger = screen.getByRole('button', { name: 'Permission mode: Normal' })
    expect(trigger.getAttribute('aria-disabled')).toBeNull()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    expect(screen.getAllByRole('menuitemradio')).toHaveLength(3)
  })
})
