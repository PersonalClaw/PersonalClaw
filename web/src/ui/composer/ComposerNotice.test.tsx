import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ComposerNoticeLine, INFO_NOTICE_MS, useComposerNotice, type ComposerNoticeState } from './ComposerNotice'

// ── The composer's notice: an error stays until it is dismissed, an update clears itself ─────
//
// 🔴 Before: every notice by the chat composer — and a microphone error on the loop front door's —
// cleared itself after six seconds, errors included. The reasons it carries are sentences a user
// reads and then acts on — a speech-to-text provider naming the setting to fix runs past two
// hundred characters — so the fix was gone before it was read. Each host's own wiring is driven
// through the real page: `pages/chat/errorNoticeStaysUntilDismissedOrSent.test.tsx` and
// `pages/loop/micErrorStaysUntilDismissed.test.tsx`.

let notice: ComposerNoticeState
function Host() {
  notice = useComposerNotice()
  return <ComposerNoticeLine notice={notice.notice} onDismiss={notice.clear} />
}

const REASON =
  'Speech-to-text with Amazon Transcribe needs an S3 bucket to upload each recording to. Set S3 ' +
  'Bucket on this Amazon Bedrock instance in Settings → Providers (under Advanced), or the ' +
  'BEDROCK_VIDEO_S3_BUCKET environment variable, then try again.'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('the composer notice', () => {
  it('an error stays, however long it takes to read, until it is dismissed', () => {
    render(<Host />)
    act(() => notice.showError(REASON))

    act(() => { vi.advanceTimersByTime(30 * 60_000) })

    expect(screen.getByRole('alert').textContent).toContain(REASON)
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('an informational notice clears on its own, and has no Dismiss to press', () => {
    render(<Host />)
    act(() => notice.showInfo('Ignored the assistant’s own voice coming back through the microphone.'))
    expect(screen.getByRole('status').textContent).toMatch(/Ignored the assistant’s own voice/)
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()

    act(() => { vi.advanceTimersByTime(INFO_NOTICE_MS - 1) })
    expect(screen.queryByRole('status'), 'not before its time').not.toBeNull()
    act(() => { vi.advanceTimersByTime(1) })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('an informational notice’s clock never takes down an error shown after it', () => {
    render(<Host />)
    act(() => notice.showInfo('This run is parked — approve the plan below to resume it.'))
    act(() => notice.showError(REASON))

    act(() => { vi.advanceTimersByTime(INFO_NOTICE_MS * 10) })

    expect(screen.getByRole('alert').textContent).toContain(REASON)
  })

  it('a notice cleared before its time leaves no clock behind to clear the next one', () => {
    render(<Host />)
    act(() => notice.showInfo('This run is parked — approve the plan below to resume it.'))
    act(() => notice.clear())
    act(() => { vi.advanceTimersByTime(INFO_NOTICE_MS - 1) })
    act(() => notice.showInfo('Ignored the assistant’s own voice coming back through the microphone.'))

    act(() => { vi.advanceTimersByTime(1) })

    expect(screen.getByRole('status').textContent, 'the second notice gets its own full time')
      .toMatch(/Ignored the assistant’s own voice/)
  })
})
