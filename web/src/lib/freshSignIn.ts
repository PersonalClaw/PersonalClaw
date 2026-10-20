/** A write that needs a recent sign-in: the owner is asked to sign in again, once, here, for every
 *  surface — and then the write is sent again, so what they asked for carries on.
 *
 *  🔑 THE GATEWAY DECIDES WHICH WRITES NEED ONE, NOT THIS FILE (`dashboard/owner_presence.py`):
 *  minting a credential (a device pairing code, a device sign-in code, an integration token, a
 *  channel owner's code), setting the first password, and making sign-in less strict. Sent from a
 *  device that signed in more than ten minutes ago, such a write is answered
 *  `401 fresh_sign_in_required`, with the gateway's sentence and what to ask for in `error.detail`:
 *  `password` (this gateway offers password sign-in) and `second_factor` (an authenticator code is
 *  set up). Every write in `api.ts` goes through `withFreshSignIn`, so a surface never predicts it.
 *  The desktop app's own window is never asked: it signs itself in.
 *
 *  Signing in again is either the password (`POST /api/auth/confirm`, which replaces this device's
 *  sign-in with a new one) or, where password sign-in is off, a new link from `personalclaw token`
 *  pasted here, which this page opens in place (a `?token=` request, the exchange a link opened in
 *  the address bar makes), so the page and what was typed into it stay put.
 *
 *  The answer is not a sign-out (it carries no `X-Auth-Required`), and neither is a refused
 *  password or link here: those are read with plain `fetch`, never `api.ts`'s error builder, which
 *  would take a refused link for this tab being signed out. This module does not import `api.ts`,
 *  which imports it: the answer is recognised by shape. */
import { Fragment, createElement, type ReactNode } from 'react'
import { apiVersionHeaders } from './apiVersion'
import { errEnvelope } from './errText'
import { GatewaySentence } from '../app/SignedOutScreen'
import { alertDialog, promptForm, type DialogField } from '../ui/dialog'

/** The owner closed the sign-in prompt, so the write was not sent again and nothing was done. */
export class SignInNotRenewed extends Error {
  constructor() {
    super('Not done — you didn’t sign in again.')
    this.name = 'SignInNotRenewed'
  }
}

export interface FreshSignInAsked {
  /** The gateway's sentence: what needs a recent sign-in, and how to sign in again. */
  message: string
  /** Whether this gateway offers password sign-in — the door the prompt opens. */
  password: boolean
  /** Whether an authenticator code is asked for with the password. */
  secondFactor: boolean
}

/** The gateway's "sign in again" answer inside a rejection, or `null` when it is anything else. */
export function freshSignInAsked(e: unknown): FreshSignInAsked | null {
  if (!(e instanceof Error) || (e as { code?: unknown }).code !== 'fresh_sign_in_required') return null
  const detail = (e as { detail?: unknown }).detail
  const d = detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : {}
  return {
    message: e.message,
    password: d.password === true,
    secondFactor: d.second_factor === true,
  }
}

/** Run `send`; when the gateway asks for a recent sign-in, ask the owner to sign in again and run
 *  it once more. `send` must build its request afresh each time it is called. */
export async function withFreshSignIn<T>(send: () => Promise<T>): Promise<T> {
  try {
    return await send()
  } catch (e) {
    const asked = freshSignInAsked(e)
    if (!asked) throw e
    if (!(await signInAgain(asked))) throw new SignInNotRenewed()
    return send()
  }
}

/** The token a pasted sign-in link carries — or the paste itself, when it is a bare token; `''`
 *  for a paste that holds none (a token has no spaces in it). A link contributes only its token, so
 *  pasting cannot send this page anywhere. */
export function tokenFromLink(raw: string): string {
  const value = raw.trim()
  if (!value) return ''
  try {
    return new URL(value).searchParams.get('token') || ''
  } catch {
    return /\s/.test(value) ? '' : value
  }
}

const HEADERS = { 'X-Session-Key': 'dashboard:ui', ...apiVersionHeaders }

interface Attempt {
  ok: boolean
  /** Why it did not sign in, to show above the next try. */
  message?: string
  /** Nothing another try could change (a lockout, password sign-in turned off): stop asking. */
  final?: boolean
}

async function signInAgain(asked: FreshSignInAsked): Promise<boolean> {
  let note = ''
  for (;;) {
    const values = await promptForm({
      title: 'Confirm it’s you',
      body: bodyOf(note, asked.message),
      fields: fieldsFor(asked),
      confirmLabel: 'Continue',
    })
    if (!values) return false
    const attempt = asked.password
      ? await withPassword(values.password ?? '', values.code ?? '')
      : await withLink(values.link ?? '')
    if (attempt.ok) return true
    if (attempt.final) {
      await alertDialog({ title: 'Couldn’t sign in again', body: bodyOf('', attempt.message ?? ''), tone: 'danger' })
      return false
    }
    note = attempt.message ?? ''
  }
}

/** Why the last try did not sign in (when it did not), above the gateway's sentence; each with its
 *  `backticked` commands shown as code, as the signed-out screen shows the gateway's sentences. */
function bodyOf(note: string, message: string): ReactNode {
  const sentence = (text: string) => createElement(GatewaySentence, { text })
  return note ? createElement(Fragment, null, sentence(note), '\n\n', sentence(message)) : sentence(message)
}

function fieldsFor(asked: FreshSignInAsked): DialogField[] {
  if (!asked.password) {
    return [{ name: 'link', label: 'Sign-in link', placeholder: 'Paste the link from personalclaw token', required: true }]
  }
  const password: DialogField = { name: 'password', label: 'Password', type: 'password', required: true }
  return asked.secondFactor
    ? [password, { name: 'code', label: 'Authenticator code', placeholder: '123456', required: true }]
    : [password]
}

async function withPassword(password: string, code: string): Promise<Attempt> {
  const r = await fetch('/api/auth/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...HEADERS },
    body: JSON.stringify(code ? { password, totp: code } : { password }),
  })
  if (r.ok) return { ok: true }
  const { message, code: refusal } = await errEnvelope(r)
  // A wrong password or code, or a missing one, is worth another try; a lockout or a gateway that
  // stopped offering password sign-in is not.
  const again = refusal === 'auth_invalid_credentials' || refusal === 'auth_totp_required' || refusal === 'auth_password_required'
  return { ok: false, message, final: !again }
}

const NOT_A_LINK =
  'That isn’t a sign-in link from this PersonalClaw. Run `personalclaw token` on the computer running PersonalClaw and paste the link it prints.'

async function withLink(raw: string): Promise<Attempt> {
  const token = tokenFromLink(raw)
  // A paste with no token in it is asked for again without a request the gateway can only refuse.
  if (!token) return { ok: false, message: NOT_A_LINK }
  const r = await fetch(`/api/auth/session?token=${encodeURIComponent(token)}`, { headers: HEADERS })
  if (r.ok) return { ok: true }
  const { message, detail } = await errEnvelope(r)
  const reason = (detail as { reason?: unknown } | undefined)?.reason
  // The gateway explains a link it made that can no longer sign a device in (it expired, or another
  // device used it). Anything else is no link of this PersonalClaw's, and its own sentence — "this
  // device isn't signed in" — would be untrue of this one, which still is.
  if (reason === 'link_expired' || reason === 'link_used') return { ok: false, message }
  return { ok: false, message: NOT_A_LINK }
}
