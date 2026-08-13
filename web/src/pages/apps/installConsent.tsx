import { useState } from 'react'
import {
  ShieldAlert, ShieldCheck, AlertTriangle, Terminal, CalendarClock, Bot, Globe, Copy, Check, Loader2,
} from 'lucide-react'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import { SquareIconButton } from '../../ui/SquareIconButton'
import type { AppSummary, AppInstallResult, AppCronSummary } from '../../lib/api'
import type { GuardedResult } from '../../lib/useGuardedInstall'

/** The APP INSTALL-CONSENT surface — everything a user is shown BEFORE an app is
 *  installed, and the override they must click if the supply-chain scanner objects.
 *
 *  It lives in its own module because it now has two call sites: the Store
 *  (`AppsSection`) and the first-run essential-apps step
 *  (`app/onboarding/EssentialsStep`). Installing from onboarding must disclose exactly
 *  what installing from the Store discloses — same bullets, same advisory rows, same
 *  "Install anyway" — so both import these components rather than each rendering its
 *  own idea of consent. Splitting it out also keeps `AppsSection` lazily loaded: a
 *  static import of the whole Store page from the onboarding flow would have pulled the
 *  Store into the first-load bundle.
 *
 *  Nothing here installs anything. Every component is presentation over data the
 *  catalog already returned, or over a scan verdict the install endpoint returned; the
 *  caller owns the request. */

export function ScanReport({ scan }: { scan: NonNullable<AppInstallResult['scan']> }) {
  const v = scan.verdict
  const tone = v === 'dangerous' ? 'text-danger' : v === 'warning' ? 'text-warn' : 'text-ok'
  const Icon = v === 'clean' ? ShieldCheck : v === 'dangerous' ? ShieldAlert : AlertTriangle
  return (
    <div className="rounded-m border border-outline-variant bg-surface-high p-m">
      <div className={`flex items-center gap-2 ${tone}`} data-type="body-m"><Icon size={16} /> Security scan: {v}</div>
      {scan.findings.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {scan.findings.slice(0, 8).map((f, i) => (
            <li key={i} data-type="body-s" className="text-on-surface-low">
              <span className="text-on-surface">{f.rule}</span> ({f.severity})
              {f.path ? ` — ${f.path}` : ''}{f.evidence ? `: ${f.evidence}` : ''}
            </li>
          ))}
        </ul>
      )}
      {v === 'dangerous' && <div data-type="body-s" className="mt-2 text-danger">This app is blocked — dangerous content cannot be installed.</div>}
    </div>
  )
}

// ── Consent modal — shown when a one-click (card / source-list) install hits an
// overridable WARNING (or a terminal dangerous) verdict, so the scanner findings
// and the "Install anyway" action are reachable without re-typing the source. */
// Exported so the onboarding essential-apps step consents through THIS surface rather
// than a second, quieter one: a warning verdict must show the same scanner findings and
// demand the same explicit "Install anyway" wherever the install was initiated.
export function ConsentModal({ label, result, busy, onConfirm, onClose }: {
  label: string; result: GuardedResult; busy: boolean
  onConfirm: () => void; onClose: () => void
}) {
  // P21: a client-install directive — the app installs on the user's local machine,
  // not this server. Show the copy-paste one-liner instead of the scanner consent UI.
  if (result.clientInstall) {
    return (
      <Modal title={`Install ${label}`} icon={<Terminal size={18} />} onClose={onClose}>
        <div className="flex flex-col gap-m p-l" style={{ minWidth: 460 }}>
          <p data-type="body-s" className="text-on-surface-low">
            {result.error || 'This app installs on your local machine, not this server.'} Run this in your terminal:
          </p>
          {result.clientInstall.shell && <ClientInstallCommand label="Install command" cmd={result.clientInstall.shell} />}
          {result.clientInstall.postInstall && <ClientInstallCommand label="Then" cmd={result.clientInstall.postInstall} />}
          <p data-type="label-s" className="text-on-surface-low">
            The command runs on your machine, outside PersonalClaw's security scanner — review it before running.
          </p>
          <div className="flex justify-end gap-2 pt-s">
            <Button variant="ghost" onClick={onClose}>Done</Button>
          </div>
        </div>
      </Modal>
    )
  }
  const dangerous = result.scan?.verdict === 'dangerous'
  return (
    <Modal title={`Install ${label}`} icon={<ShieldAlert size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <p data-type="body-s" className="text-on-surface-low">
          {dangerous
            ? 'The security scanner flagged dangerous content. This app cannot be installed.'
            : 'The security scanner raised warnings. Review the findings — you can install anyway if you trust the source.'}
        </p>
        {result.scan && <ScanReport scan={result.scan} />}
        <div className="flex justify-end gap-2 pt-s">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {!dangerous && (
            <Button variant="primary" disabled={busy} onClick={onConfirm}>
              {busy ? <Loader2 size={16} className="animate-spin" /> : <ShieldAlert size={16} />} Install anyway
            </Button>
          )}
        </div>
      </div>
    </Modal>
  )
}

/** A monospace command row with a copy button — for the P21 client-install one-liner. */
function ClientInstallCommand({ label, cmd }: { label: string; cmd: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(cmd).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {}) }
  return (
    <div>
      <div data-type="label-s" className="mb-1 text-on-surface-low uppercase tracking-wide">{label}</div>
      <div className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-[0.75rem] text-on-surface">{cmd}</code>
        <SquareIconButton label="Copy command" title={copied ? 'Copied' : 'Copy'} onClick={copy} className="shrink-0">
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </SquareIconButton>
      </div>
    </div>
  )
}

