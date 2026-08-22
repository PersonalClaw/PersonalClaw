import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check, Copy, Laptop, MonitorSmartphone, QrCode, Smartphone, Terminal, Globe, XCircle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api } from '../../lib/api'
import type { DeviceRec, DevicePairStart } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section } from './settingsUI'
import { Button } from '../../ui/Button'
import { EmptyState, FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { relPast, absTime } from '../schedule/scheduleMeta'

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

/** Provenance, in the owner's words. `pair` is the only issuer this list can currently show —
 *  an owner-token session has no device row — but the field is rendered rather than assumed,
 *  because the whole point of storing it was that the registry can say where a session came
 *  from instead of guessing. An unrecognized value renders as itself, never as blank. */
function issuerLabel(issuer: string): string {
  if (issuer === 'pair') return 'Paired with a code'
  if (issuer === 'unknown') return 'Unknown'
  return issuer
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
 *  leaves the owner believing they hold a code they do not. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setDone(true)
      setTimeout(() => setDone(false), 1500)
    } catch (e) {
      notify(`Couldn't copy the ${label} — select it and copy manually. ${msg(e)}`, 'error')
    }
  }
  return (
    <Button size="xs" variant="secondary" onClick={copy} ariaLabel={done ? `${label} copied` : `Copy ${label}`}>
      {done ? <Check size={14} /> : <Copy size={14} />} {done ? 'Copied' : 'Copy'}
    </Button>
  )
}

