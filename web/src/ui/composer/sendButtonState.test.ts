import { describe, it, expect } from 'vitest'
import { resolveSendButton, sendButtonIsActive, type SendButtonInputs } from './sendButtonState'

// The composer send/stop/steer/sent/processing state machine — the decision core
// of the plan's 5 send→stream smoke flows. These pin the button choice for every
// combination of (processing, streaming, canSend, canQueue, justSent) so a future
// edit can't silently regress e.g. "stop mid-stream" or "steer a draft into a
// running turn". The live WS round-trip (chunks accumulating, /stop halting,
// reconnect backoff) is exercised as-a-user via Chrome DevTools, not mocked here.

const base: SendButtonInputs = {
  processing: false, streaming: false, canSend: false, canQueue: false, justSent: false,
}

describe('resolveSendButton', () => {
  it('idle + empty draft → disabled send (smoke: cannot send a blank message)', () => {
    expect(resolveSendButton(base)).toBe('send-disabled')
    expect(sendButtonIsActive('send-disabled')).toBe(false)
  })

  it('idle + ready draft → live send (smoke: full send path is armed)', () => {
    expect(resolveSendButton({ ...base, canSend: true })).toBe('send')
    expect(sendButtonIsActive('send')).toBe(true)
  })

  it('just after send → transient sent bloom, inert (smoke: send→ confirmation)', () => {
    // justSent wins over the idle send/disabled choice, and is not clickable.
    expect(resolveSendButton({ ...base, justSent: true })).toBe('sent')
    expect(resolveSendButton({ ...base, canSend: true, justSent: true })).toBe('sent')
    expect(sendButtonIsActive('sent')).toBe(false)
  })

  it('streaming + no draft → stop (smoke: stop mid-stream)', () => {
    expect(resolveSendButton({ ...base, streaming: true })).toBe('stop')
    // even a ready draft stops (not steers) when the surface can't queue.
    expect(resolveSendButton({ ...base, streaming: true, canSend: true })).toBe('stop')
    expect(sendButtonIsActive('stop')).toBe(true)
  })

  it('streaming + queue-able draft → steer into the running turn', () => {
    expect(resolveSendButton({ ...base, streaming: true, canSend: true, canQueue: true })).toBe('steer')
    // queue-able but empty draft → still stop (nothing to steer).
    expect(resolveSendButton({ ...base, streaming: true, canQueue: true })).toBe('stop')
    expect(sendButtonIsActive('steer')).toBe(true)
  })

  it('processing outranks everything → inert spinner (smoke: one-shot pre-send pass)', () => {
    expect(resolveSendButton({ ...base, processing: true })).toBe('processing')
    // processing wins even if streaming/canSend/justSent are also set.
    expect(resolveSendButton({
      processing: true, streaming: true, canSend: true, canQueue: true, justSent: true,
    })).toBe('processing')
    expect(sendButtonIsActive('processing')).toBe(false)
  })
})
