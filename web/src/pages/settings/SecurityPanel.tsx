import { useEffect, useId, useRef, useState } from 'react'
import { FieldError, FieldHintProvider, Select } from '../../ui/forms'
import { notify } from '../../app/appSdk'
import { unavailableWhen, BUSY_REASON } from '../../ui/unavailable'
import {
  ShieldBan, ScanLine, FileCode2, EyeOff, Plus, X, Lock, Globe, MonitorOff, ShieldCheck, ShieldAlert,
  KeyRound, Undo2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import {
  api, type DesktopCapabilityWire, type EgressPolicyConfig, type DenylistBaseline,
  type OutsideHomePlace,
} from '../../lib/api'
import { confirm } from '../../ui/dialog'
import { ConsentDeclined } from '../../lib/securityConsent'
import { reportSignedOut } from '../../lib/signedOut'
import {
  desktopBridge, getLoginItem, requestDesktopCapability, setLoginItem,
} from '../../lib/desktopBridge'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { invalidateKeys, useQuery } from '../../lib/data'
import { rebaseList, type Rebase } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { PanelHeader, Section, SavedToast, Row, RowGroup, ToggleRow, NumberRow, StrListField, Field } from './settingsUI'
import { CardGridSkeleton, LoadError } from '../../ui/ListScaffold'
import { TextLink } from '../../ui/TextLink'
import { fvs } from '../../design/fontWeight'
import { absTime } from '../schedule/scheduleMeta'

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
                <div data-type="body-s" className="text-on-surface">{c.label}</div>
                <div data-type="body-s" className="mt-0.5 text-on-surface-low">{c.hint}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>
      <SignedInSummary />
      <SignInLifetime />
      <SignInLockout />
      <SigningKey />
      {!denied && deniedErr ? (
        <Section title="Shell denylist">
          <LoadError what="shell denylist patterns" error={deniedErr} onRetry={refreshDenied} />
        </Section>
      ) : denied ? (
        <DeniedCommandsEditor builtin={denied.builtin} user={denied.user}
          baseline={denied.baseline} userAdditions={denied.user_additions}
          onChange={onDeniedChange} />
      ) : null}
      <CredentialStoreEditor />
      <ChildProcessCeilings note={s.child_ceilings?.note ?? ''} onScopesSaved={refreshStats} />
      <EgressPolicyEditor />
      <OutsideHomeEditor />
      <DesktopCapabilitiesPanel />
    </div>
  )
}

/** Who is signed in — a count, and the way to the ONE list (Settings → Devices).
 *
 *  "What can reach my gateway, and can I cut it off" is a security question, so it is asked here;
 *  but the list itself lives in one place, and this reads the same cached query that page does
 *  rather than growing a second list that could disagree with it. */
function SignedInSummary() {
  const { data, error, refresh } = useQuery('settings:devices', () => api.devices())
  const devices = data?.filter((d) => d.pool !== 'token').length ?? 0
  const tokens = data?.filter((d) => d.pool === 'token').length ?? 0
  return (
    <Section title="Signed-in devices"
      hint="Every browser, paired device and token that can reach this gateway is listed under Devices, where you can sign any of them out — or all but this one.">
      {!data && error ? (
        <LoadError what="signed-in devices" error={error} onRetry={refresh} />
      ) : (
        <RowGroup>
          <Row label={data
            ? `${devices} ${devices === 1 ? 'device is' : 'devices are'} signed in, and ${tokens} ${tokens === 1 ? 'token is' : 'tokens are'} live.`
            : 'Reading who is signed in…'}>
            <TextLink href="#/settings/devices" ink="emphasis" size="sm">Review signed-in devices</TextLink>
          </Row>
        </RowGroup>
      )}
    </Section>
  )
}

/** The longest a sign-in may last, in seconds: 90 days, the gateway's limit
 *  (`auth/lifetimes.py::MAX_LIFETIME_SECS`). */
export const SIGN_IN_LIMIT_SECS = 90 * 86400
const UNIT_SECS: Record<string, number> = { m: 60, h: 3600, d: 86400 }

/** `30m` / `20h` / `7d` in seconds, or `null` — the gateway's one lifetime grammar
 *  (`auth/lifetimes.py::lifetime_seconds`): a whole number and a unit, nothing around it. */
export function lifetimeSecs(text: string): number | null {
  const m = /^(\d+)([mhd])$/.exec(text)
  if (!m) return null
  const secs = Number(m[1]) * UNIT_SECS[m[2]]
  return secs > 0 ? secs : null
}

/** `12 hours` / `45 days` — *secs* in the largest unit it is a whole number of. */
export function lifetimeWords(secs: number): string {
  for (const [unit, size] of [['day', 86400], ['hour', 3600], ['minute', 60]] as const) {
    if (secs >= size && secs % size === 0) {
      const n = secs / size
      return `${n} ${unit}${n === 1 ? '' : 's'}`
    }
  }
  return `${secs} seconds`
}

/** The lifetimes offered. The longest is the limit itself, so nothing longer can be chosen here. */
export const SIGN_IN_LIFETIMES: { value: string; label: string }[] = [
  { value: '12h', label: '12 hours' },
  { value: '1d', label: '1 day' },
  { value: '7d', label: '7 days' },
  { value: '14d', label: '14 days' },
  { value: '30d', label: '30 days — the default' },
  { value: '60d', label: '60 days' },
  { value: '90d', label: '90 days — the limit' },
]

/** `auth.session_ttl` — how long a browser sign-in lasts (ledger 317c).
 *
 *  It had no control anywhere: `personalclaw config set` and a hand-edited `config.json` were the
 *  only ways to shorten how long a stolen cookie keeps working. The write goes through the one
 *  config PATCH, so the gateway validates it (the 90-day limit is its rule, `config/editable.py`)
 *  and asks for the owner's consent when it lengthens the sign-in (a `SecurityControl`), with no
 *  second copy of either decision here.
 *
 *  🔑 THE CAP IS IN THE CONTROL, not a clamp behind it: the longest option is 90 days, so a longer
 *  lifetime cannot be picked. A value set elsewhere is shown as its own option rather than
 *  mis-shown as a preset; one over the limit is shown for what it is, with the fact that every
 *  sign-in lasts 90 days anyway — the same sentence `personalclaw doctor` prints. */
