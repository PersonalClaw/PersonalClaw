/** Genui action routing (AMBIENT-SURFACES §5.4).
 *
 *  A registry component may declare an ACTION (a Button, a Form's submit). When a
 *  human activates it the host emits **dual payloads** — the thesys contract:
 *
 *    · `llmFriendlyMessage`  — rich and machine-bound: the action name plus the full
 *      collected state, JSON-serialized. This is what the model / the run receives.
 *    · `humanFriendlyMessage` — the short label the TRANSCRIPT shows. The whole point
 *      of the pair: a form submit must not appear in a conversation as raw JSON.
 *
 *  Routing is by PRODUCER, not by component — the same `Form` means three different
 *  things depending on who emitted the widget:
 *
 *    chat-born      → the action becomes the next USER TURN (the existing
 *                     `ne:widget-action` bridge, with the human message as the turn's
 *                     visible text and the llm message as the content the model reads);
 *    workflow gate  → the action ANSWERS the run's waiting gate through the resume
 *                     path, so the run advances instead of the answer landing in a chat;
 *    tile           → the action re-fires the tile's bound workflow, server-side,
 *                     inside that tile's FROZEN capability set (a rendered button can
 *                     never introduce an action the binding did not declare).
 *
 *  The producer is supplied by the HOST that renders the widget (`GenUiHostCtx`), never
 *  by the component and never by the widget's own text. That is deliberate and it is a
 *  security property: a producer stamped into the block (`gate="run:token"`) would be
 *  MODEL-AUTHORED, so a chat transcript could name a run and turn a click into a gate
 *  answer. Only a host that already holds the run/tile identity can widen a widget's
 *  reach, and the default — a plain chat turn — is the harmless one. */
import { createContext, useContext } from 'react'
import { api } from '../../lib/api'
// The LEAF turn dialect, not the bridge module: importing the bridge here would close the
// appSdk → genui → components → actions → bridge → appSdk cycle (see actionTurn.ts).
import { finishActionText, publishWidgetAction } from '../widget/actionTurn'

/** The two messages one activation produces. */
export interface DualPayload {
  /** Machine-bound: what the model / run receives (full state). */
  llmFriendlyMessage: string
  /** Human-bound: the short label the transcript renders. */
  humanFriendlyMessage: string
}

/** Who emitted the widget — decides where an action goes. */
export type GenUiProducer =
  | { kind: 'chat' }
  /** A gate node's prompt, rendered by a host that holds the run + resume token. */
  | { kind: 'workflow-gate'; runId: string; token: string }
  /** A dashboard tile, rendered by the band that holds the view + tile ref. */
  | { kind: 'tile'; viewId: string; ref: string }

/** Turn an action name into a sentence a human reads when no label was declared.
 *  `submit_expense` → `Submit expense`. Never returns "" — a turn with no visible
 *  text would be worse than a mechanical one. */
