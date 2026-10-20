// The composer's right-hand action button is a small state machine: depending on
// whether a turn is streaming, whether a one-shot pre-send pass is processing, and
// whether the user has a queue-able draft, it shows send / sent / stop / steer / queue /
// processing. Kept as a pure function (no React) so the decision is unit-testable
// without mounting the CodeMirror-heavy Composer, and so the JSX stays declarative.

export type SendButtonKind =
  | 'processing' // one-shot pre-send pass (e.g. goal analyze) — spinner, inert
  | 'stop'       // a turn is streaming and there's no queue-able draft
  | 'steer'      // a turn is streaming, a draft is ready, and the turn takes it in
  | 'queue'      // a turn is streaming and a draft is ready, which runs when the turn ends
  | 'sent'       // transient success bloom right after an idle send
  | 'send'       // idle, draft meets minChars — the live send affordance
  | 'send-disabled' // idle, draft too short — dimmed, inert
  | 'send-held'   // idle, draft ready but a file it carries is still uploading — dimmed, inert

export interface SendButtonInputs {
  processing: boolean
  streaming: boolean
  canSend: boolean   // draft.trim().length >= minChars
  canQueue: boolean  // this surface allows sending a draft while a turn runs
  canSteer?: boolean // the running turn takes a draft in (its runtime pulls one) — else it queues
  justSent: boolean  // the transient post-send bloom window is open
  held?: boolean     // a file the message carries is still uploading — an idle send waits for it
}

/** Resolve which action button the composer shows. Order matters: a one-shot
 *  processing pass outranks streaming; mid-stream a ready draft is steered into a turn that
 *  takes one and queued behind one that does not, else stop; idle we show the transient
 *  'sent' bloom, then send/disabled. */
export function resolveSendButton(s: SendButtonInputs): SendButtonKind {
  if (s.processing) return 'processing'
  if (s.streaming) return s.canQueue && s.canSend ? (s.canSteer ? 'steer' : 'queue') : 'stop'
  if (s.justSent) return 'sent'
  if (!s.canSend) return 'send-disabled'
  return s.held ? 'send-held' : 'send'
}

/** Whether a given button kind is clickable (has an onClick). 'sent' and
 *  'processing' are inert; 'send-disabled' and 'send-held' are dimmed and inert. */
export function sendButtonIsActive(kind: SendButtonKind): boolean {
  return kind === 'stop' || kind === 'steer' || kind === 'queue' || kind === 'send'
}
