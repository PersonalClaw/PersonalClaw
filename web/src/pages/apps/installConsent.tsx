import { useState } from 'react'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote } from '../../lib/scanFindings'
import {
  ShieldAlert, ShieldCheck, ShieldQuestion, BadgeCheck, AlertTriangle, Terminal, CalendarClock,
  Bot, Globe, Copy, Check, Loader2,
} from 'lucide-react'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import { SquareIconButton } from '../../ui/SquareIconButton'
import type { AppSummary, AppInstallResult, AppCronSummary, AppScanReport } from '../../lib/api'
import { terminalRefusalReason, type GuardedResult } from '../../lib/useGuardedInstall'

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

/** Close a server-composed clause so a following sentence reads as a separate one. Backend error
 *  strings are composed without terminal punctuation (they are API fields, not prose), so any surface
 *  that appends its own sentence to one has to supply the boundary itself. */
const sentence = (s: string) => (/[.!?…]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`)

/** SH-3: the artifact-signature row. Shown on the SAME surface as the scan verdict,
 *  because provenance and content are two different questions a user consents over and
 *  a UI that shows only one invites "it scanned clean" to be read as "it's from who it
 *  says". `invalid` is a refusal the user cannot override, so it renders as danger, not
 *  as a warning to click through. `unsigned` is stated plainly rather than hidden —
 *  community apps are unsigned by design and that is the honest, non-alarming default. */
function SignatureRow({ signature }: { signature: NonNullable<AppScanReport['signature']> }) {
  const s = signature.state
  const tone = s === 'invalid' ? 'text-danger' : s === 'signed' ? 'text-ok' : 'text-on-surface-low'
  const Icon = s === 'invalid' ? ShieldAlert : s === 'signed' ? BadgeCheck : ShieldQuestion
  const label =
    s === 'signed' ? `Signed by ${signature.signer || 'a trusted key'}`
      : s === 'invalid' ? 'Invalid signature — install refused'
        : 'Unsigned — community tier'
  return (
    <div className="mt-2 flex flex-col gap-1">
      <div className={`flex items-center gap-2 ${tone}`} data-type="body-m">
        <Icon size={16} /> {label}
      </div>
      {s === 'invalid' && signature.reason && (
        <div data-type="body-s" className="text-danger">{signature.reason}</div>
      )}
      {s === 'unsigned' && (
        <div data-type="body-s" className="text-on-surface-low">
          No maintainer signature, so PersonalClaw can't confirm who published this. It still
          installs — the security scan above is what gates it.
        </div>
      )}
    </div>
  )
}

export function ScanReport({ scan }: { scan: NonNullable<AppInstallResult['scan']> }) {
  const v = scan.verdict
  const tone = v === 'dangerous' ? 'text-danger' : v === 'warning' ? 'text-warn' : 'text-ok'
  const Icon = v === 'clean' ? ShieldCheck : v === 'dangerous' ? ShieldAlert : AlertTriangle
  return (
    <div className="rounded-m border border-outline-variant bg-surface-high p-m">
      <div className={`flex items-center gap-2 ${tone}`} data-type="body-m"><Icon size={16} /> Security scan: {v}
        {scan.findings.length > 0 && ` · ${scan.findings.length} finding${scan.findings.length === 1 ? '' : 's'}`}
      </div>
      {scan.signature && <SignatureRow signature={scan.signature} />}
      {scan.findings.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {scan.findings.slice(0, SCAN_FINDINGS_SHOWN).map((f, i) => (
            <li key={i} data-type="body-s" className="text-on-surface-low">
              <span className="text-on-surface">{f.rule}</span> ({f.severity})
              {f.path ? ` — ${f.path}` : ''}{f.evidence ? `: ${f.evidence}` : ''}
            </li>
          ))}
          {/* The list stops at the cap; without this the eight shown read as all of them, on the
              one screen whose entire job is an informed yes/no. */}
          {hiddenFindingsNote(scan.findings.length) && (
            <li data-type="body-s" className="text-on-surface-low italic">
              {hiddenFindingsNote(scan.findings.length)}
            </li>
          )}
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
          {/* Two sentences, so they have to READ as two. The server's reason arrives WITHOUT terminal
              punctuation (`app_manager` composes "'<name>' installs on your local machine, not this
              server"), and this line appends an instruction to it — which rendered as
              "…not this server Run this in your terminal:". Only the hard-coded fallback ends in a
              period, so the seam is invisible in code review and shows up only on the real path.
              Normalizing here rather than adding a period server-side: the same string is an API
              error field with other consumers, and a sentence boundary is this surface's concern. */}
          <p data-type="body-s" className="text-on-surface-low">
            {sentence(result.error || 'This app installs on your local machine, not this server.')} Run this in your terminal:
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
  // A terminal refusal (dangerous content OR an invalid signature) explains itself and
  // offers no override; anything else here is a consentable warning.
  const refusal = terminalRefusalReason(result)
  return (
    <Modal title={`Install ${label}`} icon={<ShieldAlert size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <p data-type="body-s" className="text-on-surface-low">
          {refusal
            || 'The security scanner raised warnings. Review the findings — you can install anyway if you trust the source.'}
        </p>
        {result.scan && <ScanReport scan={result.scan} />}
        <div className="flex justify-end gap-2 pt-s">
          {/* A terminal refusal leaves NOTHING to cancel — the install was already refused server-side,
              so this button only dismisses. "Cancel" claims the user is abandoning a pending action and
              invites the reading that the app might otherwise still install. `Done` is the verb this
              file already uses for its one dismiss-only footer (the client-install branch above), and
              the two other dismiss-only modals in the app (`chat/SessionSkillsReview`,
              `ChatPage`) — so this converges, it does not invent. When the verdict IS consentable the
              footer keeps "Cancel", because there a real pending action ("Install anyway") is being
              abandoned. `AppsSection`'s install/update modals keep "Cancel" for the same reason: they
              always render a commit button (disabled, with the refusal as its `disabledReason`), so
              they are never dismiss-only. */}
          <Button variant="ghost" onClick={onClose}>{refusal ? 'Done' : 'Cancel'}</Button>
          {!refusal && (
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
  // APE-10. Consented cross-app READ-ONLY file sharing belongs in the enforced bullets:
  // a read is mounted only where storage is granted (backend_runtime) and only when the
  // consumer names the sharer AND the sharer opted in with `storageShared` (double-
  // declaration). Same target grammar as `appMessaging`, so it is described the same way
  // (a trailing `*` is a name prefix). `storageShared` (this app exposing its OWN data)
  // is disclosed too — it is what lets other apps read this one.
  if (perms.storageShared) rows.push('Shares its data with apps you grant read access')
  const sharedReads = perms.storageRead ?? []
  if (sharedReads.length) {
    rows.push(`Reads other apps' data (read-only): ${sharedReads.map(describeMessagingTarget).join(', ')}`)
  }
  // DC-2. Native desktop capabilities belong in the ENFORCED bullets: the gateway
  // mediates every app→shell call and refuses an undeclared capability 403 + SEL
  // (handlers/desktop.py). Unlike `appMessaging` there is no wildcard to explain —
  // the vocabulary is closed and each entry is an exact capability — so the names are
  // rendered as-is, humanized.
  const desktopCaps = perms.desktop ?? []
  if (desktopCaps.length) {
    rows.push(`Desktop capabilities: ${desktopCaps.map((c) => c.replace(/_/g, ' ')).join(', ')}`)
  }
  // INU-7. Raising a proposal into your inbox is an enforced grant, not a courtesy:
  // `POST /api/inbox/proposals` 403s any kind not declared here and refuses a callback
  // into another app, with a SEL row either way. Each entry is named by its LABEL (what
  // the row will say) rather than its slug — the consent surface should read like the
  // thing the user will be asked to approve.
  const proposalKinds = perms.proposals ?? []
  if (proposalKinds.length) {
    rows.push(
      `Can ask you to approve: ${proposalKinds.map((p) => p.label || p.kind_suffix).join(', ')}`,
    )
  }
  // APE-1. `backgroundTasks` and `eventSubscriptions` are a THIRD case, distinct from both
  // the enforced bullets and D2's `network` advisory. They are enforced by nothing today
  // because the runtimes do not exist: no core code hosts an app worker (APE-3) and no
  // platform event is delivered to any app, declared or not (APE-2's registry). So they
  // may not join the enforced bullets — that is the D2 defect. Nor are they the network
  // case, whose row must render even when undeclared because absence would read as
  // "blocked": here absence really does mean the app gets nothing, and so does presence,
  // so an always-on row would only imply the platform has a worker host it lacks.
  //
  // They ARE disclosed when declared, because the declaration is a STANDING grant: it
  // goes live with no second consent prompt the moment that support ships. Telling the
  // user at install is the only moment they get to weigh it.
  const declaredEvents = perms.eventSubscriptions ?? []
  const pending: string[] = []
  if (perms.backgroundTasks) pending.push('Run a long-lived background worker')
  if (declaredEvents.length) pending.push(`Receive platform events: ${declaredEvents.join(', ')}`)
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
      {/* DC-2. Same reasoning as the messaging caption above: deny-by-default is the
          real behaviour, and staying silent about it would let absence read as
          "unrestricted" rather than "no native reach at all". */}
      {desktopCaps.length === 0 && (
        <div data-type="body-s" className="mt-1 text-on-surface-low">
          Desktop capabilities: none — it declared no native capability, and the gateway
          mediates every app→desktop call, so it can reach nothing native on this machine.
        </div>
      )}
      {/* APE-1. Rendered only when declared, and never as a bullet: `enforcedRows` in
          permissionConsent.test.tsx reads every <li> in this component as "the enforced
          list", and these two are enforced by nothing. Divs keep that reading true. */}
      {pending.length > 0 && (
        <div className="mt-2 rounded-m border border-outline-variant bg-surface-high p-m">
          <div data-type="label-m" className="mb-1 text-on-surface">Declared, not yet in effect</div>
          <div className="flex flex-col gap-1">
            {pending.map((p, i) => (
              <div key={i} data-type="body-s" className="text-on-surface-low">• {p}</div>
            ))}
          </div>
          <div data-type="body-s" className="mt-1 text-on-surface-low">
            PersonalClaw does not run app workers or deliver platform events yet, so this
            grants the app nothing today — it is disclosure, not capability. It takes
            effect without asking you again once that support ships.
          </div>
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
