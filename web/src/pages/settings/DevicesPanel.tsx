import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check, Copy, KeyRound, Laptop, MonitorSmartphone, QrCode, Smartphone, Terminal, Globe, XCircle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api } from '../../lib/api'
import type { DeviceRec, DevicePairStart } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup } from './settingsUI'
import { PairingQr } from './PairingQr'
import { Button } from '../../ui/Button'
import { StatusPill } from '../../ui/StatusPill'
import { EmptyState, FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { relPast, absTime } from '../schedule/scheduleMeta'
import { copyText } from '../../app/clipboard'

/** The house form for a CAUGHT error (`lib/errText` takes a `Response`, not an exception).
 *  The api client has already turned the failed response into this message. */
const msg = (e: unknown) => String((e as Error)?.message || e)

/** The closed device vocabulary from `session_store.DEVICE_KINDS`, given a glyph and a word.
 *  A glyph alone would carry the kind in colour/shape only; the word is the accessible form. */
const KINDS: Record<DeviceRec['kind'], { label: string; icon: LucideIcon }> = {
  browser: { label: 'Browser', icon: Globe },
  mobile: { label: 'Phone', icon: Smartphone },
  desktop: { label: 'Desktop', icon: Laptop },
  cli: { label: 'Terminal', icon: Terminal },
  unknown: { label: 'Unknown', icon: MonitorSmartphone },
}

/** A token nothing has opened is a credential, not a device: it gets its own glyph and word. */
const TOKEN_KIND = { label: 'Token', icon: KeyRound }

/** How it signed in, in the owner's words — the door it came through (`session_store.ISSUERS`).
 *  A browser that opened a link reads differently from a token nothing has opened yet, so the
 *  same issuer has a sign-in wording and a token wording. An unrecognized value renders as
 *  itself, never as blank. */
function issuerLabel(d: DeviceRec): string {
  const token = d.pool === 'token'
  switch (d.issuer) {
    case 'pair': return 'Paired with a code'
    case 'enroll': return 'Signed in with a device code'
    case 'login': return 'Signed in with a password'
    case 'token': return token ? 'From personalclaw token, the CLI or a script' : 'Signed in with a personalclaw token link'
    case 'startup': return token ? 'The link the gateway printed at startup' : 'Signed in with the link the gateway opened at startup'
    case 'ready': return token ? 'The harness token (--json-ready)' : 'Signed in with the harness token (--json-ready)'
    case 'unknown': return 'Signed in before PersonalClaw recorded how'
    default: return d.issuer
  }
}

/** What to call a row: its name, else what it is. A token nothing has used has no client yet; one
 *  that HAS been used, by a client that did not say what it is, must not read as unused — the
 *  line under it lists that use. */
function rowName(d: DeviceRec): string {
  if (d.name) return d.name
  if (d.pool !== 'token') return 'Unnamed device'
  return d.last_seen > 0 ? 'Unrecognised client' : 'Token not used yet'
}

/** "Paired 3d ago" / "Signed in 3d ago" / "Issued 3d ago" — when it signed in, in its own verb. */
function signedInLine(d: DeviceRec): string {
  const verb = d.issuer === 'pair' ? 'Paired' : d.pool === 'token' ? 'Issued' : 'Signed in'
  return `${verb} ${d.minted_at > 0 ? relPast(d.minted_at) : 'at an unknown time'}`
}

/** Seconds until *expiresAt* (epoch seconds), floored at 0.
 *  Derived from the deadline rather than by decrementing `expires_in`, so a backgrounded tab
 *  that stopped getting timer ticks reads the real remaining time when it wakes, not a frozen one. */
function secsLeft(expiresAt: number): number {
  return Math.max(0, Math.floor(expiresAt - Date.now() / 1000))
}

function mmss(total: number): string {
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/** Copy one value, and SAY whether it worked. A clipboard write can be refused outright (no
 *  permission, or a non-secure context — which a LAN `http://` dashboard is), and this is a
 *  surface whose entire purpose is getting a code onto another screen: a silent failure here
 *  leaves the owner believing they hold a code they do not.
 *
 *  🔑 THIS WAS THE ONLY SITE IN THE APP THAT GOT IT RIGHT — 1 of 13 — so its logic became
 *  `app/clipboard.copyText` rather than staying a local exception, and this button is now the
 *  helper's first consumer instead of its private prototype. The reasoning above is why the helper
 *  exists; it is kept here because this surface is where the case was first understood. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    if (!(await copyText(value, `the ${label}`))) return
    setDone(true)
    setTimeout(() => setDone(false), 1500)
  }
  return (
    <Button size="xs" variant="secondary" onClick={copy} ariaLabel={done ? `${label} copied` : `Copy ${label}`}>
      {done ? <Check size={14} /> : <Copy size={14} />} {done ? 'Copied' : 'Copy'}
    </Button>
  )
}

/** Settings → Devices — the ONE list of what is signed in, in the product.
 *
 *  Every row is derived from a live session, so this list IS the answer to "what can reach this
 *  gateway right now": a sign-out removes the row because it removed the session, not because the
 *  UI hid it. It is EVERY sign-in — it used to be paired phones only, which hid the owner's own
 *  browsers, the desktop app and every script token, the sessions a sixth mint silently signed
 *  out. Other surfaces link here rather than growing a second list.
 *
 *  On the QR: `pair/start` returns a `pairing_url` that already contains the code, which is what
 *  makes it actionable on its own — the QR is a RENDERING of that URL, not a separate mechanism.
 *  `MC-8` supplies the image (`PairingQr` + `lib/qr.ts`, no new dependency); the URL and the code
 *  stay on screen beside it, because a camera that will not focus must not be the only way in. */
export function DevicesPanel() {
  const { data, error: loadErr, refresh } = useQuery('settings:devices', () => api.devices())
  const [pairing, setPairing] = useState<DevicePairStart | null>(null)
  const [starting, setStarting] = useState(false)
  const [left, setLeft] = useState(0)
  // ── What the pairing flow SAYS, and where it puts you ─────────────────────────────────────────
  //
  // Measured on this panel with the keyboard: focus was on "Pair a device", Enter generated a code,
  // and focus landed on **`<body>`** — the button that had focus is replaced by the code view, so the
  // user's place is simply gone. And the flow's ONE live region was the ticking countdown: six
  // distinct texts in six seconds inside a `role="status"`, i.e. ~300 announcements for a 5-minute
  // code, while the one fact worth announcing — a code is ready — was never announced at all,
  // because that region is mounted together with its content.
  //
  // So: a stable region (always present, empty when idle — the shape `ResultAnnouncement` and
  // `Toaster` already use) carries the two EVENTS, the countdown keeps its words and tone but stops
  // being live, and focus moves to the code itself.
  const announce = useRef('')
  const [said, setSaid] = useState('')
  const codeRef = useRef<HTMLDivElement | null>(null)
  const [revoking, setRevoking] = useState<string | null>(null)

  // One ticking clock for the code's countdown, alive only while a code is on screen.
  useEffect(() => {
    if (!pairing) return
    setLeft(secsLeft(pairing.expires_at))
    const t = setInterval(() => setLeft(secsLeft(pairing.expires_at)), 1000)
    return () => clearInterval(t)
  }, [pairing])

  const startPairing = useCallback(() => {
    setStarting(true)
    api.devicePairStart()
      .then((p) => { setLeft(secsLeft(p.expires_at)); setPairing(p) })
      .catch((e) => notify(`Couldn't start pairing: ${msg(e)}`, 'error'))
      .finally(() => setStarting(false))
  }, [])

  // A revoke names the device it is about to lock out, and a FAILED revoke is reported. The
  // silent-failure shape matters more than usual here: the owner is told a device is locked out,
  // and would otherwise stop looking at a device that still holds a live session.
  const revoke = async (device: DeviceRec) => {
    const name = rowName(device)
    // THIS device signs itself out through the one sign-out route, then reloads onto the sign-in
    // page — revoking its own row would leave this tab on a dashboard that can no longer load.
    if (device.current) {
      const ok = await confirm({
        title: 'Sign out of this device?',
        body: `${name}, the device you are using now, is signed out at once, and shows how to sign back in.`,
        danger: true,
        confirmLabel: 'Sign out',
      })
      if (!ok) return
      setRevoking(device.id)
      try {
        await api.authLogout()
        window.location.reload()
      } catch (e) {
        notify(`Couldn't sign out: ${msg(e)}`, 'error')
        setRevoking(null)
      }
      return
    }
    const way = device.issuer === 'pair'
      ? 'To use it again, pair it again with a new code.'
      : device.pool === 'token'
        ? 'Anything still using this token will be refused.'
        : 'It will be told why the next time it is used, and can sign in again.'
    const ok = await confirm({
      title: `Sign out ${name}?`,
      body: `${name} will be signed out of this gateway immediately. ${way}`,
      danger: true,
      confirmLabel: 'Sign out',
    })
    if (!ok) return
    setRevoking(device.id)
    try {
      await api.deviceRevoke(device.id)
      notify(`${name} is signed out.`, 'success')
      refresh()
    } catch (e) {
      notify(`Couldn't sign out ${name}: ${msg(e)}`, 'error')
    } finally {
      setRevoking(null)
    }
  }

  const [signingOutOthers, setSigningOutOthers] = useState(false)
  // Every device and token but this one — the answer to a lost phone, or a row you don't know.
  const revokeOthers = async (others: number) => {
    const ok = await confirm({
      title: 'Sign out all other devices?',
      body: `${others === 1 ? 'One other device or token' : `${others} other devices and tokens`} will be signed out immediately, and each is told why the next time it is used. This device stays signed in.`,
      danger: true,
      confirmLabel: 'Sign out all others',
    })
    if (!ok) return
    setSigningOutOthers(true)
    try {
      const { revoked } = await api.devicesRevokeOthers()
      notify(`${revoked === 1 ? 'One other device is' : `${revoked} other devices are`} signed out.`, 'success')
      refresh()
    } catch (e) {
      notify(`Couldn't sign out the other devices: ${msg(e)}`, 'error')
    } finally {
      setSigningOutOthers(false)
    }
  }

  // 🪤 THESE HOOKS SIT ABOVE THE LOADING/ERROR EARLY RETURNS ON PURPOSE. Placed after them, they run
  // on some renders and not others — React error #310 ("rendered more hooks than during the previous
  // render"), which took the whole panel down to its Retry state the first time I wrote this.
  const expired = pairing != null && left <= 0

  // A NEW code arrived (first one, or "New code"): say so once, and take focus to it. Keyed on the
  // code itself, so a re-render or a countdown tick cannot re-announce or steal focus.
  useEffect(() => {
    if (!pairing) { announce.current = ''; setSaid(''); return }
    if (announce.current === pairing.code) return
    announce.current = pairing.code
    const mins = Math.max(1, Math.round((pairing.expires_in ?? 300) / 60))
    setSaid(`Pairing code ${pairing.code} is ready. It expires in about ${mins} minute${mins === 1 ? '' : 's'}.`)
    codeRef.current?.focus()
  }, [pairing])

  // The expiry is an EVENT worth one announcement — unlike the second-by-second countdown, which is
  // a value and now says nothing.
  useEffect(() => {
    if (expired) setSaid('This pairing code has expired. Generate another.')
  }, [expired])

  if (!data && loadErr) return <LoadError what="devices" error={loadErr} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={2} what="devices" />

  const signedIn = data.filter((d) => d.pool !== 'token')
  const tokens = data.filter((d) => d.pool === 'token')
  const others = data.filter((d) => !d.current).length

  const renderRow = (d: DeviceRec) => {
    const kind = d.pool === 'token' ? TOKEN_KIND : KINDS[d.kind] ?? KINDS.unknown
    const KindIcon = kind.icon
    const name = rowName(d)
    return (
      <div key={d.id}
        className="flex items-center justify-between gap-l border-b border-outline-variant/30 py-3 last:border-0">
        <div className="flex min-w-0 items-start gap-3">
          <KindIcon size={18} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-s">
              <span data-type="body-s" className="truncate text-on-surface">{name}</span>
              {d.current && <StatusPill tone="primary">This device</StatusPill>}
            </div>
            {/* Every column the list owes the owner, in one readable line: kind · last seen ·
                where · how it signed in. `last_seen` of 0 means it has never made an authorized
                request, and must read as "never" — NOT as the sign-in time, which would make an
                abandoned device look active. */}
            <div data-type="body-s" className="mt-0.5 text-on-surface-low">
              {kind.label}
              {' · '}
              <span>Last seen {d.last_seen > 0 ? relPast(d.last_seen) : 'never'}</span>
              {d.ip ? <>{' · '}<span>from {d.ip}</span></> : null}
              {' · '}
              <span>{issuerLabel(d)}</span>
            </div>
            <div data-type="caption" className="mt-0.5 text-on-surface-low/80">
              {signedInLine(d)}
              {d.expires_at > 0 ? ` · ${d.pool === 'token' ? 'stops working' : 'session expires'} ${absTime(d.expires_at)}` : ''}
            </div>
          </div>
        </div>
        <Button size="xs" variant="danger" onClick={() => revoke(d)}
          loading={revoking === d.id} ariaLabel={d.current ? 'Sign out of this device' : `Sign out ${name}`}>
          Sign out
        </Button>
      </div>
    )
  }

  return (
    <div>
      <PanelHeader
        title="Devices"
        hint="Everything signed in to this gateway: your browsers, paired phones and the desktop app, and the tokens the CLI and scripts use. Sign out anything you don't recognise — it is told why the next time it is used."
      />

      <Section
        title="Pair a device"
        hint="Open the link on the other device — on the same network — and it joins with the code below. The code is single-use and short-lived."
      >
        <div className="rounded-lg bg-surface-container px-4 py-4">
          {!pairing ? (
            <div className="flex flex-wrap items-center justify-between gap-l">
              <p data-type="body-s" className="min-w-0 flex-1 text-on-surface-low">
                Generates a one-time code and a link for the device to open.
              </p>
              <Button size="sm" onClick={startPairing} loading={starting} ariaLabel="Pair a device">
                <QrCode size={16} /> Pair a device
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-l">
              {/* The scannable form, from `pair/start`'s `pairing_url` verbatim. One scan
                  is enough because the code rides inside that URL; the code is shown beside it
                  anyway, because a camera that will not focus must not be the only way in. */}
              <div className="flex flex-wrap items-start gap-l">
                <PairingQr url={pairing.pairing_url} expired={expired} />

                {/* AN EXPIRED PAYLOAD IS WITHDRAWN, NOT DIMMED. The gateway refuses a dead code
                    (`redeem_code` → `expired`), so leaving it on screen invites the owner to carry
                    a string to their phone that cannot possibly work, and to read the failure as
                    "pairing is broken" rather than "that code ran out". */}
                {expired ? (
                  <p data-type="body-s" className="min-w-0 flex-1 text-on-surface-low">
                    The code and link are no longer shown — this gateway refuses an expired code,
                    so there is nothing here that would still work.
                  </p>
                ) : (
                  /* The programmatic focus target: `tabIndex={-1}` + `role="group"` + a name, so landing
                     here announces what it is rather than a bare container. It is NOT in the tab order
                     (−1), so nothing changes for a user tabbing through the card. */
                  /* Suppressing the focus ring is correct here, and this is the one site
                     `focusRingSurvival` still counts: it is a PROGRAMMATIC focus target
                     (`tabIndex={-1}`, focused via `codeRef` so a screen reader announces the pairing
                     code), not a keyboard stop. A ring would draw around the whole block for a focus
                     the user never initiated. (Worded without the utility's literal name on purpose —
                     that scanner reads comments as well as code.) */
                  <div ref={codeRef} tabIndex={-1} role="group" aria-label="Pairing code and link"
                    className="min-w-0 flex-1 flex flex-col gap-l outline-none">
                    <div>
                      <div data-type="body-s" className="text-on-surface-low">Code</div>
                      <div className="mt-1 flex items-center gap-s">
                        {/* No `aria-label` here: `<code>` carries no role, so an aria-label on it is
                            ignored by assistive tech (and flagged by axe, which now scans this
                            route). The grouped code reads correctly as text, and the visible label
                            above names it. */}
                        <code className="select-all font-mono text-on-surface text-[1.375rem] tracking-[0.12em]">
                          {pairing.code}
                        </code>
                        <CopyButton value={pairing.code} label="pairing code" />
                      </div>
                    </div>

                    <div>
                      <div data-type="body-s" className="text-on-surface-low">Link to open on the device</div>
                      <div className="mt-1 flex items-start gap-s">
                        <code data-type="body-s" className="min-w-0 select-all break-all font-mono text-on-surface">
                          {pairing.pairing_url}
                        </code>
                        <CopyButton value={pairing.pairing_url} label="pairing link" />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* The countdown is stated in words as well as tone — an expiry communicated only
                  by colour would fail 1.4.1 — and an expired code says so instead of counting
                  into negative numbers or looking valid forever. */}
              <div className="flex flex-wrap items-center justify-between gap-l border-t border-outline-variant/30 pt-3">
                {/* 🪤 THIS USED TO BE `role="status"`, which made the only live region in the flow a
                    per-second counter: measured six distinct texts in six seconds, ~300 for one code.
                    A ticking VALUE is not an event. The words and the tone stay (an expiry carried by
                    colour alone would fail 1.4.1); the announcing moved to the region below. */}
                <span
                  data-type="body-s" className={`inline-flex items-center gap-1.5 ${expired ? 'text-warn' : 'text-on-surface-low'}`}
                >
                  {expired ? <XCircle size={14} /> : null}
                  {expired ? 'This code has expired — generate another.' : `Expires in ${mmss(left)}`}
                </span>
                <div className="flex items-center gap-s">
                  <Button size="xs" variant="secondary" onClick={startPairing} loading={starting}
                    ariaLabel="Generate a new pairing code">
                    New code
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => { setPairing(null); refresh() }}
                    ariaLabel="Done pairing">
                    Done
                  </Button>
                </div>
              </div>
            </div>
          )}
          {/* Always mounted, empty when idle — a region created together with its text is not
              reliably observed, which is exactly how "a code is ready" went unannounced. */}
          <div role="status" aria-live="polite" className="sr-only">{said}</div>
        </div>
      </Section>

      <Section
        title={`Signed in${signedIn.length ? ` (${signedIn.length})` : ''}`}
        hint="Browsers, paired devices and the desktop app. Each kind has a limit of 20; past it, the one used least recently is signed out and told why, so a script minting tokens never signs one of these out."
        right={others > 0 ? (
          <Button size="xs" variant="secondary" onClick={() => revokeOthers(others)} loading={signingOutOthers}
            ariaLabel="Sign out all other devices">
            Sign out all other devices
          </Button>
        ) : undefined}
      >
        {signedIn.length === 0 ? (
          /* The action is real (it opens the same pairing flow as the section above), but its
             label must NOT repeat that button's: two controls with one accessible name make the
             action ambiguous to anyone navigating by name. Distinct wording, one behaviour. */
          <EmptyState
            icon={MonitorSmartphone}
            title="No devices signed in"
            hint="This browser reached the gateway without a session of its own (a local-network bypass or no-auth mode). Pair a phone or open a personalclaw token link in a browser, and it appears here."
            action={{ label: 'Pair your first device', onClick: startPairing, icon: QrCode }}
          />
        ) : (
          <RowGroup>{signedIn.map(renderRow)}</RowGroup>
        )}
      </Section>

      {tokens.length > 0 && (
        <Section title={`Tokens (${tokens.length})`}
          hint="Links and tokens that no browser has opened: from personalclaw token, personalclaw run, a script, or the gateway's startup line. Each lasts what it was minted for; up to 20 can be live at once, and past that the one used least recently is signed out.">
          <RowGroup>{tokens.map(renderRow)}</RowGroup>
        </Section>
      )}
    </div>
  )
}
