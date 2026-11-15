// An approval in a queue (Needs you) is one card among others, drawn inside the card's own tile:
// the chat's words, with no rise on arrival, no alert, no border or tint of its own, its kind as a
// tag, its "From …" line in the tag row as plain text, both choices on one ground in the card's
// ink, its whole input in the flow (folding after 40 lines), and its second confirmation inline on
// `InlineConfirm`. The chat's card is unchanged.
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApprovalCard } from '../pages/chat/ApprovalCard'
import type { ApprovalSegment } from '../pages/chat/chatTypes'
import { QUEUE_FOLD_LINES } from './ApprovalPrompt'
import { ARM_MS } from './dialog/DialogShell'
import { TOUCH_TARGET } from './Toggle'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

const LONG = Array.from({ length: QUEUE_FOLD_LINES + 10 }, (_, i) => `rm -rf ~/code/site/build/part-${i}`).join('\n')

function seg(over: Partial<ApprovalSegment> = {}): ApprovalSegment {
  return {
    kind: 'approval', id: 'a1', tool: 'bash', input: LONG, purpose: 'Clear the old build', risk: 'destructive',
    confirms: [], blastRadius: { writes: true, network: false, shell: true, saysReadOnly: false, readOnly: false },
    ...over,
  }
}

function card(form: 'chat' | 'queue', over: Partial<ApprovalSegment> = {}) {
  const onAct = vi.fn(() => Promise.resolve(true))
  const view = render(
    <ApprovalCard seg={seg(over)} onAct={onAct} answers="once" form={form}
      source='loop “Tidy the site”' sourceHref="#/loops/l1" />,
  )
  return { onAct, ...view }
}

const choice = (name: RegExp) => screen.getByRole('button', { name })

describe("an approval's queue form", () => {
  it('draws no rise, no alert and no border or tint of its own, with its kind as a tag', () => {
    const { container } = card('queue')
    const root = container.querySelector('[data-approval-form="queue"]') as HTMLElement
    expect(root).not.toBeNull()
    expect(root.getAttribute('style')).toBeNull()
    expect(root.className).not.toContain('border')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('group', { name: 'Permission needed to run bash' })).toBe(root)
    expect(root.textContent).toContain('Permission needed')
  })

  it('says who asked in the tag row as plain text, and leaves the link to the panel', () => {
    card('queue')
    expect(screen.getByText('From loop “Tidy the site”').tagName).toBe('SPAN')
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('draws both choices in the card ink on one ground, Deny in danger ink, each a touch target whose words wrap', () => {
    card('queue')
    const allow = choice(/^Allow/)
    const deny = choice(/^Deny/)
    for (const b of [allow, deny]) {
      expect(b.getAttribute('data-type')).toBe('label-s')
      expect(b.style.background).toBe('var(--color-surface-high)')
      for (const cls of TOUCH_TARGET.split(' ')) expect(b.className.split(/\s+/)).toContain(cls)
      expect(b.className.split(/\s+/)).toContain('whitespace-normal')
      expect(b.className.split(/\s+/)).toContain('border-transparent')
    }
    expect(allow.style.color).toBe('var(--color-on-surface)')
    expect(deny.style.color).toBe('var(--color-danger)')
  })

  it('shows the whole input in the flow, folding after 40 lines', () => {
    const { container } = card('queue')
    fireEvent.click(screen.getByRole('button', { name: 'Show all of what bash would run' }))
    const pre = screen.getByRole('group', { name: 'Tool arguments' })
    expect(pre.className).not.toMatch(/max-h-|overflow-auto/)
    expect(pre.textContent?.split('\n')).toHaveLength(QUEUE_FOLD_LINES)
    fireEvent.click(screen.getByRole('button', { name: 'Show the other 10 lines' }))
    expect(screen.getByRole('group', { name: 'Tool arguments' }).textContent).toBe(LONG)
    expect(container.querySelector('[tabindex="0"]')).toBeNull()
  })

  it('asks once more inline before an Allow the row confirms, and every press of Allow only asks', async () => {
    const { onAct } = card('queue', { confirms: ['deletes'] })
    fireEvent.click(choice(/^Allow/))
    const ask = screen.getByRole('group', { name: 'It deletes files. Run it?' })
    expect(ask.hasAttribute('data-inline-confirm')).toBe(true)
    fireEvent.click(choice(/^Allow/))
    expect(screen.getAllByRole('group', { name: 'It deletes files. Run it?' })).toHaveLength(1)
    const yes = screen.getByRole('button', { name: 'Yes, run it' })
    fireEvent.pointerDown(yes)
    fireEvent.click(yes, { detail: 1 })
    expect(onAct).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(ARM_MS) })
    fireEvent.pointerDown(yes)
    fireEvent.click(yes, { detail: 1 })
    await act(async () => {})
    expect(onAct).toHaveBeenCalledTimes(1)
    expect(onAct).toHaveBeenCalledWith('a1', 'approved')
  })

  it('steps back from the confirmation on Back and on Escape, deciding nothing', () => {
    const { onAct } = card('queue', { confirms: ['deletes'] })
    fireEvent.click(choice(/^Allow/))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.queryByRole('group', { name: 'It deletes files. Run it?' })).toBeNull()
    fireEvent.click(choice(/^Allow/))
    fireEvent.keyDown(screen.getByRole('button', { name: 'Yes, run it' }), { key: 'Escape' })
    expect(screen.queryByRole('group', { name: 'It deletes files. Run it?' })).toBeNull()
    expect(onAct).not.toHaveBeenCalled()
  })

  it('leaves the chat card as it was: its alert, its tint, its scrolled input and its own confirmation', () => {
    const { container } = card('chat', { confirms: ['deletes'] })
    expect(screen.getByRole('alert').textContent).toBe('Permission needed')
    expect(container.querySelector('[data-approval-form]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show all of what bash would run' }))
    expect(screen.getByRole('group', { name: 'Tool arguments' }).className).toContain('max-h-[14rem]')
    fireEvent.click(choice(/^Allow/))
    expect(container.querySelector('[data-inline-confirm]')).toBeNull()
    expect(screen.getByRole('button', { name: /^Yes, run it: / })).toBeTruthy()
  })

  it('sizes its risk and reach chips by their text, on both forms', () => {
    for (const form of ['chat', 'queue'] as const) {
      const { unmount } = card(form)
      const risk = screen.getByText('Destructive').closest('span') as HTMLElement
      const reach = screen.getAllByRole('listitem')
      const chips = [risk, ...reach]
      expect(reach.length).toBeGreaterThan(0)
      for (const chip of chips) {
        expect(chip.className).toContain('min-h-[1.125rem]')
        expect(chip.className).not.toContain('h-[18px]')
      }
      unmount()
    }
  })
})
