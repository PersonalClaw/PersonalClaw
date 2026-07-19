// The composer's right-hand action button is a small state machine: depending on
// whether a turn is streaming, whether a one-shot pre-send pass is processing, and
// whether the user has a queue-able draft, it shows send / sent / stop / steer /
// processing. Kept as a pure function (no React) so the decision is unit-testable
// without mounting the CodeMirror-heavy Composer, and so the JSX stays declarative.

export type SendButtonKind =
  | 'processing' // one-shot pre-send pass (e.g. goal analyze) — spinner, inert
  | 'stop'       // a turn is streaming and there's no queue-able draft
  | 'steer'      // a turn is streaming AND a draft is ready to inject into it
  | 'sent'       // transient success bloom right after an idle send
  | 'send'       // idle, draft meets minChars — the live send affordance
  | 'send-disabled' // idle, draft too short — dimmed, inert

export interface SendButtonInputs {
  processing: boolean
  streaming: boolean
  canSend: boolean   // draft.trim().length >= minChars
  canQueue: boolean  // this surface allows steering a draft into a running turn
  justSent: boolean  // the transient post-send bloom window is open
}

/** Resolve which action button the composer shows. Order matters: a one-shot
 *  processing pass outranks streaming; mid-stream we steer if there's a draft to
 *  queue, else stop; idle we show the transient 'sent' bloom, then send/disabled. */
export function resolveSendButton(s: SendButtonInputs): SendButtonKind {
  if (s.processing) return 'processing'
  if (s.streaming) return s.canQueue && s.canSend ? 'steer' : 'stop'
  if (s.justSent) return 'sent'
  return s.canSend ? 'send' : 'send-disabled'
}

/** Whether a given button kind is clickable (has an onClick). 'sent' and
 *  'processing' are inert; 'send-disabled' is dimmed and inert. */
export function sendButtonIsActive(kind: SendButtonKind): boolean {
  return kind === 'stop' || kind === 'steer' || kind === 'send'
}
