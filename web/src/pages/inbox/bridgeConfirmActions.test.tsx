/** A control-bridge action waiting for your confirmation is answered from its Inbox row.
 *
 *  🔴 Red on main: nothing in PersonalClaw could confirm one. The row said "Confirm to allow it"
 *  and offered no control, and the only confirmation that worked was the asking client's own
 *  `/confirm`, which let it confirm itself. You answer it here now
 *  (`/api/external-access/bridge/confirmations/<id>`), and the client that asked cannot. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { InboxItem } from '../../lib/api'
import { BridgeConfirmActions } from './BridgeConfirmActions'

const answerBridgeConfirmation = vi.fn()

vi.mock('../../lib/api', () => ({
  api: { answerBridgeConfirmation: (...a: unknown[]) => answerBridgeConfirmation(...a) },
}))

const ASKS =
  'A local agent on the control bridge asks to run create_task with {"title": "Pay rent"}. Confirm ' +
  'runs it once; the agent cannot confirm it itself. It expires 10 minutes after it was asked.'

/** The row `inbound.bridge.handle_action` raises, as the Inbox serves it. */
function bridgeRow(): InboxItem {
  return {
    id: 'inbox-bridge', channel: 'loop', channel_name: 'loop', message: ASKS,
    sender_id: 'loop', sender_name: 'loop', classification: 'needs_reply', confidence: 'high',
    status: 'pending', item_kind: 'needs_input',
    refs: {
      source: 'control_bridge', action: 'create_task', confirmation: 'conf-1',
      asked_by: 'bridge:surface',
    },
  } as InboxItem
}

beforeEach(() => vi.clearAllMocks())

describe('confirming a control-bridge action', () => {
  it('asks the row’s question and says what each answer does', () => {
    render(<BridgeConfirmActions item={bridgeRow()} onChanged={vi.fn()} />)
    expect(screen.getByText(ASKS)).toBeInTheDocument()
    expect(screen.getByText('Approve runs it once, now. Deny drops it.')).toBeInTheDocument()
  })

  it('Approve confirms it as you, once', async () => {
    answerBridgeConfirmation.mockResolvedValue({ status: 'ok', action: 'create_task', result: {} })
    const onChanged = vi.fn()
    render(<BridgeConfirmActions item={bridgeRow()} onChanged={onChanged} />)
    fireEvent.click(screen.getByText('Approve').closest('button')!)

    await waitFor(() => expect(answerBridgeConfirmation).toHaveBeenCalledWith('conf-1', true))
    expect(await screen.findByText('Ran create_task once.')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
  })

  it('Deny drops it and runs nothing', async () => {
    answerBridgeConfirmation.mockResolvedValue({ status: 'declined', action: 'create_task' })
    render(<BridgeConfirmActions item={bridgeRow()} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByText('Deny').closest('button')!)

    await waitFor(() => expect(answerBridgeConfirmation).toHaveBeenCalledWith('conf-1', false))
    expect(await screen.findByText('Declined. Nothing ran; the agent has to ask again.')).toBeInTheDocument()
  })

  it('an expired confirmation says so and leaves the card', async () => {
    answerBridgeConfirmation.mockRejectedValue(
      new Error('This confirmation was already answered, or it expired: nothing ran.'),
    )
    render(<BridgeConfirmActions item={bridgeRow()} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByText('Approve').closest('button')!)

    expect(
      await screen.findByText('This confirmation was already answered, or it expired: nothing ran.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Approve').closest('button')).toBeInTheDocument()
  })
})