// APE-12. One `appMessaging` entry, in the words a user can act on. The grammar is
// `apps/permissions.py::_matches_any`, so this MUST mirror it: a trailing `*` is a
// name PREFIX, not a literal app, and a bare `*` matches every name. Rendering
// `mail-*` as though an app called "mail-*" existed would understate the grant — it
// covers every current AND future app under that prefix. (`_matches_any`'s third
// branch also treats an exact entry as a `/`-path prefix; app names are kebab-case
// with no `/`, so that branch cannot widen an app target and is not claimed here.)
function describeMessagingTarget(pattern: string): string {
  if (pattern === '*') return 'any installed app'
  if (pattern.endsWith('*')) return `any app whose name starts with “${pattern.slice(0, -1)}”`
  return pattern
}

// EI-12 D2. The bullets are the permissions the gateway ENFORCES server-side, and
// `network` is deliberately not among them: an app's provider code is imported
// in-process by the gateway, so there is no per-app egress chokepoint to enforce at
// (docs/security/limitations.md §2). Listing it beside storage/cron/agent — which are
// enforced — would read as a grant the platform polices, and OMITTING it when the app
// declares `network: false` would read as a block. Both are false, so it gets its own
// advisory row, rendered either way.
//
// APE-12. `appMessaging` is the OPPOSITE case and belongs in the enforced bullets: the
// broker (`POST /api/apps/message`) is the only app-to-app path and refuses an
// undeclared target 403 + SEL (apps/messaging.py). It used to render nowhere at all —
// `AppPermissionsWire` never declared the field — so install consent never said which
// other apps an app may talk to. Declaring nothing is disclosed too (the caption
// below the bullets): deny-by-default is the real behaviour, and silence would repeat
// the mistake D2 found for `network`.
export function PermissionList({ perms }: { perms: AppSummary['permissions'] }) {
  const rows: string[] = []
  if (perms.api?.length) rows.push(`API: ${perms.api.join(', ')}`)
  if (perms.events?.length) rows.push(`Events: ${perms.events.join(', ')}`)
  if (perms.mcpTools?.length) rows.push(`MCP tools: ${perms.mcpTools.join(', ')}`)
  if (perms.memory) rows.push(`Memory: ${perms.memory}`)
  if (perms.storage) rows.push('Storage')
  if (perms.cron) rows.push('Scheduled jobs')
  if (perms.agent) rows.push('Run background agents')
  const messaging = perms.appMessaging ?? []
  if (messaging.length) {
    rows.push(`App messaging: ${messaging.map(describeMessagingTarget).join(', ')}`)
  }
  return (
    <div>
      <div data-type="label-m" className="mb-1 text-on-surface">Permissions the gateway enforces</div>
      {rows.length === 0 ? <div data-type="body-s" className="text-on-surface-low">None — this app is granted no gateway capability.</div> : (
        <ul className="flex flex-col gap-1">
          {rows.map((r, i) => <li key={i} data-type="body-s" className="text-on-surface-low">• {r}</li>)}
        </ul>
      )}
      {messaging.length === 0 && (
        <div data-type="body-s" className="mt-1 text-on-surface-low">
          App messaging: none — it declared no target, and the gateway broker is the only
          way one app can reach another, so it can message no other app.
        </div>
      )}
      <div className="mt-2 flex gap-2 rounded-m border border-outline-variant bg-surface-high p-m">
        <Globe size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-on-surface-low" />
        <div data-type="body-s" className="text-on-surface-low">
          <span className="text-on-surface">Network access: {perms.network ? 'declared' : 'not declared'}</span>
          {' — advisory only. PersonalClaw does not confine an app\'s outbound traffic: this app\'s '}
          code can reach the network either way. The declaration is disclosure, not containment.
        </div>
      </div>
    </div>
  )
}

// P29: the recurring jobs an app declares, shown pre-install. Each is an agent run on
// a schedule — we surface the cadence + which agent + the prompt so the user sees what
// will run unattended before granting the `cron` permission.
function fmtCadence(c: AppCronSummary): string {
  if (c.cron_expr) return c.cron_expr
  const s = c.every ?? 0
  if (!s) return 'on a schedule'
  if (s % 86400 === 0) { const d = s / 86400; return `every ${d === 1 ? 'day' : `${d} days`}` }
  if (s % 3600 === 0) { const h = s / 3600; return `every ${h === 1 ? 'hour' : `${h} hours`}` }
  if (s % 60 === 0) { const m = s / 60; return `every ${m === 1 ? 'minute' : `${m} minutes`}` }
  return `every ${s}s`
}

export function CronConsentList({ crons }: { crons: AppCronSummary[] }) {
  return (
    <div>
      <div data-type="label-m" className="mb-1 flex items-center gap-1.5 text-on-surface">
        <CalendarClock size={14} /> Scheduled jobs
      </div>
      <div data-type="body-s" className="mb-2 text-on-surface-low">
        This app runs {crons.length === 1 ? 'a background agent' : `${crons.length} background agents`} on a schedule once installed.
      </div>
      <ul className="flex flex-col gap-1.5">
        {crons.map((c, i) => (
          <li key={c.name || i} className="rounded-m border border-outline-variant bg-surface-high p-m">
            <div className="flex items-center justify-between gap-2">
              <span data-type="body-s" className="text-on-surface">{c.name || 'job'}</span>
              <span data-type="label-s" className="shrink-0 text-on-surface-low">{fmtCadence(c)}</span>
            </div>
            {(c.agent || c.message) && (
              <div className="mt-1 flex items-start gap-1.5 text-on-surface-low" data-type="label-s">
                <Bot size={12} className="mt-0.5 shrink-0" />
                <span className="min-w-0">
                  {c.agent && <span className="text-on-surface-var">{c.agent}</span>}
                  {c.agent && c.message && ' — '}
                  {c.message && <span className="line-clamp-2">{c.message}</span>}
                </span>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