/** Settings → Devices — the ONE device registry in the product (COMPANION-APPS C2 / CA-2).
 *
 *  Every row is derived from a live session, so this list IS the answer to "what can reach this
 *  gateway right now": a revoke removes the row because it removed the session, not because the
 *  UI hid it. Other surfaces link here rather than growing a second list.
 *
 *  On the QR: `pair/start` returns a `pairing_url` that already contains the code, which is what
 *  makes it actionable on its own — the QR is a RENDERING of that URL, not a separate mechanism.
 *  This ships the URL and the code (what a second browser on the LAN actually needs) and marks
 *  where the image belongs; adding an encoder is a dependency decision, and the repo's own
 *  precedent is that TOTP enrollment ships no QR either (AccountPanel sends you to the CLI). */
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
    const name = device.name || 'this device'
    const ok = await confirm({
      title: `Revoke ${name}?`,
      body: `${name} will lose access to this gateway immediately and will have to pair again with a new code.`,
      danger: true,
      confirmLabel: 'Revoke access',
    })
    if (!ok) return
    setRevoking(device.id)
    try {
      await api.deviceRevoke(device.id)
      notify(`${name} can no longer reach this gateway.`, 'success')
      refresh()
    } catch (e) {
      notify(`Couldn't revoke ${name}: ${msg(e)}`, 'error')
    } finally {
      setRevoking(null)
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

  return (
    <div>
      <PanelHeader
        title="Devices"
        hint="Phones, tablets and other browsers you have paired with this gateway. Each one holds an ordinary session, so revoking a device logs exactly that device out."
      />

      <Section
        title="Pair a device"
        hint="Open the link on the other device — on the same network — and it joins with the code below. The code is single-use and short-lived."
      >
        <div className="rounded-lg bg-surface-container px-4 py-4">
          {!pairing ? (
            <div className="flex flex-wrap items-center justify-between gap-l">
              <p className="min-w-0 flex-1 text-on-surface-low text-[0.8125rem]">
                Generates a one-time code and a link for the device to open.
              </p>
              <Button size="sm" onClick={startPairing} loading={starting} ariaLabel="Pair a device">
                <QrCode size={16} /> Pair a device
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-l">
              {/* Where the QR image belongs. Deliberately a labelled placeholder and not a
                  silent omission: the owner should be able to see that the scannable form is
                  not here yet, and still complete the pairing from the link and the code. */}
              <div className="flex flex-wrap items-start gap-l">
                <div
                  className="flex size-[148px] shrink-0 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-outline-variant px-2 text-center"
                  role="img"
                  aria-label="QR code not available — use the pairing link and code shown beside this"
                >
                  <QrCode size={28} className="text-on-surface-low" aria-hidden="true" />
                  <span className="text-on-surface-low text-[0.75rem] leading-tight">
                    No scannable code yet — use the link and code
                  </span>
                </div>

                {/* The programmatic focus target: `tabIndex={-1}` + `role="group"` + a name, so landing
                    here announces what it is rather than a bare container. It is NOT in the tab order
                    (−1), so nothing changes for a user tabbing through the card. */}
                {/* Suppressing the focus ring is correct here, and this is the one site
                    `focusRingSurvival` still counts: it is a PROGRAMMATIC focus target
                    (`tabIndex={-1}`, focused via `codeRef` so a screen reader announces the pairing
                    code), not a keyboard stop. A ring would draw around the whole block for a focus
                    the user never initiated. (Worded without the utility's literal name on purpose —
                    that scanner reads comments as well as code.) */}
                <div ref={codeRef} tabIndex={-1} role="group" aria-label="Pairing code and link"
                  className="min-w-0 flex-1 flex flex-col gap-l outline-none">
                  <div>
                    <div className="text-on-surface-low text-[0.8125rem]">Code</div>
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
                    <div className="text-on-surface-low text-[0.8125rem]">Link to open on the device</div>
                    <div className="mt-1 flex items-start gap-s">
                      <code className="min-w-0 select-all break-all font-mono text-on-surface text-[0.8125rem]">
                        {pairing.pairing_url}
                      </code>
                      <CopyButton value={pairing.pairing_url} label="pairing link" />
                    </div>
                  </div>
                </div>
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
                  className={`inline-flex items-center gap-1.5 text-[0.8125rem] ${expired ? 'text-warn' : 'text-on-surface-low'}`}
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

      <Section title={`Paired devices${data.length ? ` (${data.length})` : ''}`}
        hint="Revoking a device drops its session on this gateway and on disk, so it stays locked out across a restart.">
        {data.length === 0 ? (
          /* The action is real (it opens the same pairing flow as the section above), but its
             label must NOT repeat that button's: two controls with one accessible name make the
             action ambiguous to anyone navigating by name. Distinct wording, one behaviour. */
          <EmptyState
            icon={MonitorSmartphone}
            title="No devices paired"
            hint="Nothing but this browser can reach your gateway with a paired session."
            action={{ label: 'Pair your first device', onClick: startPairing, icon: QrCode }}
          />
        ) : (
          <div className="rounded-lg bg-surface-container px-4 py-1">
            {data.map((d) => {
              const kind = KINDS[d.kind] ?? KINDS.unknown
              const KindIcon = kind.icon
              const name = d.name || 'Unnamed device'
              return (
                <div key={d.id}
                  className="flex items-center justify-between gap-l border-b border-outline-variant/30 py-3 last:border-0">
                  <div className="flex min-w-0 items-start gap-3">
                    <KindIcon size={18} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
                    <div className="min-w-0">
                      <div className="truncate text-on-surface text-[0.8125rem]">{name}</div>
                      {/* Every column the registry owes the owner, in one readable line:
                          kind · last seen · issuer · paired · expires. `last_seen` of 0 means the
                          device has never made an authorized request, and must read as "never" —
                          NOT as the pairing time, which would make an abandoned device look active. */}
                      <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">
                        {kind.label}
                        {' · '}
                        <span>Last seen {d.last_seen > 0 ? relPast(d.last_seen) : 'never'}</span>
                        {' · '}
                        <span>{issuerLabel(d.issuer)}</span>
                      </div>
                      <div className="mt-0.5 text-on-surface-low/80 text-[0.75rem]">
                        Paired {d.minted_at > 0 ? relPast(d.minted_at) : 'unknown'}
                        {d.expires_at > 0 ? ` · session expires ${absTime(d.expires_at)}` : ''}
                      </div>
                    </div>
                  </div>
                  <Button size="xs" variant="danger" onClick={() => revoke(d)}
                    loading={revoking === d.id} ariaLabel={`Revoke ${name}`}>
                    Revoke
                  </Button>
                </div>
              )
            })}
          </div>
        )}
      </Section>
    </div>
  )
}