function SignInLifetime() {
  const [saved, setSaved] = useState(false)
  const [stored, setStored] = useState<string | null>(null)
  const { data, error, refresh } = useQuery('settings:auth', () =>
    api.personalclawConfig().then((c) => (c.auth ?? {}) as Record<string, unknown>),
  )
  useEffect(() => {
    if (!data) return
    setStored(typeof data.session_ttl === 'string' && data.session_ttl ? data.session_ttl : '30d')
  }, [data])
  // This browser's own sign-in, from the one list (Settings → Devices reads the same query). A link
  // `personalclaw token` printed carries its own lifetime, which the choice here does not reach —
  // and it is the only door a container install has, so the browser says when it ends.
  const { data: devices, error: devicesError } = useQuery('settings:devices', () => api.devices())
  const here = devices?.find((d) => d.current)
  const byLink = here && here.issuer === 'token' && here.expires_at > 0 ? here : undefined
  const label = 'Sign-ins last'
  const hint = 'How long a browser stays signed in after a password, a device code, a pairing, or the link the gateway opens at start, before it must sign in again. At most 90 days: the longer a sign-in keeps working, the longer anyone who copies it can use your dashboard. A change applies to the next sign-in; a device already signed in keeps the lifetime it signed in with. A link from personalclaw token keeps its own lifetime instead: 20 hours, unless it was made with --ttl (at most 90 days).'

  if (!data && error) {
    return (
      <Section title="Sign-in lifetime">
        <LoadError what="sign-in lifetime" error={error} onRetry={refresh} />
      </Section>
    )
  }
  const current = stored ?? '30d'
  const secs = lifetimeSecs(current)
  const over = secs !== null && secs > SIGN_IN_LIMIT_SECS
  const options = SIGN_IN_LIFETIMES.some((o) => o.value === current)
    ? SIGN_IN_LIFETIMES
    : [
      ...SIGN_IN_LIFETIMES,
      secs === null
        ? { value: current, label: `${current} — not a length of time`, disabled: true }
        : over
          ? { value: current, label: `${current} — longer than the limit`, disabled: true }
          : { value: current, label: `${lifetimeWords(secs)} — set outside Settings` },
    ]
  const choose = (value: string) => {
    const prev = current
    setStored(value)
    api.patchConfig('auth.session_ttl', value).then(() => {
      setSaved(true); window.setTimeout(() => setSaved(false), 1500)
      invalidateKeys('settings:auth')
    }).catch((e) => {
      setStored(prev)
      if (e instanceof ConsentDeclined) { notify(e.message); return }
      notify(`Couldn't change how long a sign-in lasts: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Sign-in lifetime">
      <RowGroup>
        <Field label={label} hint={hint}>
          <div className="flex items-center gap-s">
            <Select value={current} options={options} onChange={choose} disabled={stored === null}
              disabledReason={stored === null ? 'Still reading how long a sign-in lasts' : undefined} />
            <SavedToast show={saved} />
          </div>
          {over ? (
            <p role="status" data-type="body-s" className="mt-s flex items-start gap-1.5 text-on-surface">
              <ShieldAlert size={15} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />
              <span>{`auth.session_ttl is ${current}, longer than the 90-day limit for a sign-in, so every sign-in lasts 90 days. Choose 90 days or less.`}</span>
            </p>
          ) : secs === null ? (
            <p role="status" data-type="body-s" className="mt-s flex items-start gap-1.5 text-on-surface">
              <ShieldAlert size={15} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />
              <span>{`auth.session_ttl is “${current}”, which is not a length of time, so every sign-in lasts the 30-day default. Choose a lifetime.`}</span>
            </p>
          ) : null}
          {byLink && (
            <p data-type="body-s" className="mt-s text-on-surface-low">
              {`This browser signed in with a link from personalclaw token, so it stays signed in until ${absTime(byLink.expires_at)}: the link's own lifetime, not the one chosen here. To stay signed in longer, open a link from personalclaw token --ttl 30d.`}
            </p>
          )}
          {!devices && devicesError ? (
            <FieldError>{"Couldn't read this browser's sign-in, so when it ends isn't shown here."}</FieldError>
          ) : null}
        </Field>
      </RowGroup>
    </Section>
  )
}

/** The attempt counts offered, and the lockout lengths. The gateway's bounds are 1–100 attempts
 *  (`config/editable.py`); a value set outside Settings is shown as itself, as the lifetime above is. */
export const LOCKOUT_ATTEMPTS: { value: string; label: string }[] = [
  { value: '3', label: '3 wrong attempts' },
  { value: '5', label: '5 wrong attempts — the default' },
  { value: '10', label: '10 wrong attempts' },
  { value: '20', label: '20 wrong attempts' },
]
export const LOCKOUT_WINDOWS: { value: string; label: string }[] = [
  { value: '5m', label: '5 minutes' },
  { value: '15m', label: '15 minutes — the default' },
  { value: '30m', label: '30 minutes' },
  { value: '1h', label: '1 hour' },
  { value: '4h', label: '4 hours' },
  { value: '1d', label: '1 day' },
]

/** `auth.lockout_threshold` + `auth.lockout_window` — how many wrong tries a device gets.
 *
 *  Both were on the config PATCH allowlist, bounded, help-texted and read by every sign-in door —
 *  the password page, a device code, a pairing code — with no control anywhere: the Account panel
 *  STATED the lockout, and a hand-edited `config.json` was the only way to change it. The writes go
 *  through the one config PATCH, which validates them and asks for consent when a change allows
 *  more guesses (more attempts, or a shorter lockout), with no second copy of either rule here. */
function SignInLockout() {
  const [saved, setSaved] = useState<'' | 'threshold' | 'window'>('')
  const [stored, setStored] = useState<{ threshold: string; window: string } | null>(null)
  const { data, error, refresh } = useQuery('settings:auth', () =>
    api.personalclawConfig().then((c) => (c.auth ?? {}) as Record<string, unknown>),
  )
  useEffect(() => {
    if (!data) return
    const threshold = Number(data.lockout_threshold)
    setStored({
      threshold: Number.isInteger(threshold) && threshold > 0 ? String(threshold) : '5',
      window: typeof data.lockout_window === 'string' && data.lockout_window ? data.lockout_window : '15m',
    })
  }, [data])

  if (!data && error) {
    return (
      <Section title="Sign-in lockout">
        <LoadError what="sign-in lockout" error={error} onRetry={refresh} />
      </Section>
    )
  }
  const current = stored ?? { threshold: '5', window: '15m' }
  const windowSecs = lifetimeSecs(current.window)
  const attemptOptions = LOCKOUT_ATTEMPTS.some((o) => o.value === current.threshold)
    ? LOCKOUT_ATTEMPTS
    : [...LOCKOUT_ATTEMPTS, { value: current.threshold, label: `${current.threshold} wrong attempts — set outside Settings` }]
  const windowOptions = LOCKOUT_WINDOWS.some((o) => o.value === current.window)
    ? LOCKOUT_WINDOWS
    : [
      ...LOCKOUT_WINDOWS,
      windowSecs === null
        ? { value: current.window, label: `${current.window} — not a length of time`, disabled: true }
        : { value: current.window, label: `${lifetimeWords(windowSecs)} — set outside Settings` },
    ]
  const unread = stored === null ? 'Still reading the sign-in lockout' : undefined

  const write = (field: 'threshold' | 'window', value: string) => {
    const prev = current
    setStored({ ...current, [field]: value })
    const [path, wire] = field === 'threshold'
      ? ['auth.lockout_threshold', Number(value)] as const
      : ['auth.lockout_window', value] as const
    api.patchConfig(path, wire).then(() => {
      setSaved(field); window.setTimeout(() => setSaved(''), 1500)
      invalidateKeys('settings:auth')
    }).catch((e) => {
      setStored(prev)
      if (e instanceof ConsentDeclined) { notify(e.message); return }
      notify(`Couldn't change the sign-in lockout: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Sign-in lockout"
      hint="How many wrong passwords, device codes or pairing codes a device may try before PersonalClaw stops accepting them from it for a while. Fewer attempts and a longer lockout make guessing slower; a lockout you trip yourself ends on its own.">
      <RowGroup>
        <Field label="Stop accepting sign-ins after" hint="Wrong attempts from one address, counted over the lockout below.">
          <div className="flex items-center gap-s">
            <Select value={current.threshold} options={attemptOptions} onChange={(v) => write('threshold', v)}
              disabled={stored === null} disabledReason={unread} />
            <SavedToast show={saved === 'threshold'} />
          </div>
        </Field>
        <Field label="Lockout lasts" hint="How long sign-ins from that address are refused — and the window its wrong attempts are counted over.">
          <div className="flex items-center gap-s">
            <Select value={current.window} options={windowOptions} onChange={(v) => write('window', v)}
              disabled={stored === null} disabledReason={unread} />
            <SavedToast show={saved === 'window'} />
          </div>
          {windowSecs === null ? (
            <p role="status" data-type="body-s" className="mt-s flex items-start gap-1.5 text-on-surface">
              <ShieldAlert size={15} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />
              <span>{`auth.lockout_window is “${current.window}”, which is not a length of time, so a lockout lasts the 15-minute default. Choose a length.`}</span>
            </p>
          ) : null}
        </Field>
      </RowGroup>
    </Section>
  )
}

/** Replace the key every sign-in is signed with.
 *
 *  `session_store.rotate_key` existed and nothing called it, so the one answer to "the key, or a
 *  sign-in, may have been copied" was not in the product: signing devices out one by one leaves the
 *  key that could sign new ones in. The gateway signs every browser, paired device and token out —
 *  this one included — and tells each why; the sentence it answers for THIS browser is shown at
 *  once, through the same signed-out screen its next refused request would bring up. */
function SigningKey() {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const replace = async () => {
    if (!(await confirm({
      title: 'Replace the sign-in key?',
      body: 'Every browser, paired device and token signed in to this gateway is signed out at once — this browser too — and each is told why the next time it connects. Each one has to sign in again. Integration tokens are separate and keep working.',
      confirmLabel: 'Replace and sign everyone out',
      danger: true,
    }))) return
    setBusy(true); setErr('')
    try {
      const { signed_out: signedOut, notice } = await api.rotateSigningKey()
      if (notice) { reportSignedOut(notice); return }
      // No session of its own ended (the local-network bypass admits this browser without one).
      notify(`The sign-in key was replaced, and ${signedOut === 1 ? 'one sign-in' : `${signedOut} sign-ins`} ended.`, 'success')
    } catch (e) {
      setErr(`Couldn't replace the sign-in key: ${String((e as Error)?.message || e)}`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title="Sign-in key"
      hint="Every sign-in to this gateway is signed with one key. If you think the key, or a sign-in, was copied, replace it: every browser, paired device and token is signed out at once, and nothing signed with the old key works again.">
      <RowGroup>
        <Row label="Replace the key and sign everyone out" hint="This browser is signed out too. Sign back in the way you signed in: your password, a pairing, or a new personalclaw token link.">
          <Button variant="danger" size="sm" onClick={replace} loading={busy}>
            <KeyRound size={15} /> Replace the key
          </Button>
        </Row>
      </RowGroup>
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}

/** The `sandbox.*` config section — resource ceilings for agent-influenced child processes
 *  (PLATFORM-HARDENING-FLOORS §1), delivered post-exec by the ceiling shim.
 *
 *  All five were on the PATCH allowlist with `_meta` help and live readers (`sandbox.py`,
 *  `config/safety.py`, `sandbox_providers/docker.py`) and NO control anywhere in `web/`, so the one
 *  set of limits standing between a runaway tool and the host could only be changed by hand-editing
 *  `config.json`. They belong on THIS panel: it is the enforcement-posture page, and the stat cards
 *  at the top already count the other enforcement mechanisms.
 *
 *  🔑 ONLY THE BOUNDS ARE EDITABLE, and the copy says so. Which files may never be captured and
 *  which env names are refused outright are code-level floors with no config field — no PATCH here
 *  can widen them, and `env_passthrough` in particular still loses to the credential floor at spawn
 *  time. A control that implied otherwise would be the worse defect. */
function ChildProcessCeilings({ note, onScopesSaved }: {
  /** What Max memory and Max processes do on the gateway's host, or `''` when they contain a
   *  child. From the server, never `navigator`: the browser may be on another machine. */
  note: string
  /** The note depends on Cgroup scopes, so a saved toggle re-reads it. */
  onScopesSaved: () => void
}) {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const { data, error: loadErr, refresh } = useQuery('settings:sandbox', () =>
    api.personalclawConfig().then((c) => (c.sandbox ?? {}) as Record<string, unknown>),
    { persist: true },
  )
  useEffect(() => { if (data) setCfg(data) }, [data])

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = (cfg ?? {})[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`sandbox.${key}`, value).then(() => {
      onSaved?.()
      if (key === 'cgroup_scopes') onScopesSaved()
    }).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  // The names added and removed, never this panel's copy of the list (`StrListField`).
  const editList = (key: string, next: string[], onSaved: () => void, label?: string) => {
    const prev = Array.isArray((cfg ?? {})[key]) ? ((cfg ?? {})[key] as string[]) : []
    api.saveListEdits(`sandbox.${key}`, prev, next).then((stored) => {
      setCfg((c) => ({ ...c, [key]: stored }))
      onSaved()
    }).catch((e) => notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error'))
  }

  return (
    <Section title="Child process ceilings"
      hint="Limits applied to processes the agent can influence — bash tools, app backends, MCP servers, hook and cron scripts. They take effect on the next spawn; a running child keeps the limits it was started with. 0 disables an individual limit.">
      {/* Before the fields, not under them: on a Mac two of these do nothing a user would want,
          and the one place that said so was a warning in the gateway log. */}
      {note ? (
        <p data-type="body-s" className="mb-3 flex items-start gap-1.5 text-on-surface">
          <ShieldAlert size={15} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />
          <span>{note}</span>
        </p>
      ) : null}
      {!data && loadErr
        ? <LoadError what="sandbox ceilings" error={loadErr} onRetry={refresh} />
        : !cfg
          ? <CardGridSkeleton cards={1} cols={1} what="sandbox ceilings" />
          : (
            <RowGroup>
              <NumberRow label="Max open files" cfg={cfg} field="nofile" min={0} max={1048576} step={1024} patch={patch}
                hint="Open-file ceiling for an agent child process. 0 disables the cap. Connected CLI agents are exempt by profile — they multiplex many pipes and would hit it immediately." />
              <NumberRow label="Max memory (MB)" cfg={cfg} field="max_rss_mb" min={0} max={1048576} step={256} patch={patch}
                hint="Address-space ceiling for an agent child. 0 disables it, which is the default: the limit is coarse and can break memory-mapped toolchains, so it is opt-in." />
              <NumberRow label="Max processes" cfg={cfg} field="max_pids" min={0} max={100000} step={64} patch={patch}
                hint="Process ceiling for an agent child. 0 disables it, and leaving it at 0 is the recommendation — this limit counts ALL of your existing processes, not just the child's, so an absolute cap can make a busy machine fail with “cannot fork”. Real per-child containment is the cgroup tier below." />
              <ToggleRow label="Cgroup scopes (Linux)" cfg={cfg} field="cgroup_scopes" patch={patch}
                hint="Wrap each agent-influenced spawn in a transient systemd user scope carrying the ceilings above, so they bound the child's WHOLE process tree instead of one process. This is the fork-bomb containment the process limit cannot give. Linux only, and a no-op where a systemd user manager is unavailable (macOS, most containers)." />
              <StrListField label="Child environment passthrough" cfg={cfg} field="env_passthrough" editList={editList}
                placeholder="Add name…"
                hint="Extra environment VARIABLE NAMES a child may inherit, on top of the minimal base (PATH, locale, home, proxy/CA settings). A child is anything PersonalClaw starts to run code it didn't write: an agent's command, a hook, a script, and an app's installs, hooks and servers. Everything else is withheld, and a proxy address reaches a child without its password. A name added here is passed as it is, password included. Names matching the credential floor (AWS secrets, SSH agent socket, GPG home, git askpass) are refused even when declared here." />
            </RowGroup>
          )}
    </Section>
  )
}

/** Why there is no OS keychain to keep credentials in, keyed by the read's `keychain_missing`: the
 *  sentence the unavailable switch carries, in the owner's terms rather than a backend's. */
const KEYCHAIN_MISSING: Record<string, string> = {
  not_installed: 'This install of PersonalClaw has no keychain support, so there is no OS keychain to keep credentials in. Installing PersonalClaw with its keychain extra (personalclaw[keychain]) adds it.',
  no_service: 'No OS keychain answers on this machine: a container has none, and neither does a computer whose secret service is not running.',
}

/** Settings -> Security -> Credential storage.
 *
 *  Two controls with deliberately different weights. The TOGGLE is a plain config write: it
 *  changes where the NEXT credential is written and touches nothing already stored. The MOVE
 *  is a confirmed data operation on the user's secrets, so it goes through a danger dialog
 *  that names the snapshot by filename before anything is written.
 *
 *  🔑 THE READ IS BARE — no `.catch(() => null)`. Same reasoning as this panel's other two
 *  reads: "0 credentials in .env" is pixel-identical to a failed fetch, and on a surface
 *  about where secrets are kept that is the one lie it must not tell.
 *
 *  The panel renders the RESOLVED backend, and `blocked` (computed server-side) is what
 *  disables the move — so a machine that asked for a keychain it does not have shows the
 *  reason instead of a button that would refuse. */
function CredentialStoreEditor() {
  const { data: cs, error, refresh } = useQuery(
    'settings:credential-store', () => api.credentialStore(),
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  if (!cs) {
    return (
      <Section title="Credential storage">
        {error ? <LoadError what="credential storage state" error={error} onRetry={refresh} />
          : <CardGridSkeleton cards={1} cols={1} what="credential storage state" />}
      </Section>
    )
  }

  const run = async (label: string, fn: () => Promise<{ reason: string; moved: string[]; failed: string[] }>) => {
    setBusy(true); setErr(''); setNote('')
    try {
      const r = await fn()
      // A PARTIAL result is a 200 with `failed` — it did real work and the user has to see
      // both halves, so the reason is surfaced as an error while the counts still report.
      if (r.reason) setErr(r.reason)
      setNote(`${label}: ${r.moved.length} credential${r.moved.length === 1 ? '' : 's'}.`)
      refresh()
    } catch (e) { setErr(e instanceof Error ? e.message : `${label} failed`) }
    finally { setBusy(false) }
  }

  const move = async () => {
    const names = cs.pending_keys.join(', ')
    if (!(await confirm({
      title: `Move ${cs.pending} credential${cs.pending === 1 ? '' : 's'} into the OS keychain?`,
      // 🔴 THE SNAPSHOT STEP, STATED BEFORE THE ACTION. The change's requirement is a VISIBLE
      // snapshot confirm — so the body names the file that gets written, says the keys leave
      // .env, and says the move is reversible. Every clause here is true of the handler:
      // `_write_snapshot` writes that exact filename at 0600 before the first keychain write,
      // and `rollback_credentials_to_keychain` restores those bytes verbatim.
      body: `Your current .env is copied to ${cs.snapshot_name} first (mode 0600), then ${names} `
        + 'move into the keychain and are removed from .env. No key leaves .env until its value has '
        + 'been read back out of the keychain. Use Roll back to undo this and restore .env exactly.',
      confirmLabel: 'Snapshot and move',
      danger: true,
    }))) return
    await run('Moved', () => api.migrateCredentialsToKeychain())
  }

  const rollBack = async () => {
    if (!(await confirm({
      title: 'Restore .env from the pre-migration snapshot?',
      body: `.env is rewritten from ${cs.snapshot_name} byte for byte, the keychain copies of those `
        + 'keys are deleted, and the snapshot file is removed. Credentials you added to the keychain '
        + 'after migrating are left alone.',
      confirmLabel: 'Roll back',
      danger: true,
    }))) return
    await run('Restored', () => api.rollbackCredentialsToKeychain())
  }

  const inKeychain = cs.backend === 'keychain'
  // Where no keychain answers, asking for one changes nothing: the switch is unavailable and says
  // why. One asked for already (earlier, or on another machine) can still be turned off.
  const missing = KEYCHAIN_MISSING[cs.keychain_missing] ?? ''
  const cannotAsk = missing !== '' && cs.requested !== 'keychain'
  return (
    <Section title="Credential storage" hint="Where this instance keeps provider credentials. The default is the .env file in this instance's home folder, at mode 0600; the OS keychain (macOS Keychain, Linux Secret Service, Windows Credential Locker) is an opt-in upgrade. A machine with no usable secret service keeps using .env and says so — there is never a third location.">
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-3 rounded-lg bg-surface-container px-4 py-3">
          <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
            {inKeychain ? <KeyRound size={17} className="text-primary" /> : <Lock size={17} className="text-primary" />}
          </span>
          <div className="min-w-0">
            <div data-type="label-s" className="text-on-surface" style={fvs(600)}>
              {inKeychain ? 'OS keychain' : '.env at mode 0600'}
            </div>
            <div data-type="body-s" className="mt-0.5 text-on-surface-low">
              {cs.keychain_keys} in the keychain · {cs.pending} still in .env
            </div>
            {missing && cs.requested === 'keychain' && (
              <div data-type="body-s" className="mt-1 text-error">
                {`${missing} Credentials stay in .env at mode 0600. Turn “Store credentials in the OS keychain” off to stop asking for one.`}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-start gap-2.5 rounded-lg bg-surface-container px-3 py-2.5">
          <Toggle on={cs.requested === 'keychain'} disabled={busy || cannotAsk}
            disabledReason={cannotAsk ? `${missing} Credentials stay in .env at mode 0600.` : undefined}
            label="Store credentials in the OS keychain"
            onChange={async (on) => {
              setBusy(true); setErr(''); setNote('')
              try { await api.setCredentialKeychain(on); refresh() }
              catch (ex) { setErr(ex instanceof Error ? ex.message : 'Failed to save') }
              finally { setBusy(false) }
            }} />
          <span className="min-w-0">
            <span data-type="body-s" className="text-on-surface">Store credentials in the OS keychain</span>
            <span data-type="body-s" className="block text-on-surface-low">
              {cannotAsk
                ? `${missing} Credentials stay in .env at mode 0600.`
                : 'Changes where NEW credentials are written. Secrets already in .env stay readable and stay put until you move them below.'}
            </span>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Two of the three gate clauses are preconditions a user can fix, so the reason is
              named rather than left to a control that silently leaves the tab order. */}
          <Button onClick={move} disabled={busy || cs.blocked || cs.pending === 0}
            disabledReason={cs.blocked
              ? missing
                ? 'There is no OS keychain on this machine to move them into'
                : 'Turn on "Store credentials in the OS keychain" first'
              : cs.pending === 0 ? 'There are no credentials left in .env to move' : undefined}>
            <KeyRound size={15} /> Move {cs.pending > 0 ? cs.pending : ''} to keychain
          </Button>
          {cs.rollback_available && (
            <Button variant="secondary" onClick={rollBack} disabled={busy} disabledReason={BUSY_REASON}>
              <Undo2 size={15} /> Roll back
            </Button>
          )}
          {cs.pending === 0 && !cs.blocked && (
            <span data-type="body-s" className="text-on-surface-low">
              Nothing left in .env{cs.verification.checked > 0 ? ` — ${cs.verification.checked} verified in the keychain` : ''}.
            </span>
          )}
        </div>
        {note && <div data-type="body-s" className="text-on-surface-low" role="status">{note}</div>}
        {err && <FieldError>{err}</FieldError>}
      </div>
    </Section>
  )
}

/** Human labels for the native capability vocabulary. An UNMAPPED name still
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

/** The one row in this section that is a PREFERENCE, not a permission.
 *
 *  🪤 WHAT THIS FIXES. `login_item` is a `kind: "shell"` capability, and `probe()` answers
 *  those with `granted` the moment the shell is running — correctly, because the question
 *  it asks is "can this shell register a login item?". Rendered through
 *  `GRANT_PRESENTATION` like its neighbours, that came out as a green **"Open at login —
 *  Granted"** on a machine where nothing was registered: a true sentence about the
 *  facility that every user reads as a false one about their own preference. And the row
 *  offered no control, because `requestable: false` is also correct — the capability
 *  bridge cannot request this. So the surface named the setting, mis-stated it, and could
 *  not change it.
 *
 *  This row reports the OS's actual REGISTRATION instead, and writes it with the same call
 *  the menu-bar item's own "Open at Login" checkbox uses — one mechanism, two surfaces, so
 *  flipping either is reflected by the other.
 *
 *  NO CONFIG KEY BEHIND IT, deliberately. The OS is the authority: the user can remove the
 *  registration in System Settings → General → Login Items while PersonalClaw is not
 *  running, so a mirror in `config.json` would be a second source for one fact and would
 *  go stale the first time that happened. Every read here goes to the OS. */
function LoginItemRow({ label }: { label: string }) {
  const { data: state, refresh } = useQuery('settings:login-item', () => getLoginItem())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState(false)

  // `undefined` while the read is in flight; `null` if the shell answered nothing. Either
  // way there is no registration to state, and inventing one is the defect above.
  if (!state) return null

  const flip = async (next: boolean) => {
    setBusy(true)
    setErr('')
    const res = await setLoginItem(next)
    if (!res) setErr('The desktop app is no longer connected.')
    else if (!res.ok) setErr(res.reason || 'macOS did not apply the change.')
    else { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
    setBusy(false)
    // Read the registration back rather than trust the write. The OS is the authority, so
    // a refused write has to leave this toggle showing what is actually registered — the
    // alternative is a switch that sits in the position the user chose while the machine
    // does the opposite.
    refresh()
  }

  return (
    <div className="flex items-start justify-between gap-3 rounded-lg bg-surface-container px-4 py-3">
      <div className="min-w-0">
        <div data-type="label-s" className="text-on-surface" style={fvs(600)}>{label}</div>
        <div data-type="body-s" className={`mt-0.5 ${state.enabled ? 'text-success' : 'text-on-surface-low'}`}>
          {state.enabled
            ? 'On — PersonalClaw starts when you log in'
            : 'Off — PersonalClaw starts only when you open it'}
        </div>
        {/* `describes` names the exact registration this touches. A login item is a
            persistent change to the user's machine, so the sentence belongs BEFORE the
            flip, not in a confirmation after it. */}
        <div data-type="body-s" className="mt-0.5 text-on-surface-low">{state.describes}</div>
        {err && <FieldError>{err}</FieldError>}
      </div>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {/* TWO disabled classes, deliberately kept apart. An unsupported platform is a
            PRECONDITION — nothing the user does here will ever enable it — so it carries the
            reason and stays keyboard-reachable rather than going dark unexplained. `busy` is
            IN-FLIGHT and stays native: re-clicking mid-write is the failure being prevented,
            and a reason there would soften it. They cannot co-occur — an unsupported switch
            cannot be clicked, so `busy` never rises on one. */}
        <Toggle on={state.enabled} onChange={flip} label={label}
          disabled={!state.supported || busy}
          disabledReason={state.supported ? undefined : state.describes} />
      </div>
    </div>
  )
}

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

/** Settings → Security → Desktop capabilities.
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
  // Read in render, not in state: the bridge is either injected before the first paint or
  // not at all (`contextBridge` runs at preload), so there is nothing to subscribe to.
  const loginItemBridge = !!desktopBridge()?.loginItem
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
              <div data-type="label-s" className="text-on-surface" style={fvs(600)}>Desktop app not connected</div>
              <div data-type="body-s" className="mt-0.5 text-on-surface-low">
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
            // The login item is a preference this panel can actually WRITE, so it renders
            // its own row against the login-item bridge. A shell too old to carry that
            // namespace falls through to the plain capability row below rather than
            // mounting a toggle that would call through `undefined`.
            if (cap === 'login_item' && loginItemBridge) return <LoginItemRow key={cap} label={label} />
            return (
              <div key={cap} className="flex items-start justify-between gap-3 rounded-lg bg-surface-container px-4 py-3">
                <div className="min-w-0">
                  <div data-type="label-s" className="text-on-surface" style={fvs(600)}>{label}</div>
                  <div data-type="body-s" className={`mt-0.5 ${p.tone}`}>{p.label}</div>
                  {state.reason && (
                    <div data-type="body-s" className="mt-0.5 text-on-surface-low">{state.reason}</div>
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
            <div data-type="caption" className="text-on-surface-low">
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
  const { data: stored, refresh } = useQuery(
    'settings:egress', () => api.securityEgress().catch(() => null), { persist: true },
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // `confirmed` only from the private-networks checkbox, which asks its own question below. A host
  // list change that widens the guard is asked about by the gateway instead (`api.patchConfig`).
  // Held for the save it belongs to, so a re-applied save carries the consent the owner gave.
  const confirmed = useRef(false)
  const reread = () => { invalidateKeys('settings:egress'); refresh() }
  // 🔴 THE THREE KEYS ARE ONE DOCUMENT, written over the revision it was read at. Every edit here
  // resent `{...eg, <one key>}` from this panel's copy, so a host allowed in another tab was dropped
  // by the next edit made here. A stale copy is now refused and the edit re-applied on top.
  const guard = useStaleWriteGuard<EgressPolicyConfig>({
    read: api.securityEgress,
    write: (next, base) => api.setSecurityEgress(next, base, confirmed.current),
    onSaved: reread,
    onDiscard: reread,
  })
  if (!stored) return null
  const eg = stored.value

  const save = async (op: Rebase<EgressPolicyConfig>, consented = false): Promise<boolean> => {
    confirmed.current = consented
    setBusy(true); setErr('')
    try { return await guard.apply(stored, op) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save'); return false }
    finally { setBusy(false) }
  }
  // A host list edit as an operation — these names in, those out — so it re-applies onto a list
  // another tab has changed.
  const editHosts = (key: 'allow_hosts' | 'deny_hosts', hosts: string[]) => {
    const edit = rebaseList(eg[key], hosts)
    return save((theirs) => ({ ...theirs, [key]: edit(theirs[key]) }))
  }

  return (
    <Section title="Network egress" hint="The agent's outbound fetches, scrapes, and webhooks are blocked from reaching non-public addresses (loopback, LAN, cloud metadata) by default — SSRF protection. Shell commands reach only the allowed hosts below without a person saying yes (the built-in agent's always, an agent CLI's when it asks first), and so do the programs apps start: a chat asks you about any other host, an unattended run (a loop, a schedule) is refused it, and an app's program is stopped unless its install review named the host. A deny always wins over an allow.">
      <div className="flex flex-col gap-4">
        <HostList label="Allowed hosts" hint="Fetches reach these even if they resolve to a private/LAN address (e.g. a homelab service), and a shell command or an app's program reaches them without asking. Bare domain covers subdomains."
          hosts={eg.allow_hosts} disabled={busy || guard.conflict !== null}
          onChange={(hosts) => editHosts('allow_hosts', hosts)} />
        <HostList label="Denied hosts" hint="Never reachable, even if public. Overrides an allow."
          hosts={eg.deny_hosts} disabled={busy || guard.conflict !== null}
          onChange={(hosts) => editHosts('deny_hosts', hosts)} />
        <label className="flex items-start gap-2.5 rounded-lg bg-surface-container px-3 py-2.5 cursor-pointer">
          <input type="checkbox" checked={eg.allow_private} disabled={busy || guard.conflict !== null}
            onChange={async (e) => {
              const next = e.target.checked
              if (next && !(await confirm({
                title: 'Allow egress to all private networks?',
                body: 'The agent’s outbound fetches, scrapes, and webhooks will be able to reach ANY private or LAN address, not just your allow-list above — removing SSRF protection for your whole network. Only do this on a fully trusted network.',
                confirmLabel: 'Allow private networks',
                danger: true,
              }))) return
              save((theirs) => ({ ...theirs, allow_private: next }), next)
            }}
            className="mt-0.5 size-4 shrink-0 accent-primary" />
          <span className="min-w-0">
            <span data-type="body-s" className="text-on-surface">Allow all private networks</span>
            <span data-type="body-s" className="block text-on-surface-low">Let fetches reach any private/LAN address, not just the allow-list. Only on a fully trusted network — this removes SSRF protection for the whole LAN. A shell command still reaches only the allowed hosts without asking.</span>
          </span>
        </label>
        <StaleWriteNotice guard={guard} what="Your network egress overrides" />
        {err && <FieldError>{err}</FieldError>}
      </div>
    </Section>
  )
}

/** The places outside the PersonalClaw home it may READ (`personalclaw/outside_home.py`).
 *
 *  PersonalClaw keeps what it reads and writes in its home. Each place here already lives
 *  elsewhere on this computer (the skills other AI tools share, the Hugging Face folder, another
 *  CLI's sign-in, another agent tool's setup) and is off until the owner turns it on. Turning one
 *  on asks first and names the folder; turning it off never asks and stops the reading at once.
 *  PersonalClaw never writes or deletes there, and the copy says so, because that is the promise
 *  the switch makes. An agent tool's setup is also read when she presses Look in it (the Tools
 *  page, Bring your setup over), for that press alone, so the hint names the press too.
 *
 *  The read is bare: a failed fetch is said, not rendered as "nothing is allowed". */
export function OutsideHomeEditor() {
  const { data, error: loadErr, refresh } = useQuery(
    'settings:outside-home', () => api.outsideHome(), { persist: true },
  )
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const title = "Outside PersonalClaw's home"
  if (!data && loadErr) {
    return (
      <Section title={title}>
        <LoadError what="places outside PersonalClaw's home" error={loadErr} onRetry={refresh} />
      </Section>
    )
  }
  if (!data) return null

  const set = async (place: OutsideHomePlace, on: boolean) => {
    if (on && !(await confirm({
      // Only the first letter lowers: "Skills other AI tools share" keeps its "AI".
      title: `Let PersonalClaw read ${place.label.charAt(0).toLowerCase()}${place.label.slice(1)}?`,
      body: (
        <>
          <p>{place.detail}</p>
          <p className="mt-2">It is outside PersonalClaw’s home, at <code>{place.paths.join(', ')}</code>. You can turn this off here at any time.</p>
        </>
      ),
      confirmLabel: 'Allow reading it',
    }))) return
    // The one place, not the list this page read: another tab may have allowed or revoked a place
    // since, and a saved place no longer offered (an app since removed) is left as it is.
    setBusy(place.id); setErr('')
    try { await api.setOutsideHomePlace(place.id, on, on); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save') }
    finally { setBusy('') }
  }

  return (
    <Section title={title} hint="PersonalClaw reads and writes only inside its own home. It reads something another tool keeps on this computer only when you ask it to look there, such as Look in on the Tools page, or once you turn that place on below. It never writes to or deletes anything there.">
      <RowGroup>
        {data.places.map((place) => (
          <OutsidePlaceRow key={place.id} place={place} busy={busy !== ''}
            onChange={(on) => set(place, on)} />
        ))}
      </RowGroup>
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}

function OutsidePlaceRow({ place, busy, onChange }: {
  place: OutsideHomePlace; busy: boolean; onChange: (on: boolean) => void
}) {
  const hintId = useId()
  return (
    <FieldHintProvider value={hintId}>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-l border-b border-outline-variant/30 py-3 last:border-0">
        <div data-type="body-s" className="text-on-surface">{place.label}</div>
        <div id={hintId} data-type="body-s" className="mt-0.5 text-on-surface-low">
          {place.detail}{' '}
          <span className="break-all font-mono text-on-surface-var">{place.paths.join(', ')}</span>
        </div>
        <div className="col-start-2 row-start-1 flex items-center">
          <Toggle on={place.allowed} label={place.label} disabled={busy} onChange={onChange} />
        </div>
      </div>
    </FieldHintProvider>
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
  label: string; hint: string; hosts: string[]; disabled: boolean
  /** Resolves true once the list is stored. A refused save (the list changed in another tab) keeps
   *  the typed host in the box, where the stale-write notice says the change is kept. */
  onChange: (hosts: string[]) => Promise<boolean>
}) {
  const [draft, setDraft] = useState('')
  const [refused, setRefused] = useState('')
  const add = async () => {
    const h = draft.trim().toLowerCase()
    if (!h) return
    const why = hostRefusal(draft, hosts)
    // The draft is KEPT on a refusal — emptying it is what a successful add looks like.
    if (why) { setRefused(why); return }
    setRefused('')
    if (await onChange([...hosts, h])) setDraft('')
  }
  return (
    <div>
      <div data-type="body-s" className="mb-1 flex items-center gap-1.5 text-on-surface"><Globe size={13} className="text-on-surface-low" /> {label}</div>
      <div data-type="body-s" className="mb-2 text-on-surface-low">{hint}</div>
      <div className="flex flex-col gap-1.5">
        {hosts.map((h) => (
          <div key={h} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
            <code data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{h}</code>
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
            data-type="body-s" className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary" />
          <button type="button" onClick={add} data-type="body-s"
            {...unavailableWhen(!draft.trim(), 'Enter a host first', { busy: disabled })}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary disabled:opacity-50 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed">
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
  // Metrics + chrome only — the type size rides `data-type="caption"` on each consumer.
  const chip = 'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5'
  const digest = <code className="tabular-nums" title={b.sha256}>{b.sha256.slice(0, 12)}…</code>
  if (b.verified) {
    return (
      <span role="status" data-type="caption" className={`${chip} bg-surface-container text-on-surface-low`}
        aria-label={`Baseline v${b.version} matches what shipped: ${b.count} patterns verified against the release sha256`}>
        <ShieldCheck size={13} className="text-primary" aria-hidden />
        <span>Baseline v{b.version} matches what shipped — {b.count} patterns, sha256 {digest}</span>
      </span>
    )
  }
  return (
    <span role="alert" data-type="caption" className={`${chip} border border-danger/30 bg-danger/5 text-danger`}
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

  // One pattern in or out, applied to the stored list — never this panel's copy of it, which a
  // second tab may have changed since (a pattern added there would have been dropped here).
  const save = async (write: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true)
    setErr('')
    try {
      await write()
      onChange()
      return true
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save')
      return false
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    const p = draft.trim()
    if (!p || user.includes(p)) { setDraft(''); return }
    try { new RegExp(p) } catch { setErr('Not a valid regular expression'); return }
    // The draft is kept on a failure — emptying it is what a successful add looks like.
    if (await save(() => api.addDeniedCommand(p))) setDraft('')
  }

  return (
    <Section title="Shell denylist" hint="Regexes matched against every command PersonalClaw runs for an agent or an automation, and every command an agent CLI asks it to run, before anyone is asked to approve one. The packaged baseline is always enforced and read-only; your patterns are added to it, never subtracted from it.">
      <div className="flex flex-col gap-4">
        <div>
          <div data-type="body-s" className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-on-surface-low">
            <span className="inline-flex items-center gap-1.5"><Lock size={13} aria-hidden /> Baseline ({builtin.length}) — always enforced, not editable here</span>
            <BaselineState baseline={baseline} />
          </div>
          <p data-type="body-s" className="mb-2 text-on-surface-low">
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
              <code key={p} data-type="caption" className="block px-2 py-1 text-on-surface-low tabular-nums">{p}</code>
            ))}
          </div>
        </div>
        <div>
          {/* 🪤 NOT `user.length`. The server dedupes a user pattern that already equals a
              baseline entry, so a config list of 3 whose entries duplicate built-ins adds
              NOTHING to what is enforced. The count comes from the effective set
              (`user_additions`), and the shadowed remainder is named rather than hidden —
              otherwise the panel would claim additions that change no behaviour. */}
          <div data-type="body-s" className="mb-0.5 text-on-surface">Your patterns</div>
          <div data-type="body-s" className="mb-2 text-on-surface-low">
            {userAdditions} user addition{userAdditions === 1 ? '' : 's'} on top of the baseline
            {user.length > userAdditions
              && ` · ${user.length - userAdditions} of your ${user.length} entries already match a baseline pattern and add nothing`}
          </div>
          <div className="flex flex-col gap-1.5">
            {user.map((p) => (
              <div key={p} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                <code data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{p}</code>
                <button type="button" disabled={busy} onClick={() => save(() => api.removeDeniedCommand(p))}
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
                data-type="body-s" className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary"
              />
              <button type="button" onClick={add} data-type="body-s"
                {...unavailableWhen(!draft.trim(), 'Enter a pattern first', { busy })}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary disabled:opacity-50 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed">
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
