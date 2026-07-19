import { useState } from 'react'
import { ChevronDown, KeyRound, AlertTriangle, CheckCircle2, Clock, TerminalSquare, RefreshCw, Beaker, Plug, PlugZap, Loader2 } from 'lucide-react'
import { api, type SettingsProvider, type AgentRuntime, type ChannelRuntime } from '../../lib/api'
import { Toggle } from './settingsUI'
import { ProviderConfigForm } from './ProviderConfigForm'

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
  const hasConfig = !!ext.enabled && ext.provider?.hasConfigSchema === true
  const unavailable = ext.available === false
  // A managed provider is an app (install/uninstall = on/off). A non-managed one
  // is an always-on native built-in — mandatory, shown without a toggle.
  const managed = ext.managed === true

  const toggle = async () => {
    setBusy(true)
    try { ext.enabled ? await api.disableProvider(ext.name) : await api.enableProvider(ext.name); onChanged() }
    finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: unavailable ? 0.6 : busy ? 0.6 : 1 }}>
      <div className="flex items-center gap-3">
        {/* enabled dot */}
        <span className="size-2 shrink-0 rounded-full" style={{ background: ext.enabled && !unavailable ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{ext.displayName || ext.name}</span>
            {ext.version && <span className="text-on-surface-low text-[0.68rem]">v{ext.version}</span>}
            {(ext.provider?.capabilities ?? []).map((c) => (
              <span key={c} className="rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.65rem]">{c}</span>
            ))}
            {unavailable && <span className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.65rem]"><AlertTriangle size={10} /> unavailable</span>}
          </div>
          {ext.description && <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{ext.description}</p>}
        </div>

        {runtime && !unavailable && <RuntimeChip state={runtime.state} />}
        {runtime && runtime.state === 'needs_login' && runtime.login_command && onSignIn && (
          <button type="button" onClick={() => onSignIn(runtime)}
            className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-2.5 py-1 text-on-surface text-[0.75rem] hover:bg-surface-highest">
            <KeyRound size={12} /> Sign in
          </button>
        )}
        {/* Manual availability re-check — forces a fresh readiness probe. */}
        {runtime && runtime.type !== 'native' && !unavailable && onRecheck && (
          <button type="button" disabled={rechecking}
            onClick={async () => { setRechecking(true); try { await onRecheck() } finally { setRechecking(false) } }}
            aria-label="Check availability" title="Check availability"
            className="grid size-7 shrink-0 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface disabled:opacity-50">
            <RefreshCw size={14} className={rechecking ? 'animate-spin' : ''} />
          </button>
        )}
        {/* Managed app provider → install/uninstall toggle. Native built-in →
            always-on (mandatory): no toggle, just a quiet badge. */}
        {!unavailable && (managed
          ? <Toggle on={ext.enabled} onChange={toggle} label={`Toggle ${ext.name}`} />
          : <span className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.65rem]" title="Built-in provider — always available">Always on</span>
        )}
        {hasConfig && (
          <button type="button" onClick={() => onOpenChange(!open)} aria-label="Configure" title="Configure"
            className="grid size-7 shrink-0 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface"
            style={open ? { color: 'var(--color-primary)' } : undefined}>
            <ChevronDown size={16} className="transition-transform" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
          </button>
        )}
      </div>

      {/* unavailable reason / runtime detail */}
      {unavailable && ext.unavailableReason && (
        <div className="mt-2 flex items-start gap-1.5 text-on-surface-low text-[0.78rem]"><AlertTriangle size={12} className="mt-0.5 shrink-0" /> {ext.unavailableReason}</div>
      )}
      {runtime && runtime.detail && runtime.state !== 'ready' && !unavailable && (
        <div className="mt-2 flex items-start gap-1.5 text-on-surface-low text-[0.78rem]"><TerminalSquare size={12} className="mt-0.5 shrink-0" /> {runtime.detail}</div>
      )}
      {ext.error && <div className="mt-2 flex items-center gap-1.5 text-[0.78rem]" style={{ color: 'var(--color-danger)' }}><AlertTriangle size={12} /> {ext.error}</div>}

      {/* Live channel runtime — connection health + connect/disconnect/test. This
          is the RUNTIME view (is the transport actually connected right now),
          distinct from the enable/config surface above. */}
      {channel && <ChannelRuntimeRow channel={channel} onChanged={onChannelChanged} />}

      {open && hasConfig && <ProviderConfigForm name={ext.name} />}
    </div>
  )
}

const CHANNEL_STATE_TONE: Record<string, string> = {
  connected: 'var(--color-ok)', ready: 'var(--color-ok)', online: 'var(--color-ok)',
  error: 'var(--color-danger)', offline: 'var(--color-on-surface-low)',
}

/** The live connection strip for a channel provider: a health dot + state/detail,
 *  plus Test / Connect|Disconnect actions that hit the /api/channels runtime. */
function ChannelRuntimeRow({ channel, onChanged }: { channel: ChannelRuntime; onChanged?: () => void }) {
  const [busy, setBusy] = useState('')
  const [detail, setDetail] = useState<string | null>(null)
  const tone = CHANNEL_STATE_TONE[channel.health.state] ?? 'var(--color-on-surface-low)'
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
      <span className="inline-flex items-center gap-1.5 text-[0.78rem] text-on-surface-var">
        <span className="size-2 rounded-full" style={{ background: tone }} />
        {channel.connected ? 'Connected' : channel.health.state === 'error' ? 'Error' : 'Not connected'}
      </span>
      {(detail ?? channel.health.detail) && <span className="text-on-surface-low text-[0.72rem] truncate max-w-[60%]">{detail ?? channel.health.detail}</span>}
      <div className="ml-auto flex items-center gap-1.5">
        <button type="button" onClick={() => act('test')} disabled={!!busy}
          className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-[0.72rem] text-on-surface-var hover:text-on-surface disabled:opacity-50">
          {busy === 'test' ? <Loader2 size={11} className="animate-spin" /> : <Beaker size={11} />} Test
        </button>
        {channel.connected
          ? <button type="button" onClick={() => act('disconnect')} disabled={!!busy}
              className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-[0.72rem] text-on-surface-var hover:text-danger disabled:opacity-50">
              {busy === 'disconnect' ? <Loader2 size={11} className="animate-spin" /> : <Plug size={11} />} Disconnect
            </button>
          : <button type="button" onClick={() => act('connect')} disabled={!!busy}
              className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-[0.72rem] text-on-surface-var hover:text-primary disabled:opacity-50">
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
    error: { icon: <AlertTriangle size={12} />, label: 'Error', color: 'var(--color-danger)' },
  }
  const m = map[state] ?? map.error
  return <span className="inline-flex shrink-0 items-center gap-1 text-[0.72rem]" style={{ color: m.color }}>{m.icon} {m.label}</span>
}
