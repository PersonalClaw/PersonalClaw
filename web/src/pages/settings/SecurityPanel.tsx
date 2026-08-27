import { useState } from 'react'
import { FieldError } from '../../ui/forms'
import { unavailableWhen } from '../../ui/unavailable'
import {
  ShieldBan, ScanLine, FileCode2, EyeOff, Plus, X, Lock, Globe, MonitorOff, ShieldCheck, ShieldAlert,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import {
  api, type DesktopCapabilityWire, type EgressPolicyConfig, type DenylistBaseline,
} from '../../lib/api'
import { requestDesktopCapability } from '../../lib/desktopBridge'
import { Button } from '../../ui/Button'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section } from './settingsUI'
import { CardGridSkeleton, LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'

/** Security posture → /api/security/stats (counts) + /api/security/denied-commands
 *  (the bash denylist: always-on baseline shown read-only with its verified state; user
 *  patterns editable).
 *
 *  🔑 BOTH READS ARE BARE — no `.catch(() => null)`. On every other panel a swallowed
 *  read costs a shimmer; here it produces the one lie this surface must never tell.
 *  "Denied commands 0" and an empty built-in list are pixel-identical to "nothing is
 *  blocked", and a reader has no way to tell a working instance from a failed fetch. The
 *  rejection reaches the hook and the failure is what renders. */
export function SecurityPanel() {
  // Posture stats change slowly — persist so a revisit (and a full reload) paints
  // instantly from cache and revalidates in the background.
  const { data: s, error: loadErr, refresh: refreshStats } = useQuery(
    'settings:security', () => api.securityStats(), { persist: true },
  )
  const { data: denied, error: deniedErr, refresh: refreshDenied } = useQuery(
    'settings:denied-commands', () => api.deniedCommands(), { persist: true },
  )
  // Adding/removing a user pattern changes the denied-commands COUNT too —
  // refresh both, or the stat tile shows the stale pre-edit number.
  const onDeniedChange = () => { refreshDenied(); refreshStats() }
  // 🪤 `!s`, not `s === undefined`: the settings hub's tile SHARES the
  // `settings:security` key and still substitutes `null` on failure, persisting it to
  // sessionStorage — so this panel can be seeded with a `null` that already means
  // "failed". Both spellings of "no data" must reach the error branch.
  if (!s && loadErr) return <LoadError what="security settings" error={loadErr} onRetry={refreshStats} />
  if (!s) return <CardGridSkeleton cards={4} cols={2} what="security settings" />

  const cards: { icon: LucideIcon; label: string; value: number; hint: string }[] = [
    { icon: ShieldBan, label: 'Denied commands', value: s.denied_commands, hint: 'Shell patterns blocked from execution' },
    { icon: ScanLine, label: 'Suspicious patterns', value: s.suspicious_patterns, hint: 'Prompt-injection / exfiltration signatures watched' },
    { icon: FileCode2, label: 'Tool schemas', value: s.tool_schemas, hint: 'Tools with enforced argument validation' },
    { icon: EyeOff, label: 'Redaction paths', value: s.redaction_paths, hint: 'Sensitive paths redacted from output' },
  ]

  return (
    <div>
      <PanelHeader title="Security" hint="The enforcement posture protecting this self-hosted instance. Built-in protections are managed in code; you can extend the shell denylist below." />
      <Section title="Active protections">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {cards.map((c) => (
            <div key={c.label} className="flex items-start gap-3 rounded-lg bg-surface-container px-4 py-3">
              <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
                <c.icon size={17} className="text-primary" />
              </span>
              <div className="min-w-0">
                <div className="text-on-surface text-[1.25rem] tabular-nums" style={fvs(600)}>{c.value}</div>
                <div className="text-on-surface text-[0.8125rem]">{c.label}</div>
                <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">{c.hint}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>
      {!denied && deniedErr ? (
        <Section title="Shell denylist">
          <LoadError what="shell denylist patterns" error={deniedErr} onRetry={refreshDenied} />
        </Section>
      ) : denied ? (
        <DeniedCommandsEditor builtin={denied.builtin} user={denied.user}
          baseline={denied.baseline} userAdditions={denied.user_additions}
          onChange={onDeniedChange} />
      ) : null}
      <EgressPolicyEditor />
      <DesktopCapabilitiesPanel />
    </div>
  )
}

/** Human labels for the native capability vocabulary (DC-2). An UNMAPPED name still
 *  renders — humanized from its own id — because silently dropping a capability the
 *  gateway reported would make this panel less truthful than the API behind it. */
const DESKTOP_CAPABILITY_LABELS: Record<string, string> = {
  audio_capture: 'Microphone',
  screen_capture: 'Screen recording',
  native_notifications: 'Native notifications',
  global_hotkey: 'Global hotkey',
  tray: 'Menu-bar item',
  login_item: 'Open at login',
}

const capabilityLabel = (cap: string) =>
  DESKTOP_CAPABILITY_LABELS[cap] ?? cap.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

/** How each grant state reads to a user, and how it looks. `unavailable` and
 *  `not-determined` are deliberately NOT styled as failures — neither is a problem.
 *  `granted` uses `text-success`, not `text-primary`: the brand primary is a coral in
 *  this theme, so a granted row was rendering the same colour as a denied one — a
 *  security state a user cannot tell apart at a glance is not rendering truth. */
const GRANT_PRESENTATION: Record<DesktopCapabilityWire['granted'], { label: string; tone: string }> = {
  granted: { label: 'Granted', tone: 'text-success' },
  denied: { label: 'Denied', tone: 'text-error' },
  restricted: { label: 'Restricted by policy', tone: 'text-error' },
  'not-determined': { label: 'Not requested yet', tone: 'text-on-surface-low' },
  unavailable: { label: 'Unavailable', tone: 'text-on-surface-low' },
}

/** Settings → Security → Desktop capabilities (DC-2 T2.3).
 *
 *  Renders what the shell actually reported through `GET /api/desktop/state`, and
 *  nothing else. With no shell the state is `{connected: false, capabilities: {}}`, so
 *  this panel says the desktop app is not connected instead of listing six
 *  capabilities a browser tab could never grant. A Request button appears ONLY where
 *  the shell said `requestable` — where macOS exposes no prompt (screen recording,
 *  notification authorization) the row states where to grant it instead of offering a
 *  control that would do nothing. */
function DesktopCapabilitiesPanel() {
  const { data: ds, refresh } = useQuery(
    'settings:desktop-state', () => api.desktopState().catch(() => null),
  )
  const [busyCap, setBusyCap] = useState('')
  const [err, setErr] = useState('')
  if (!ds) return null

  const caps = Object.entries(ds.capabilities)

  const request = async (cap: string) => {
    setBusyCap(cap); setErr('')
    const res = await requestDesktopCapability(cap)
    // A null result means the page is not running inside the shell — the same
    // condition the "not connected" state below describes, reached from a stale view.
    if (!res) setErr('The desktop app is no longer connected.')
    else if (!res.granted && res.reason) setErr(res.reason)
    setBusyCap('')
    refresh()
  }

  return (
    <Section
      title="Desktop capabilities"
      hint="Native permissions the PersonalClaw desktop app holds on this machine. Each one is granted by macOS, not by PersonalClaw — revoking it in System Settings takes effect immediately, and nothing here can grant itself."
    >
      {!ds.connected || caps.length === 0 ? (
        <div className="rounded-lg bg-surface-container px-4 py-3">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-high">
              <MonitorOff size={17} className="text-on-surface-low" />
            </span>
            <div className="min-w-0">
              <div className="text-on-surface text-[0.8125rem]" style={fvs(600)}>Desktop app not connected</div>
              <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">
                You are viewing this in a browser tab. Native capabilities — microphone, notifications, the menu-bar item — exist only while the PersonalClaw desktop app is running, so there is nothing to show or grant here.
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {caps.map(([cap, state]) => {
            const p = GRANT_PRESENTATION[state.granted] ?? GRANT_PRESENTATION.unavailable
            const label = capabilityLabel(cap)
            return (
              <div key={cap} className="flex items-start justify-between gap-3 rounded-lg bg-surface-container px-4 py-3">
                <div className="min-w-0">
                  <div className="text-on-surface text-[0.8125rem]" style={fvs(600)}>{label}</div>
                  <div className={`mt-0.5 text-[0.8125rem] ${p.tone}`}>{p.label}</div>
                  {state.reason && (
                    <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">{state.reason}</div>
                  )}
                </div>
                {state.requestable && (
                  <Button variant="secondary" size="sm" loading={busyCap === cap}
                    onClick={() => request(cap)}>
                    {/* The capability is IN the name, so six buttons on one panel are
                        not six identically-named controls. */}
                    Allow {label.toLowerCase()}
                  </Button>
                )}
              </div>
            )
          })}
          {ds.shell && (
            <div className="text-on-surface-low text-[0.75rem]">
              Reported by the desktop app {ds.shell.version} on {ds.shell.platform}.
            </div>
          )}
          {err && <FieldError>{err}</FieldError>}
        </div>
      )}
    </Section>
  )
}

/** Operator overrides for the outbound egress guard. The guard blocks non-public
 *  destinations by default on every fetch/scrape/webhook; a self-hoster relaxes that for
 *  THEIR network here (a homelab LAN service) without weakening the default. A deny wins
 *  over an allow. */
function EgressPolicyEditor() {
  const { data: eg, refresh } = useQuery(
    'settings:egress', () => api.securityEgress().catch(() => null), { persist: true },
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!eg) return null

  const save = async (next: EgressPolicyConfig) => {
    setBusy(true); setErr('')
    try { await api.setSecurityEgress(next); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save') }
    finally { setBusy(false) }
  }

  return (
    <Section title="Network egress" hint="The agent's outbound fetches, scrapes, and webhooks are blocked from reaching non-public addresses (loopback, LAN, cloud metadata) by default — SSRF protection. Relax it for your own network below; a deny always wins over an allow.">
      <div className="flex flex-col gap-4">
        <HostList label="Allowed hosts" hint="Reachable even if they resolve to a private/LAN address (e.g. a homelab service). Bare domain covers subdomains."
          hosts={eg.allow_hosts} disabled={busy}
          onChange={(hosts) => save({ ...eg, allow_hosts: hosts })} />
        <HostList label="Denied hosts" hint="Never reachable, even if public. Overrides an allow."
          hosts={eg.deny_hosts} disabled={busy}
          onChange={(hosts) => save({ ...eg, deny_hosts: hosts })} />
        <label className="flex items-start gap-2.5 rounded-lg bg-surface-container px-3 py-2.5 cursor-pointer">
          <input type="checkbox" checked={eg.allow_private} disabled={busy}
            onChange={(e) => save({ ...eg, allow_private: e.target.checked })}
            className="mt-0.5 size-4 shrink-0 accent-primary" />
          <span className="min-w-0">
            <span className="text-on-surface text-[0.8125rem]">Allow all private networks</span>
            <span className="block text-on-surface-low text-[0.8125rem]">Permit egress to any private/LAN address, not just the allow-list. Only on a fully trusted network — this removes SSRF protection for the whole LAN.</span>
          </span>
        </label>
        {err && <FieldError>{err}</FieldError>}
      </div>
    </Section>
  )
}

/** Why an entry was refused. Returns `null` when the host is addable.
 *
 *  🔴 THE THREE REFUSALS USED TO BE SILENT, and on this panel that is a security defect: a user who
 *  pastes `https://nas.local` believes they have allowed their homelab, and they have not. Driven with
 *  the guard route intercepted — nothing was added, no error, no live region, three times:
 *
 *    input                  before                                   after
 *    https://nas.local      nothing added, draft KEPT, no message    "Enter a bare hostname — no
 *    nas.local:8080         "                                         scheme, port, or spaces…"
 *    two words              "                                        "
 *    nas.local (duplicate)  nothing added, draft CLEARED, silent     "nas.local is already listed"
 *
 *  The duplicate case is the worse one: clearing the box is exactly what a SUCCESSFUL add does, so the
 *  interface actively said "done". Separated here so both branches are assertable without a DOM. */
export function hostRefusal(raw: string, hosts: string[]): string | null {
  const h = raw.trim().toLowerCase()
  if (!h) return null                                    // the Add button is already gated on this
  if (hosts.includes(h)) return `${h} is already listed.`
  // Bare hostname only, mirroring the server guard — a scheme, a port or a space means the user pasted
  // a URL, which is the single most likely mistake here.
  if (h.includes('/') || h.includes(':') || h.includes(' ')) {
    return 'Enter a bare hostname — no scheme, port, or spaces (e.g. nas.local).'
  }
  return null
}

/** A small add/remove editor for a bare-hostname list. */
function HostList({ label, hint, hosts, disabled, onChange }: {
  label: string; hint: string; hosts: string[]; disabled: boolean; onChange: (hosts: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const [refused, setRefused] = useState('')
  const add = () => {
    const h = draft.trim().toLowerCase()
    if (!h) return
    const why = hostRefusal(draft, hosts)
    // The draft is KEPT on a refusal — emptying it is what a successful add looks like.
    if (why) { setRefused(why); return }
    setRefused(''); onChange([...hosts, h]); setDraft('')
  }
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-on-surface text-[0.8125rem]"><Globe size={13} className="text-on-surface-low" /> {label}</div>
      <div className="mb-2 text-on-surface-low text-[0.8125rem]">{hint}</div>
      <div className="flex flex-col gap-1.5">
        {hosts.map((h) => (
          <div key={h} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
            <code className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{h}</code>
            <button type="button" disabled={disabled} onClick={() => onChange(hosts.filter((x) => x !== h))}
              className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface" aria-label={`Remove ${h}`}>
              <X size={15} />
            </button>
          </div>
        ))}
        <div className="flex items-center gap-2">
          {/* Named from `label`, not a constant: this component renders TWICE ("Allowed hosts" and
              "Denied hosts") and a placeholder is not an accessible name — so both inputs announced
              nothing, and a shared constant would have announced them IDENTICALLY. Confusing the
              allow box for the deny box is a security-relevant mistake. */}
          <input value={draft} disabled={disabled}
            aria-label={`Add a host to ${label.toLowerCase()}`}
            aria-invalid={refused ? true : undefined}
            onChange={(e) => { setDraft(e.target.value); if (refused) setRefused('') }}
            onKeyDown={(e) => { if (e.key === 'Enter') add() }}
            placeholder="e.g. nas.local"
            className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface text-[0.8125rem] outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary" />
          <button type="button" onClick={add}
            {...unavailableWhen(!draft.trim(), 'Enter a host first', { busy: disabled })}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary text-[0.8125rem] disabled:opacity-50 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed">
            <Plus size={15} /> Add
          </button>
        </div>
        {/* `FieldError` rather than a hand-rolled danger line: it carries `role="alert"`, which is the
            difference between "the refusal is on screen" and "the user was told". */}
        {refused && <FieldError>{refused}</FieldError>}
      </div>
    </div>
  )
}

/** Which baseline is in force, and whether the packaged file still matches the sha256
 *  captured when the process started.
 *
 *  🔑 THE WORDING IS THE FEATURE. This says "matches what shipped", never "tamper-proof"
 *  or "secure", because the check is anti-drift and anti-LLM-tamper, NOT anti-owner:
 *  anyone who can edit the installed package before startup owns the baseline. Claiming
 *  more here would be the panel's own lie. Full statement in docs/security/threat-model.md.
 *
 *  The ROLE flips with the state — a verified baseline is a quiet `status`, a diverged one
 *  is an `alert`, because the reader did not ask for that news and it changes what the
 *  list below means. Both carry an explicit `aria-label`: `status`/`alert` do not take
 *  their name from content, so without one the a11y tree would announce nothing. */
function BaselineState({ baseline: b }: { baseline: DenylistBaseline }) {
  const chip = 'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[0.75rem]'
  const digest = <code className="tabular-nums" title={b.sha256}>{b.sha256.slice(0, 12)}…</code>
  if (b.verified) {
    return (
      <span role="status" className={`${chip} bg-surface-container text-on-surface-low`}
        aria-label={`Baseline v${b.version} matches what shipped: ${b.count} patterns verified against the release sha256`}>
        <ShieldCheck size={13} className="text-primary" aria-hidden />
        <span>Baseline v{b.version} matches what shipped — {b.count} patterns, sha256 {digest}</span>
      </span>
    )
  }
  return (
    <span role="alert" className={`${chip} border border-danger/30 bg-danger/5 text-danger`}
      aria-label={`Baseline v${b.version} does not match what shipped: ${b.detail || 'the packaged file diverged'}. The ${b.count} verified patterns are still enforced.`}>
      <ShieldAlert size={13} aria-hidden />
      <span>Baseline v{b.version} does NOT match what shipped — {b.detail || 'the packaged file diverged'}; the {b.count} verified patterns are still enforced (release sha256 {digest})</span>
    </span>
  )
}

/** The bash denied-command denylist: the packaged baseline (read-only — there is no
 *  control here that can edit, reorder or remove one, by design) + an editable user list.
 *  User patterns are validated as regexes server-side and appended to the always-on
 *  baseline, so the effective set can only ever grow. */
function DeniedCommandsEditor({ builtin, user, baseline, userAdditions, onChange }: {
  builtin: string[]; user: string[]; baseline: DenylistBaseline; userAdditions: number; onChange: () => void
}) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const save = async (next: string[]) => {
    setBusy(true)
    setErr('')
    try {
      await api.setUserDeniedCommands(next)
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    const p = draft.trim()
    if (!p || user.includes(p)) { setDraft(''); return }
    try { new RegExp(p) } catch { setErr('Not a valid regular expression'); return }
    await save([...user, p])
    setDraft('')
  }

  return (
    <Section title="Shell denylist" hint="Regexes matched against every command the agent runs. The packaged baseline is always enforced and read-only; your patterns are added to it, never subtracted from it.">
      <div className="flex flex-col gap-4">
        <div>
          <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-on-surface-low text-[0.8125rem]">
            <span className="inline-flex items-center gap-1.5"><Lock size={13} aria-hidden /> Baseline ({builtin.length}) — always enforced, not editable here</span>
            <BaselineState baseline={baseline} />
          </div>
          <p className="mb-2 text-on-surface-low text-[0.8125rem]">
            The baseline ships with PersonalClaw and is re-checked against the sha256
            recorded at release on every read, so nothing running inside the agent — the
            model included — can quietly shorten it. That catches drift and tampering from
            the inside; it is not a lock. Anyone who can edit the installed package before
            PersonalClaw starts owns the baseline.
          </p>
          {/* Every child is a read-only <code>, so this region has NO focusable descendant:
              a keyboard user could not scroll it at all (WCAG 2.1.1; axe
              scrollable-region-focusable, serious). Same resolution the kanban columns
              took — a tab stop makes the browser's own arrow/PageUp/PageDown scrolling
              work, and role+label keep it announced as a named container rather than an
              unnamed widget. Named with its count so the announcement says how much is
              in there. */}
          <div className="max-h-44 overflow-y-auto rounded-lg bg-surface-container p-2"
            tabIndex={0} role="group" aria-label={`Baseline shell denylist patterns (${builtin.length}), read-only`}>
            {builtin.map((p) => (
              <code key={p} className="block px-2 py-1 text-on-surface-low text-[0.75rem] tabular-nums">{p}</code>
            ))}
          </div>
        </div>
        <div>
          {/* 🪤 NOT `user.length`. The server dedupes a user pattern that already equals a
              baseline entry, so a config list of 3 whose entries duplicate built-ins adds
              NOTHING to what is enforced. The count comes from the effective set
              (`user_additions`), and the shadowed remainder is named rather than hidden —
              otherwise the panel would claim additions that change no behaviour. */}
          <div className="mb-0.5 text-on-surface text-[0.8125rem]">Your patterns</div>
          <div className="mb-2 text-on-surface-low text-[0.8125rem]">
            {userAdditions} user addition{userAdditions === 1 ? '' : 's'} on top of the baseline
            {user.length > userAdditions
              && ` · ${user.length - userAdditions} of your ${user.length} entries already match a baseline pattern and add nothing`}
          </div>
          <div className="flex flex-col gap-1.5">
            {user.map((p) => (
              <div key={p} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                <code className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{p}</code>
                <button type="button" disabled={busy} onClick={() => save(user.filter((x) => x !== p))}
                  className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface" aria-label={`Remove ${p}`}>
                  <X size={15} />
                </button>
              </div>
            ))}
            <div className="flex items-center gap-2">
              {/* "Shell denylist" (the Section title) is the context a screen reader needs here — a
                  bare "pattern" box gives no hint that typing in it BLOCKS a command. */}
              <input
                value={draft}
                aria-label="Add a shell denylist pattern (regex)"
                onChange={(e) => { setDraft(e.target.value); setErr('') }}
                onKeyDown={(e) => { if (e.key === 'Enter') add() }}
                placeholder="e.g. my-secret-tool .*"
                className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface text-[0.8125rem] outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary"
              />
              <button type="button" onClick={add}
                {...unavailableWhen(!draft.trim(), 'Enter a pattern first', { busy })}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary text-[0.8125rem] disabled:opacity-50 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed">
                <Plus size={15} /> Add
              </button>
            </div>
            {err && <FieldError>{err}</FieldError>}
          </div>
        </div>
      </div>
    </Section>
  )
}
