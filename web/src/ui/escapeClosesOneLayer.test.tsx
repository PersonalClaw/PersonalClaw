import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { forwardRef, type ReactNode } from 'react'

// ── Escape closes one layer: a window over the page, not the panel docked beside it ──────────────
//
// 🔴 Red before: Chat preferences is a modal window over the chat, and a docked panel (Activity, the
// working folder, the session map, Chat history, an open file) listened for Escape on the window as
// the modal does, so one Escape closed both, and the focus landed on whichever finished last. Now a
// panel yields Escape while a dialog is open over the page (`[aria-modal="true"]`), as the turn's
// Stop key does, and closes on Escape again once the dialog is gone.

vi.mock('./content/ContentSurface', () => ({
  ContentSurface: forwardRef(function ContentSurface({ title }: { title: string }, _ref) {
    return <div data-testid="surface" data-title={title} />
  }),
}))

const { SidePanel } = await import('./SidePanel')
const { Modal } = await import('./Modal')
const { AppearanceProvider } = await import('../app/appearance')
const { ChatFilePanel } = await import('../pages/chat/ChatFilePanel')
const { registerBuiltinContentTypes } = await import('./content/registerBuiltins')

// The file viewer resolves a file's content type from the registry the app fills at boot.
registerBuiltinContentTypes()

afterEach(cleanup)

function mount(panel: (onClose: () => void) => ReactNode, withModal: boolean) {
  const panelClosed = vi.fn()
  const modalClosed = vi.fn()
  render(
    <AppearanceProvider>
      {panel(panelClosed)}
      {withModal && <Modal title="Chat preferences" onClose={modalClosed}><button type="button">Inside</button></Modal>}
    </AppearanceProvider>,
  )
  return { panelClosed, modalClosed }
}

const sidePanel = (onClose: () => void) => <SidePanel title="Activity" onClose={onClose}><p>Activity rows</p></SidePanel>
const filePanel = (onClose: () => void) => <ChatFilePanel path="/home/u/notes.md" onClose={onClose} />

describe.each([
  ['a docked side panel', sidePanel],
  ['the chat’s file panel', filePanel],
] as const)('with %s beside the chat', (_name, panel) => {
  it('Escape in a window over the page closes the window, and not the panel', () => {
    const { panelClosed, modalClosed } = mount(panel, true)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Inside' }), { key: 'Escape' })
    expect(modalClosed).toHaveBeenCalledTimes(1)
    expect(panelClosed).not.toHaveBeenCalled()
  })

  it('with no window over the page, Escape closes the panel', () => {
    const { panelClosed } = mount(panel, false)
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(panelClosed).toHaveBeenCalledTimes(1)
  })
})