export function humanizeAction(action: string): string {
  const words = (action || '').replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim()
  if (!words) return 'Action'
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Compose the dual payloads for one activation.
 *
 *  The llm message reuses the `[UI] ` prefix and the 16 KiB clip the raw-HTML widget
 *  bridge already established (`finishActionText`) — ONE turn dialect, so an agent
 *  branches on one prefix and an adversarial payload is clipped by the same rule on
 *  both paths. The human message is NEVER clipped there: it is a label, and it is the
 *  string the transcript shows. */
export function composeDualPayload(input: {
  action: string
  /** The declared visible label (a Button's `label`, a Form's `submit`). */
  label?: string
  /** Collected state: form values, the button's declared payload. */
  payload?: Record<string, unknown>
  /** The saved-artifact identity, when the widget IS one (the C32 living view). */
  live?: { saved: boolean; slug: string }
}): DualPayload | null {
  const payload = input.payload && Object.keys(input.payload).length ? input.payload : undefined
  let body: string
  try {
    body = payload ? `${input.action}: ${JSON.stringify(payload)}` : input.action
  } catch {
    // A value postMessage/structured-clone accepted that JSON cannot serialize
    // (a cycle). Refusing beats throwing inside a click handler.
    return null
  }
  const label = (input.label || '').trim() || humanizeAction(input.action)
  return {
    llmFriendlyMessage: finishActionText(body, input.live),
    humanFriendlyMessage: label,
  }
}

/** What a routed action did — surfaced next to the control so a failed submit is
 *  visible where it happened rather than only in the console. */
export interface GenUiActionResult {
  ok: boolean
  /** Machine-readable outcome, for tests and for the control's own state. */
  outcome: 'chat-turn' | 'gate-resolved' | 'tile-refired' | 'refused' | 'error'
  message?: string
}

/** Route one composed activation to its producer's sink. */
export async function routeGenUiAction(
  dual: DualPayload,
  producer: GenUiProducer,
  raw: { action: string; payload?: Record<string, unknown> },
): Promise<GenUiActionResult> {
  if (producer.kind === 'workflow-gate') {
    try {
      const r = await api.resumeWorkflowRun(producer.runId, {
        answer: raw.payload && Object.keys(raw.payload).length ? raw.payload : dual.humanFriendlyMessage,
        resume_token: producer.token,
      })
      // The server says whether the gate was actually answered. `ok === false` carries
      // a code (unknown token, stale epoch, invalid answer) — rendering "submitted" on
      // top of that would be the lie this result type exists to prevent.
      if (r && r.ok === false) return { ok: false, outcome: 'refused', message: 'This gate could not be answered — it may already be resolved.' }
      return { ok: true, outcome: 'gate-resolved' }
    } catch (e) {
      return { ok: false, outcome: 'error', message: (e as Error)?.message || 'Could not answer the gate.' }
    }
  }
  if (producer.kind === 'tile') {
    try {
      const r = await api.tileWidgetAction(producer.viewId, {
        ref: producer.ref,
        action: raw.action,
        payload: raw.payload,
      })
      if (r && r.ok === false) {
        return {
          ok: false,
          outcome: 'refused',
          message: r.message || 'That action is outside this tile’s frozen capability set.',
        }
      }
      return { ok: true, outcome: 'tile-refired' }
    } catch (e) {
      return { ok: false, outcome: 'error', message: (e as Error)?.message || 'Could not re-fire this tile.' }
    }
  }
  // chat-born: the action becomes the next user turn. `label` is what the bubble
  // shows; `llmFriendlyMessage` is what the model reads.
  publishWidgetAction(dual.llmFriendlyMessage, { label: dual.humanFriendlyMessage })
  return { ok: true, outcome: 'chat-turn' }
}

/** What an action-capable component calls. Supplied by the widget host so a
 *  component never reaches a router (or an api client) itself. */
export type GenUiEmit = (input: {
  action: string
  label?: string
  payload?: Record<string, unknown>
}) => void | Promise<void>

/** Null emitter: an action-capable component rendered by a host that wired no
 *  routing does NOTHING on click rather than throwing. */
const NO_EMIT: GenUiEmit = () => {}

export const GenUiActionCtx = createContext<GenUiEmit>(NO_EMIT)

/** The emitter for the widget currently rendering. */
export function useGenUiAction(): GenUiEmit {
  return useContext(GenUiActionCtx)
}

/** What a host declares about the widgets it renders. */
export interface GenUiHost {
  producer: GenUiProducer
  /** Called after an action was routed successfully — the host's own refresh (close the
   *  inbox row, re-read the tile). Optional: a chat host has nothing to refresh. */
  onResolved?: () => void
}

/** The DEFAULT host: a chat turn. Every widget with no host above it is chat-born,
 *  which is both the common case and the only harmless one. */
const CHAT_HOST: GenUiHost = { producer: { kind: 'chat' } }

export const GenUiHostCtx = createContext<GenUiHost>(CHAT_HOST)

/** The host declaration for the widget currently rendering. */
export function useGenUiHost(): GenUiHost {
  return useContext(GenUiHostCtx)
}
