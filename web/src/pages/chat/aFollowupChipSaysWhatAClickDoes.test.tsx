import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FollowupChips } from './FollowupChips'

// A follow-up chip's label said "Click to edit · double-click to send", and a double-click never
// sent anything. The host clears the chips on the first click (it fills the composer and the chips
// are done), so the second click of the pair landed on the transcript and the chip's double-click
// handler could never run. Measured in the browser: click detail 1 on the chip, then click detail 2
// and the dblclick 15 ms later on a transcript element, the text in the composer and no turn sent.
//
// Sending a suggestion as it stands is the send glyph's job, and the glyph names what it sends. A
// double-click, which many people give any button, does not also send words as hers, and the
// label's tooltip now says what a click does.

const ITEM = 'Add it to the school-fall-2026.md file'

describe('a follow-up chip says what a click on it does', () => {
  afterEach(cleanup)

  it('a double-click on the label fills the composer and sends nothing', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onSend = vi.fn()
    render(<FollowupChips items={[ITEM]} onPick={onPick} onSend={onSend} />)

    await user.dblClick(screen.getByRole('button', { name: ITEM }))

    expect(onSend).not.toHaveBeenCalled()
    expect(onPick).toHaveBeenCalledWith(ITEM)
  })

  it("the label's tooltip says a click is for editing, and promises no double-click", () => {
    render(<FollowupChips items={[ITEM]} onPick={() => {}} onSend={() => {}} />)

    const title = screen.getByRole('button', { name: ITEM }).getAttribute('title') ?? ''
    expect(title).toBe('Click to edit it before you send it')
    expect(title.toLowerCase()).not.toContain('double')
  })

  it('the send glyph still sends the chip as it stands', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<FollowupChips items={[ITEM]} onPick={() => {}} onSend={onSend} />)

    await user.click(screen.getByRole('button', { name: `Send: ${ITEM}` }))

    expect(onSend).toHaveBeenCalledTimes(1)
    expect(onSend).toHaveBeenCalledWith(ITEM)
  })
})
