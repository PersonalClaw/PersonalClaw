/** The owner's consent for a write that loosens a security setting — asked ONCE, here, for
 *  every surface.
 *
 *  🔑 THE GATEWAY DECIDES WHICH WRITES LOOSEN, NOT THIS FILE. A field on its list
 *  (`config/edit_spec.py`: a `SecurityControl` on the `_EDITABLE_CONFIG` spec, or an agent's
 *  `approval_mode`) answers a loosening write with `400 confirmation_required`, carrying
 *  `{field, consent}` in `error.detail`. So a surface never predicts the direction — raising a
 *  budget, removing a denied pattern, allowing a host — and cannot disagree with the server
 *  about it. It sends the write; if the gateway asks, the owner is asked in the gateway's own
 *  words, and the write is resent with `confirm: true` only after they agree.
 *
 *  A surface that already asked its own, more specific question (the YOLO switch, "Allow all
 *  private networks", turning the 2FA requirement off, "Propose fix branches", letting an MCP
 *  server ask questions) passes `confirmed` so the owner is not asked twice.
 *
 *  This module deliberately does not import `api.ts` (which imports it): the refusal is
 *  recognised by shape — `ApiError` carries `.code` and `.detail`. */
import { confirm } from '../ui/dialog'

/** The owner declined the consent the gateway asked for, so nothing was written. */
export class ConsentDeclined extends Error {
  readonly field: string
  constructor(field: string) {
    super('Not changed — you kept the current setting.')
    this.name = 'ConsentDeclined'
    this.field = field
  }
}

interface ConsentAsked { field: string; consent: string }

/** The gateway's consent question inside a rejection, or `null` when it is anything else. */
export function consentAsked(e: unknown): ConsentAsked | null {
  if (!(e instanceof Error) || (e as { code?: unknown }).code !== 'confirmation_required') return null
  const detail = (e as { detail?: unknown }).detail
  if (!detail || typeof detail !== 'object') return null
  const { field, consent } = detail as Record<string, unknown>
  return typeof field === 'string' && typeof consent === 'string' && consent.trim()
    ? { field, consent }
    : null
}

/** Run `send`; when the gateway asks for consent, ask the owner and resend with it.
 *
 *  `send(true)` must put `confirm: true` in the body. `confirmed` is for a caller that already
 *  asked: it is sent with consent the first time, and a refusal then propagates unchanged. */
export async function withSecurityConsent<T>(
  send: (confirmed: boolean) => Promise<T>,
  confirmed = false,
): Promise<T> {
  try {
    return await send(confirmed)
  } catch (e) {
    const asked = confirmed ? null : consentAsked(e)
    if (!asked) throw e
    const ok = await confirm({
      title: 'Loosen a security setting?',
      body: asked.consent,
      confirmLabel: 'Allow',
      danger: true,
    })
    if (!ok) throw new ConsentDeclined(asked.field)
    return send(true)
  }
}
