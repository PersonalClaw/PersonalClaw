/** The owner's consent for a write that needs their yes — a loosened security setting, or a grant
 *  for what a trigger runs — asked ONCE, here, for every surface.
 *
 *  🔑 THE GATEWAY DECIDES WHICH WRITES NEED IT, NOT THIS FILE. A field on its list
 *  (`config/edit_spec.py`: a `SecurityControl` on the `_EDITABLE_CONFIG` spec, or an agent's
 *  `approval_mode` or tool list), or an action a trigger is not allowed to run
 *  (`triggers/grants.py`), answers the write with `confirmation_required`, carrying
 *  `{field, consent, title}` in `error.detail`:
 *  a 200 marked `X-PersonalClaw-Consent-Asked` to this page, whose writes say they ask
 *  (`api.ts`), so no failed request is logged for a question, and a 400 to any other client. So a
 *  surface never predicts the direction — raising a budget, removing a denied
 *  pattern, allowing a host — and cannot disagree with the server about it. It sends the write; if
 *  the gateway asks, the owner is asked in the gateway's own words, heading included, and the
 *  write is resent with `confirm: true` only after they agree. The heading is the gateway's too:
 *  a grant question is not headed "Loosen a security setting?", which is a loosening's alone.
 *
 *  A loosening also says what THIS write changes, from and to (`change`: "$33.50 → $10,033.50"),
 *  and for a raise of ten times or more one more sentence (`caution`). The sentence alone asked the
 *  same question of every value: a cap typed as $10,033.50 for $100 read exactly like $100.
 *
 *  A surface that already asked its own, more specific question (the YOLO switch, "Allow all
 *  private networks", turning the 2FA requirement off, "Propose fix branches", letting an MCP
 *  server ask questions) passes `confirmed` so the owner is not asked twice.
 *
 *  A question about a workflow an automation runs also carries what it showed of it (`shown`: the
 *  version its sentence names, and those of the workflows it runs as steps), and the resend sends
 *  that back beside `confirm: true`, so the yes is to what the owner read and not to whatever an
 *  agent saved while the dialog was open. When something did, the gateway refuses the yes
 *  (`409 stale_write`, nothing changed) with the question as it is now, which says who saved what
 *  since, and the owner is asked again (`triggers/grants.py`).
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

interface ConsentAsked {
  field: string; consent: string; title: string
  /** What the write changes, from and to — a loosening's, absent from any other question. */
  change?: string
  /** One more sentence, for a raise of ten times or more. */
  caution?: string
  /** What the question showed of the workflow an automation runs, which the yes sends back: the
   *  gateway's own value, handed back as it came. Absent from any other question. */
  shown?: Record<string, unknown>
}

/** The question in a rejection whose `code` is *code*, or `null` when it carries none. */
function questionIn(e: unknown, code: string): ConsentAsked | null {
  if (!(e instanceof Error) || (e as { code?: unknown }).code !== code) return null
  const detail = (e as { detail?: unknown }).detail
  if (!detail || typeof detail !== 'object') return null
  const { field, consent, title, change, caution, shown } = detail as Record<string, unknown>
  if (!(typeof field === 'string' && typeof consent === 'string' && consent.trim()
    && typeof title === 'string' && title.trim())) return null
  return {
    field, consent, title,
    ...(typeof change === 'string' && change.trim() ? { change } : {}),
    ...(typeof caution === 'string' && caution.trim() ? { caution } : {}),
    ...(shown && typeof shown === 'object' && !Array.isArray(shown) ? { shown: shown as Record<string, unknown> } : {}),
  }
}

/** The gateway's consent question inside a rejection, or `null` when it is anything else. */
export function consentAsked(e: unknown): ConsentAsked | null {
  return questionIn(e, 'confirmation_required')
}

/** The question a yes was refused with because what its question showed moved since
 *  (`409 stale_write` carrying the question as it is now), or `null` for any other refusal. */
export function consentAskedAgain(e: unknown): ConsentAsked | null {
  return questionIn(e, 'stale_write')
}

/** What the dialog says, one paragraph each: the gateway's sentence, what the write changes, and
 *  the second look a raise of ten times or more asks for. The dialog keeps line breaks, so an
 *  egress change's one line per list stays one line each. */
export function consentBody(asked: ConsentAsked): string {
  return [asked.consent, asked.change, asked.caution].filter(Boolean).join('\n\n')
}

/** Run `send`; when the gateway asks for consent, ask the owner and resend with it.
 *
 *  `send(true)` must put `confirm: true` in the body, and `send(true, shown)` must put `shown`
 *  beside it too: what the question showed, which the yes is held to. A yes the gateway refuses
 *  because what it showed moved since is asked again, in the gateway's new words, until the owner
 *  allows what it shows then or declines. `confirmed` is for a caller that already asked: it is
 *  sent with consent the first time, and a refusal then propagates unchanged. */
export async function withSecurityConsent<T>(
  send: (confirmed: boolean, shown?: Record<string, unknown>) => Promise<T>,
  confirmed = false,
): Promise<T> {
  try {
    return await send(confirmed)
  } catch (e) {
    let asked = confirmed ? null : consentAsked(e)
    if (!asked) throw e
    for (;;) {
      const ok = await confirm({
        title: asked.title,
        body: consentBody(asked),
        confirmLabel: 'Allow',
        danger: true,
      })
      if (!ok) throw new ConsentDeclined(asked.field)
      try {
        return await (asked.shown ? send(true, asked.shown) : send(true))
      } catch (again) {
        const next = consentAskedAgain(again)
        if (!next) throw again
        asked = next
      }
    }
  }
}
