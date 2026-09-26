import { useState } from 'react'
import { ChevronDown, KeyRound, AlertTriangle, CheckCircle2, Clock, TerminalSquare, RefreshCw, Beaker, Plug, PlugZap, Loader2, HelpCircle } from 'lucide-react'
import { api, type SettingsProvider, type AgentRuntime, type ChannelRuntime } from '../../lib/api'
import { reportingWrite } from '../../app/reportingWrite'
import { Toggle } from './settingsUI'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { ProviderConfigForm } from './ProviderConfigForm'
import { fvs } from '../../design/fontWeight'

/** One provider card: identity + enable toggle, with the provider's own
 *  schema-driven config tucked UNDER the toggle (expand chevron, only when
 *  enabled + the provider has a settingsSchema). Agent cards also show a
 *  runtime-readiness chip + Sign-in when their runtime needs login.
 *
 *  The config accordion is fully controlled by the parent so it can ride the URL
 *  (?open=<provider>, push → Back collapses it). One provider's config is open at
 *  a time across the whole panel; opening another closes the first. */
export function ProviderCard({ ext, runtime, channel, open, onOpenChange, onChanged, onSignIn, onRecheck, onChannelChanged }: {
  ext: SettingsProvider; runtime?: AgentRuntime; channel?: ChannelRuntime; open: boolean; onOpenChange: (v: boolean) => void; onChanged: () => void
  onSignIn?: (rt: AgentRuntime) => void; onRecheck?: () => Promise<void> | void; onChannelChanged?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [rechecking, setRechecking] = useState(false)
  const [measuring, setMeasuring] = useState(false)
  const hasConfig = !!ext.enabled && ext.provider?.hasConfigSchema === true
  // Measured by the gateway in a child process, never on this request: a card the gateway has
  // not measured yet reads `checking` (the list no longer waits on an app's hook — one of them
  // held it for 171 s). `unknown` is "the check could not answer", which is not the app's "no".
  const availability = ext.availability?.state ?? 'available'
  const unavailable = availability === 'unavailable'
  const unknown = availability === 'unknown'
  const checking = availability === 'checking' || measuring
  const reason = ext.availability?.reason ?? ''
  // A managed provider is an app (install/uninstall = on/off). A non-managed one
  // is an always-on native built-in — mandatory, shown without a toggle.
  /** What the card's own title shows — the subject every control here acts on. */
  const who = ext.displayName || ext.name
  const managed = ext.managed === true
  // "Check again" — the facts a hook reads change exactly when the user installs a package or
  // signs a CLI in, so the card offers the re-measure beside the answer it would change.
  const checkAgain = async () => {
    setMeasuring(true)
    try {
      if (await reportingWrite(`re-check ${who}`, () => api.recheckProviderAvailability(ext.name))) onChanged()
    } finally { setMeasuring(false) }
  }

  // Reported, and re-read either way. A refused enable (one of its tools has a name another
  // provider holds) used to reject unhandled: the switch sprang back and nothing said why. The
  // server's sentence is the toast, and the re-read puts the same sentence under the card.
  const toggle = async () => {
    setBusy(true)
    try {
      await reportingWrite(`turn ${who} ${ext.enabled ? 'off' : 'on'}`, () =>
        ext.enabled ? api.disableProvider(ext.name) : api.enableProvider(ext.name))
      onChanged()
    } finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: unavailable ? 0.6 : busy ? 0.6 : 1 }}>
      <div className="flex items-center gap-3">
        {/* enabled dot */}
        <span className="size-2 shrink-0 rounded-full" style={{ background: ext.enabled && !unavailable ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span data-type="title-m" className="truncate text-on-surface" style={fvs(500)}>{ext.displayName || ext.name}</span>
            {ext.version && <span data-type="caption" className="text-on-surface-low">v{ext.version}</span>}
            {(ext.provider?.capabilities ?? []).map((c) => (
              <span key={c} data-type="caption" className="rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface-low">{c}</span>
            ))}
            {unavailable && <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low"><AlertTriangle size={10} /> unavailable</span>}
            {unknown && !checking && <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low"><HelpCircle size={10} /> couldn't check</span>}
            {checking && <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low" title="Checking whether it can run on this machine"><Loader2 size={10} className="animate-spin" /> checking</span>}
          </div>
          {ext.description && <p data-type="body-s" className="mt-0.5 truncate text-on-surface-low">{ext.description}</p>}
        </div>

        {runtime && !unavailable && <RuntimeChip state={runtime.state} />}
        {/* Every control in this card is ONE PER PROVIDER, so a static verb announces as N identical
            entries — measured live on this surface: 11 buttons, every one of them named "Configure".
            Each name now carries `who`, the card's own visible title; visible text and tooltips are
            untouched. The Toggle below already named its provider, so the form is this file's own.
            🪤 This comment sits BEFORE the conditional, not after its `&& (` — a JSX comment in an
            EXPRESSION position is an object literal, which is four TS1005s and no comment at all. */}
        {runtime && runtime.state === 'needs_login' && runtime.login_command && onSignIn && (
          <button type="button" onClick={() => onSignIn(runtime)} aria-label={`Sign in: ${who}`}
            data-type="caption" className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-2.5 py-1 text-on-surface hover:bg-surface-highest">
            <KeyRound size={12} /> Sign in
          </button>
        )}
        {/* Manual availability re-check — forces a fresh readiness probe. `loading`, not
            `disabled`: a probe in flight is working, not unavailable, and the primitive's own
            spinner replaces spinning this button's RefreshCw by hand.
            🪤 Same trap as the comment above: this sits BEFORE the conditional, not after its
            `&& (` — a JSX comment in an EXPRESSION position is an object literal. */}
        {runtime && runtime.type !== 'native' && !unavailable && onRecheck && (
          <SquareIconButton label={`Check availability: ${who}`} title="Check availability" loading={rechecking} className="shrink-0"
            onClick={async () => { setRechecking(true); try { await onRecheck() } finally { setRechecking(false) } }}>
            <RefreshCw size={14} />
          </SquareIconButton>
        )}
        {/* Managed app provider → install/uninstall toggle. Native built-in →
            always-on (mandatory): no toggle, just a quiet badge. */}
        {!unavailable && (managed
          ? <Toggle on={ext.enabled} onChange={toggle} label={`Toggle ${ext.name}`} />
          : <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low" title="Built-in provider — always available">Always on</span>
        )}
        {/* Configure is a DISCLOSURE, not a toggle: it reveals the config form below. `ariaExpanded`
            rather than `on` — the tint is identical, the announcement is the true one.
            🪤 And this comment sits before the conditional, not after its `&& (`. */}
        {hasConfig && (
          <SquareIconButton label={`Configure: ${who}`} title="Configure" ariaExpanded={open}
            onClick={() => onOpenChange(!open)} className="shrink-0">
            <ChevronDown size={16} className="transition-transform" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
          </SquareIconButton>
        )}
      </div>

      {/* unavailable / unmeasurable reason, with the re-measure beside it; runtime detail */}
      {(unavailable || unknown) && !checking && (
        <div data-type="caption" className="mt-2 flex items-start gap-1.5 text-on-surface-low">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">{reason || (unknown ? 'Its availability could not be checked.' : "Its app reports that it can't run on this machine.")}</span>
          <button type="button" onClick={checkAgain} aria-label={`Check again: ${who}`}
            className="shrink-0 underline hover:text-on-surface">Check again</button>
        </div>
      )}
      {runtime && runtime.detail && runtime.state !== 'ready' && !unavailable && (
        <div data-type="caption" className="mt-2 flex items-start gap-1.5 text-on-surface-low"><TerminalSquare size={12} className="mt-0.5 shrink-0" /> {runtime.detail}</div>
      )}
      {ext.error && <div data-type="caption" className="mt-2 flex items-center gap-1.5" style={{ color: 'var(--color-danger)' }}><AlertTriangle size={12} /> {ext.error}</div>}

      {/* Live channel runtime — connection health + connect/disconnect/test. This
          is the RUNTIME view (is the transport actually connected right now),
          distinct from the enable/config surface above. */}
      {channel && <ChannelRuntimeRow channel={channel} onChanged={onChannelChanged} />}

      {/* A save rebuilds the provider — for a channel, its receiver restarts on what was saved —
          so the card re-reads what the save changed instead of showing the status from before. */}
      {open && hasConfig && <ProviderConfigForm name={ext.name} onSaved={onChanged} />}
    </div>
  )
}

const CHANNEL_STATE_TONE: Record<string, string> = {
  connected: 'var(--color-ok)', ready: 'var(--color-ok)', online: 'var(--color-ok)',
  error: 'var(--color-danger)', offline: 'var(--color-on-surface-low)',
}

/** The status word beside the dot, from what the channel is doing (`health.state`) — never from
 *  `connected`, which says only that a token is present. The word used to come from `connected`,
 *  so a channel whose receiver never started read "Connected" beside a red dot and a detail saying
 *  it was not receiving. `starting` is core's own state while it starts the receiver. */
const CHANNEL_STATE_LABEL: Record<string, string> = {
  ready: 'Connected', starting: 'Starting…', error: 'Error', offline: 'Not connected',
}

/** The live connection strip for a channel provider: a health dot + state/detail,
 *  plus Test / Connect|Disconnect actions that hit the /api/channels runtime. */
function ChannelRuntimeRow({ channel, onChanged }: { channel: ChannelRuntime; onChanged?: () => void }) {
  const [busy, setBusy] = useState('')
  const [detail, setDetail] = useState<string | null>(null)
  const state = channel.health.state
  const tone = CHANNEL_STATE_TONE[state] ?? 'var(--color-on-surface-low)'
  const act = async (kind: 'test' | 'connect' | 'disconnect') => {
    if (busy) return
    setBusy(kind); setDetail(null)
    try {
      const r = kind === 'test' ? await api.testChannel(channel.name)
        : kind === 'connect' ? await api.connectChannel(channel.name)
        : await api.disconnectChannel(channel.name)
      const d = (r as { health?: { detail?: string }; detail?: string })
      setDetail(d.detail ?? d.health?.detail ?? (kind === 'test' ? 'Tested' : kind === 'connect' ? 'Connected' : 'Disconnected'))
      onChanged?.()
    } catch (e) { setDetail(e instanceof Error ? e.message : 'Failed') }
    finally { setBusy('') }
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-outline-variant/30 pt-2">
      <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-var">
        {state === 'starting'
          ? <Loader2 size={10} className="animate-spin" aria-hidden />
          : <span className="size-2 rounded-full" style={{ background: tone }} />}
        {CHANNEL_STATE_LABEL[state] ?? state}
      </span>
      {(detail ?? channel.health.detail) && <span data-type="caption" className="text-on-surface-low truncate max-w-[60%]">{detail ?? channel.health.detail}</span>}
      <div className="ml-auto flex items-center gap-1.5">
        {/* One strip per channel provider, so these three share names across rows too. */}
        <button type="button" onClick={() => act('test')} disabled={!!busy} aria-label={`Test: ${channel.name}`}
          data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-on-surface-var hover:text-on-surface disabled:opacity-50">
          {busy === 'test' ? <Loader2 size={11} className="animate-spin" /> : <Beaker size={11} />} Test
        </button>
        {channel.connected
          ? <button type="button" onClick={() => act('disconnect')} disabled={!!busy} aria-label={`Disconnect: ${channel.name}`}
              data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-on-surface-var hover:text-danger disabled:opacity-50">
              {busy === 'disconnect' ? <Loader2 size={11} className="animate-spin" /> : <Plug size={11} />} Disconnect
            </button>
          : <button type="button" onClick={() => act('connect')} disabled={!!busy} aria-label={`Connect: ${channel.name}`}
              data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-on-surface-var hover:text-primary disabled:opacity-50">
              {busy === 'connect' ? <Loader2 size={11} className="animate-spin" /> : <PlugZap size={11} />} Connect
            </button>}
      </div>
    </div>
  )
}

// We don't have the schema in the list payload; the form fetches it. Show the
function RuntimeChip({ state }: { state: string }) {
  const map: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
    ready: { icon: <CheckCircle2 size={12} />, label: 'Ready', color: 'var(--color-success)' },
    needs_login: { icon: <KeyRound size={12} />, label: 'Needs sign-in', color: 'var(--color-warning)' },
    not_found: { icon: <AlertTriangle size={12} />, label: 'Not found', color: 'var(--color-on-surface-low)' },
    timeout: { icon: <Clock size={12} />, label: 'Slow to start', color: 'var(--color-warning)' },
    // Never measured yet: one background probe is running. A plain read no longer spawns the
    // runtime, so "not answered yet" is a state of its own rather than an error.
    checking: { icon: <Loader2 size={12} className="animate-spin" />, label: 'Checking…', color: 'var(--color-on-surface-low)' },
    error: { icon: <AlertTriangle size={12} />, label: 'Error', color: 'var(--color-danger)' },
  }
  const m = map[state] ?? map.error
  return <span data-type="caption" className="inline-flex shrink-0 items-center gap-1" style={{ color: m.color }}>{m.icon} {m.label}</span>
}
