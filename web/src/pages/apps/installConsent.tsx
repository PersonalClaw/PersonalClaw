import { useCallback, useRef, useState, type ReactNode } from 'react'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote, ruleGloss } from '../../lib/scanFindings'
import { trustTierLabel } from '../../lib/trustTier'
import {
  ShieldAlert, ShieldCheck, ShieldQuestion, BadgeCheck, AlertTriangle, Terminal, CalendarClock, Bot,
  Globe, LayoutDashboard, PackagePlus, Copy, Check, Server, Download, RefreshCw, Sparkles, Loader2,
} from 'lucide-react'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { FieldError } from '../../ui/forms'
import {
  api, type AppSummary, type AppInstallResult, type AppCronSummary, type AppScanReport, type AppCatalogEntry,
  type AppPythonDependency, type AppDisclosure, type AppScanFinding,
} from '../../lib/api'
import { terminalRefusalReason } from '../../lib/useGuardedInstall'
import { readableErrText } from '../../lib/errText'
import { copyText } from '../../app/clipboard'
import { launchChat } from '../../app/appSdk'

/** The APP INSTALL-CONSENT surface — the one path every install and update takes, and
 *  everything a user is shown before anything is installed.
 *
 *  🔑 ONE PATH, NOT A COMPONENT PER SURFACE. The Store card, the Store detail panel, Manage
 *  Sources, Install from URL, Update, and the first-run essential-apps step all open THIS
 *  dialog through {@link useAppInstall}. It used to be a modal each caller opened only when
 *  the scanner objected, so a clean-scanning app installed on one click with no consent
 *  screen at all — measured on the real image, 50 of 65 Store installs, including Growth
 *  Tracker (API reach into projects, tasks and knowledge, an agent grant, a daily cron) and
 *  Research Lab (an hourly background agent), whose jobs then sat in `triggers.json`,
 *  enabled, unseen.
 *
 *  🔑 WHAT IT DISCLOSES IS THE SERVER'S READING OF THE BYTES ABOUT TO BE INSTALLED. The
 *  dialog opens by asking `POST /api/apps/preview`, which stages the source, scans it and
 *  returns what it grants and runs (`apps/disclosure.describe`) plus a `consent` digest of
 *  those exact bytes; Install sends the digest back, and the server installs only if the
 *  bytes still match. So a registry listing or a pasted URL — whose manifest the catalog
 *  never read — is disclosed as fully as a local card, and a source that changes between
 *  the review and the click is reviewed again instead of installed on the first yes.
 *
 *  It lives in its own module because the onboarding step renders it too, and a static
 *  import of the whole Store page from the first-run flow would pull the Store into the
 *  first-load bundle. */

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
function SignatureRow({ signature, verdict, tier }: {
  signature: NonNullable<AppScanReport['signature']>
  /** The scan verdict on the SAME report. The unsigned note has to know it: on a
   *  `dangerous` verdict there is no install to reassure anyone about, and the sentence
   *  that reassured them sat five lines above "This app is blocked". */
  verdict: string
  /** The trust tier the install gate computed for these exact bytes (`ScanReport.tier`).
   *  Spelled through `trustTierLabel`, which is the SAME map the Tools page badge reads —
   *  #2627: this row disclosed "community tier" while the Tools page then called the very
   *  same bundle `built-in`, and two hardcoded literals is how that happens. */
  tier?: string
}) {
  const s = signature.state
  const blocked = verdict === 'dangerous'
  const tone = s === 'invalid' ? 'text-danger' : s === 'signed' ? 'text-ok' : 'text-on-surface-low'
  const Icon = s === 'invalid' ? ShieldAlert : s === 'signed' ? BadgeCheck : ShieldQuestion
  const label =
    s === 'signed' ? `Signed by ${signature.signer || 'a trusted key'}`
      : s === 'invalid' ? 'Invalid signature — install refused'
        : `Unsigned — ${trustTierLabel(tier)}`
  return (
    <div className="mt-s flex flex-col gap-xs">
      <div className={`flex items-center gap-s ${tone}`} data-type="body-m">
        <Icon size={16} /> {label}
      </div>
      {s === 'invalid' && signature.reason && (
        <div data-type="body-s" className="text-danger">{signature.reason}</div>
      )}
      {/* The second sentence is about what the MISSING SIGNATURE does or does not stop, so
          it has to agree with the verdict it sits beside. Unconditional, it told the user
          "It still installs" a few lines above "This app is blocked — dangerous content
          cannot be installed": one screen asserting both that the app installs and that it
          cannot. On a refusal the honest version of the same point is that being unsigned
          is not why — the scan is — and neither fact reopens the install. */}
      {s === 'unsigned' && (
        <div data-type="body-s" className="text-on-surface-low">
          No maintainer signature, so PersonalClaw can't confirm who published this.{' '}
          {blocked
            ? 'That is not why this install was refused — the security scan is, and it cannot be overridden.'
            : 'It still installs — the security scan above is what gates it.'}
        </div>
      )}
    </div>
  )
}

/** Whether a finding describes something this app's OWN code does when it runs — the
 *  question consent turns on. Two server-side proofs say it does not: the match is inert
 *  text (`reachability` unreachable / commentary), or it sits in a file nothing the app
 *  runs loads (`runtime` unloaded — its own tests and fixtures). Everything else stays in
 *  the prominent group, `untraceable` included: "PersonalClaw could not rule it out" is no
 *  reason to move a finding out of sight. Never decided from a file NAME — a module called
 *  `test_x.py` that the provider imports is code the app runs, and the server says so. */
export function findingNotRunByApp(f: AppScanFinding): boolean {
  return f.reachability === 'unreachable' || f.reachability === 'commentary' || f.runtime === 'unloaded'
}

/** The one-line reading of a finding's reachability/runtime facts, or `''` when there is
 *  nothing to add to the row. The server's own reason rides along as the line's `title`. */
function findingStatus(f: AppScanFinding): string {
  if (f.reachability === 'unreachable') return 'Inert text: the app holds this string, but nothing in it can run it.'
  if (f.reachability === 'commentary') return 'Inside a comment: text the code never runs.'
  if (f.runtime === 'unloaded') return "In the app's own tests or fixtures: nothing the app runs loads this file."
  if (f.reachability === 'reachable') return 'Live code: the app can run this.'
  if (f.reachability === 'unparseable') return 'This file could not be analysed, so it is treated as code that runs.'
  if (f.runtime === 'untraceable') return "PersonalClaw couldn't check whether the app runs this file, so it is counted as code that does."
  return ''
}

function FindingList({ findings }: { findings: AppScanFinding[] }) {
  return (
    <ul className="mt-xs flex flex-col gap-xs">
      {/* rule (severity) — path: evidence, then what the rule MEANS, then whether the app can
          even run it. The first line is the scanner's own vocabulary and the evidence is the
          real argv; neither tells a non-expert what the app can do to their machine, which is
          the only question they can actually answer. */}
      {findings.slice(0, SCAN_FINDINGS_SHOWN).map((f, i) => {
        const status = findingStatus(f)
        return (
          <li key={i} data-type="body-s" className="text-on-surface-low" data-finding={f.rule}>
            <span className="text-on-surface">{f.rule}</span> ({f.severity})
            {f.path ? ` — ${f.path}` : ''}{f.evidence ? `: ${f.evidence}` : ''}
            {ruleGloss(f.rule) && (
              <span className="block text-on-surface-var">{ruleGloss(f.rule)}</span>
            )}
            {status && (
              <span className="block text-on-surface-low italic" data-testid="finding-status"
                title={f.reachability_reason || f.runtime_reason || undefined}>{status}</span>
            )}
          </li>
        )
      })}
      {/* The list stops at the cap; without this the eight shown read as all of them, on the
          one screen whose entire job is an informed yes/no. */}
      {hiddenFindingsNote(findings.length) && (
        <li data-type="body-s" className="text-on-surface-low italic">
          {hiddenFindingsNote(findings.length)}
        </li>
      )}
    </ul>
  )
}

export function ScanReport({ scan }: { scan: AppScanReport }) {
  const v = scan.verdict
  const tone = v === 'dangerous' ? 'text-danger' : v === 'warning' ? 'text-warn' : 'text-ok'
  const Icon = v === 'clean' ? ShieldCheck : v === 'dangerous' ? ShieldAlert : AlertTriangle
  // 🔑 Everything not proven inert first and in full; what the app provably cannot run grouped
  // below it. A dialog that led with a test fixture's "reads a credential file and sends its
  // contents off this machine" described the app's tests as its behaviour (Spec Builder, and 14
  // others). The first group carries no heading of its own: it holds text as well as code (a
  // README addressing your assistant), so "code this app can run" would be a claim about half
  // of it — the collapsed group's own summary is the separation.
  const runs = scan.findings.filter((f) => !findingNotRunByApp(f))
  const notRun = scan.findings.filter(findingNotRunByApp)
  return (
    <div className="rounded-md border border-outline-variant bg-surface-high p-m">
      <div className={`flex items-center gap-s ${tone}`} data-type="body-m"><Icon size={16} /> Security scan: {v}
        {scan.findings.length > 0 && ` · ${scan.findings.length} finding${scan.findings.length === 1 ? '' : 's'}`}
      </div>
      {scan.signature && <SignatureRow signature={scan.signature} verdict={v} tier={scan.tier} />}
      {runs.length > 0 && (
        <div className="mt-s" data-testid="scan-runs">
          <FindingList findings={runs} />
        </div>
      )}
      {notRun.length > 0 && (
        <details className="mt-s" data-testid="scan-not-run" open={runs.length === 0}>
          <summary data-type="label-m" className="cursor-pointer text-on-surface">
            {notRun.length} the app cannot run
          </summary>
          <div data-type="body-s" className="mt-xs text-on-surface-low">
            Inert text, or files nothing the app runs loads — usually its own tests and fixtures.
            They are listed because the scanner matched them, not because installing runs them.
          </div>
          <FindingList findings={notRun} />
        </details>
      )}
      {v === 'dangerous' && <div data-type="body-s" className="mt-s text-danger">This app is blocked — dangerous content cannot be installed.</div>}
    </div>
  )
}

/** The disclosure a CATALOG row can make, or `undefined` when its manifest was never read.
 *
 *  🔑 The catalog's fields alone cannot answer this. `CatalogEntry.to_dict` is `asdict`, so
 *  every row ships `permissions: {}` — both for a scanned manifest that declares nothing and
 *  for a registry POINTER whose manifest is not fetched until the install is reviewed — and
 *  `{}` is truthy. `consentKnown` is the one authority for the distinction, and this is the
 *  one place it is consulted, so no surface can claim "granted no gateway capability" about
 *  an app nobody read. The install dialog never uses this: it discloses the server's reading
 *  of the bytes being installed, which exists for a pointer too. */
export function disclosureOf(entry: AppCatalogEntry | undefined): AppDisclosure | undefined {
  if (!entry?.consentKnown) return undefined
  return {
    permissions: entry.permissions ?? {},
    crons: entry.crons ?? [],
    pythonDependencies: entry.pythonDependencies ?? [],
    hasUI: Boolean(entry.hasUI),
    uiComponents: entry.uiComponents ?? '',
    hasBackend: Boolean(entry.hasBackend),
    onInstall: entry.onInstall ?? '',
    onUpdate: entry.onUpdate ?? '',
    mcpServers: entry.mcpServers ?? [],
  }
}

type DisclosureAction = 'install' | 'update'

/** Everything installing (or updating to) an app grants and runs, on ONE surface — the
 *  scheduled jobs it switches on, the permissions the gateway enforces with the advisory
 *  rows beside them, and what it runs on this machine. Every consent surface renders this
 *  and nothing else, so none can show the bullets and forget the jobs, or show the jobs and
 *  forget the packages — the failures this module has had one at a time. */
export function AppDisclosureView({ disclosure, action }: { disclosure: AppDisclosure; action: DisclosureAction }) {
  return (
    <div className="flex flex-col gap-m" data-testid="app-disclosure">
      {disclosure.crons.length > 0 && <CronConsentList crons={disclosure.crons} action={action} />}
      <PermissionList perms={disclosure.permissions ?? {}} hostUi={consentHostUi(disclosure)}
        pythonDeps={disclosure.pythonDependencies} />
      <RunsRow disclosure={disclosure} action={action} />
    </div>
  )
}

/** What the install runs on this machine beyond its grants: a server process of its own,
 *  the shell command its install (or update) hook executes in the app's folder, and each
 *  MCP server it adds to the assistant. Renders nothing when there is none of it. */
function RunsRow({ disclosure: d, action }: { disclosure: AppDisclosure; action: DisclosureAction }) {
  const hook = action === 'update' ? d.onUpdate : d.onInstall
  const items: ReactNode[] = []
  if (d.hasBackend) items.push('Starts its own server process, which keeps running while the app is on.')
  if (hook) {
    items.push(<>Runs <code className="font-mono text-on-surface">{hook}</code> in the app's folder during the {action}.</>)
  }
  if (d.mcpServers.length) {
    items.push(
      <>Adds {d.mcpServers.length === 1 ? 'an MCP server' : `${d.mcpServers.length} MCP servers`} your assistant can call:{' '}
        {d.mcpServers.map((s, i) => (
          <span key={s.name}>{i > 0 ? ', ' : ''}<span className="text-on-surface">{s.name}</span>
            {s.launches && <> (<code className="font-mono">{s.launches}</code>)</>}</span>
        ))}.
      </>,
    )
  }
  if (!items.length) return null
  return (
    <div className="flex gap-s rounded-md border border-outline-variant bg-surface-high p-m" data-testid="consent-runs">
      <Server size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-on-surface-low" />
      <div data-type="body-s" className="text-on-surface-low">
        <div className="text-on-surface">What it runs on this machine</div>
        <ul className="mt-xs flex flex-col gap-xs">
          {items.map((item, i) => <li key={i}>• {item}</li>)}
        </ul>
      </div>
    </div>
  )
}

/** One comparable line per thing an app gets, keyed so an update can say what it ADDS and
 *  what it REMOVES. The keys carry the whole fact (a job's cadence, agent and prompt), so a
 *  retimed job reads as one gone and one new rather than as no change. */
function disclosureFacts(d: AppDisclosure): { key: string; label: string }[] {
  return [
    ...permissionRows(d.permissions ?? {}).map((r) => ({ key: `perm:${r}`, label: r })),
    ...(d.permissions?.network ? [{ key: 'network', label: 'Network access declared' }] : []),
    ...d.crons.filter((c) => c.scheduled !== false).map((c) => ({
      key: `cron:${JSON.stringify([c.name, c.every, c.cron_expr, c.agent, c.message])}`,
      label: `Scheduled job “${c.name || 'job'}” — ${fmtCadence(c)}`,
    })),
    ...d.pythonDependencies.map((p) => ({ key: `py:${p.spec}`, label: `Python package ${p.spec}` })),
    ...(d.hasUI || d.uiComponents ? [{ key: 'ui', label: 'Runs in this dashboard page' }] : []),
    ...(d.hasBackend ? [{ key: 'backend', label: 'Its own server process' }] : []),
    ...(d.onInstall ? [{ key: `oninstall:${d.onInstall}`, label: `Install command: ${d.onInstall}` }] : []),
    ...(d.onUpdate ? [{ key: `onupdate:${d.onUpdate}`, label: `Update command: ${d.onUpdate}` }] : []),
    ...d.mcpServers.map((s) => ({ key: `mcp:${s.name}:${s.launches}`, label: `MCP server “${s.name}”` })),
  ]
}

/** On an update review: what the new version gets that the installed one did not, and what
 *  it stops getting. The server decided whether consent is needed (`needed`) on the same
 *  projection, so when it says yes and nothing here differs, that is said too rather than
 *  showing an empty list beside a consent button. */
function DisclosureChanges({ previous, current, needed }: { previous: AppDisclosure; current: AppDisclosure; needed: boolean }) {
  const before = disclosureFacts(previous)
  const after = disclosureFacts(current)
  const had = new Set(before.map((f) => f.key))
  const has = new Set(after.map((f) => f.key))
  const added = after.filter((f) => !had.has(f.key))
  const removed = before.filter((f) => !has.has(f.key))
  return (
    <div className="rounded-md border border-outline-variant bg-surface-high p-m" data-testid="update-changes">
      <div data-type="label-m" className="text-on-surface">What this update changes</div>
      {added.length === 0 && removed.length === 0 ? (
        <div data-type="body-s" className="mt-xs text-on-surface-low">
          {needed
            ? 'Details of what the app gets change — review the full list below.'
            : 'Nothing the app gets changes.'}
        </div>
      ) : (
        <ul className="mt-xs flex flex-col gap-xs">
          {added.map((f) => <li key={`+${f.key}`} data-type="body-s" className="text-on-surface">+ Adds: {f.label}</li>)}
          {removed.map((f) => <li key={`-${f.key}`} data-type="body-s" className="text-on-surface-low">− Drops: {f.label}</li>)}
        </ul>
      )}
    </div>
  )
}

/** What browser code this app ships (#492) — the consent fact the permission block
 *  cannot state, because an app's UI bundle is imported into THIS page and the `api`
 *  allowlist bounds its backend and its SDK client, not its page code
 *  (`docs/security/limitations.md` §4).
 *
 *  🔑 ONE reading for both wires. The pre-install `AppCatalogEntry` and the installed
 *  `AppSummary` carry the same two field names for it, so this accepts either and the
 *  Store card, the install dialog, and the installed-app panel cannot answer the
 *  question differently — the mistake `disclosureOf` exists to prevent, one field
 *  along. The two facts stay SEPARATE because a components module is the broader
 *  one: the shell loads it for an enabled app with no page visit at all.
 *
 *  `undefined` in, `undefined` out — and the row then renders nothing. A registry
 *  POINTER ships `hasUI: false` for the same reason it ships `permissions: {}` (its
 *  manifest is not read until install), and "no browser code" is a CLAIM, not a
 *  default, so it must not be made about an app nobody has read. Nothing needs to
 *  re-check `consentKnown` here: a catalog row reaches this only through
 *  {@link disclosureOf}, which gates on that flag. */
export function consentHostUi(
  a: Pick<AppCatalogEntry, 'hasUI' | 'uiComponents'> | undefined,
): { page: boolean; components: boolean } | undefined {
  if (!a) return undefined
  return { page: Boolean(a.hasUI), components: Boolean(a.uiComponents) }
}

/** A monospace command row with a copy button — for the P21 client-install one-liner. */
function ClientInstallCommand({ label, cmd }: { label: string; cmd: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => { if (await copyText(cmd, 'the command')) { setCopied(true); setTimeout(() => setCopied(false), 1500) } }
  return (
    <div>
      <div data-type="label-s" className="mb-xs text-on-surface-low uppercase tracking-wide">{label}</div>
      <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-s">
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
//
// THREE network states, not two. The row read `not declared` for a manifest carrying an
// explicit `"network": false`, collapsing a STATEMENT by the author into silence — and it
// is the statement a user most wants: "this app says it does not go out to the internet".
// `undefined` (the key absent) is the only honest "not declared"; the server now emits
// `false` for a declining app (`Permissions.to_dict` keeps the key when the manifest
// mentioned it) so the two cases can read differently here.
function networkClaim(network: boolean | undefined): string {
  if (network === undefined) return 'not declared'
  return network ? 'declared' : 'declared as denied'
}

// #492. The bullets and the network row are both about the app's BACKEND reach. An app
// that ships a UI also gets a second kind of authority the manifest has no field for: its
// bundle is fetched and `import()`-ed into this very page (`appSdk.loadContributedModule`
// — no iframe, sharing the host React instance), so its page code holds the host DOM, the
// owner's session cookie and authenticated same-origin `/api/*` reach. The gateway cannot
// scope that: `app_permission_middleware` acts on requests carrying an app identity, and a
// bare `fetch` from app UI carries none, so it arrives as the owner.
//
// That makes the consent screen's silence the actual defect (#492): it showed declared
// permissions, and the thing it showed was not the thing that bounds the UI. Same fix as
// EI-12 D2 made for `network` — its own advisory row, stated either way, because absence
// would otherwise read as "the platform confines it". Rendered ONLY when the caller
// supplies the fact: an omitted `hostUi` leaves the row out rather than asserting "no
// browser code" about an app nobody has read. `consentHostUiRendered.test.ts` is the rail
// that keeps every production call site supplying it.
function HostPageRow({ hostUi }: { hostUi: { page: boolean; components: boolean } }) {
  const runs = hostUi.page || hostUi.components
  return (
    <div className="mt-s flex gap-s rounded-md border border-outline-variant bg-surface-high p-m">
      <LayoutDashboard size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-on-surface-low" />
      <div data-type="body-s" className="text-on-surface-low">
        <span className="text-on-surface">Runs in this dashboard page: {runs ? 'yes' : 'no'}</span>
        {runs ? (
          <>
            {' — advisory only. PersonalClaw loads this app\'s interface into the dashboard itself, '}
            so its browser code has the same page, the same session and the same /api access you
            do. The permissions above bound its backend and its SDK calls, not its page code.
            {hostUi.components && ' Its component module loads as soon as the app is enabled, without you opening its page.'}
          </>
        ) : (
          ' — this app ships no browser code, so nothing of it runs in the dashboard.'
        )}
      </div>
    </div>
  )
}

/** Comma-separated requirement specifiers, each in monospace — because the specifier IS
 *  the disclosure. A user deciding about `anthropic>=0.20` has to see that string; "this
 *  app installs some Python packages" is a category, not a fact they can weigh. */
function specList(specs: string[]) {
  return specs.map((s, i) => (
    <span key={s}>{i > 0 ? ', ' : ''}<code className="font-mono text-on-surface">{s}</code></span>
  ))
}

// The install runs `pip install` into `<home>/app-python`, which the GATEWAY loads into its own
// process (`apps/app_python.py`). This row is that disclosure, and it belongs with `network`
// and the host-page row rather than in the
// bullets above them — the same argument EI-12 D2 made for `network` and #492 made for
// dashboard code. The bullets are grants the gateway ENFORCES; a module that is importable
// in-process has no chokepoint to enforce at, so rendering it as a bullet would read as a
// capability the platform polices, which is the one thing that is false about it.
//
// It lives INSIDE `PermissionList` rather than at each consent surface, which is what puts
// it on the install dialog and the Store detail panel by construction: both render
// `AppDisclosureView`, and a disclosure that has to be remembered per caller is exactly what
// failed here before.
//
// 🔑 IT IS NOT "ADVISORY ONLY", AND MUST NOT BORROW THAT PHRASE. Its two neighbours say
// advisory because a manifest DECLARATION is not enforced there. Here the packages really
// do install: the sentence is about something that will happen, not about a claim the
// platform declines to police.
//
// 🔑 A CORE-OWNED PIN IS A DIFFERENT STATEMENT, and the split comes from the installer's own
// authority (`app_manager._core_requirement_pins`, the set `_reject_core_dependency_conflicts`
// gates on), never a list kept here. `Pillow>=10,<13` means "the version you already have
// must satisfy this, or the install is refused" — nothing new enters the process. Grouping
// the two rather than qualifying every row keeps the loud half loud: a user scanning this
// should see which packages are actually new at a glance.
function PythonDepsRow({ deps }: { deps: AppPythonDependency[] }) {
  const fresh = deps.filter((d) => !d.coreOwned).map((d) => d.spec)
  const owned = deps.filter((d) => d.coreOwned).map((d) => d.spec)
  return (
    <div className="mt-s flex gap-s rounded-md border border-outline-variant bg-surface-high p-m">
      <PackagePlus size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-on-surface-low" />
      <div data-type="body-s" className="text-on-surface-low">
        <span className="text-on-surface">
          Python packages added to this gateway: {fresh.length ? specList(fresh) : 'none new'}
        </span>
        {fresh.length > 0 ? (
          <>
            {' — installing this app runs '}<code className="font-mono">pip install</code>
            {' into your PersonalClaw data folder ('}<code className="font-mono">app-python</code>
            {'), and the gateway loads those packages into its own process, after its own. '}
            They can add code but never replace a package PersonalClaw or another app already
            uses. Once loaded, that code is importable by PersonalClaw itself and by every other
            app you install, and none of the permissions above bound it.
          </>
        ) : ' — every package it declares is one PersonalClaw already ships.'}
        {owned.length > 0 && (
          <div className="mt-xs">
            It also pins {specList(owned)} — {owned.length === 1 ? 'a package' : 'packages'}{' '}
            PersonalClaw itself depends on. {owned.length === 1 ? 'That pin is' : 'Those pins are'}{' '}
            checked against the version this gateway already runs, and the install is refused
            rather than changing it, so a version you already have is what gets used.
          </div>
        )}
      </div>
    </div>
  )
}

/** The enforced-grant bullets for `perms`, worded as the consent surface shows them. ONE
 *  builder, so the install dialog's bullets and an update's "what changes" list cannot word
 *  one grant two ways. */
export function permissionRows(perms: AppSummary['permissions']): string[] {
  const rows: string[] = []
  if (perms.api?.length) rows.push(`API: ${perms.api.join(', ')}`)
  if (perms.events?.length) rows.push(`Events: ${perms.events.join(', ')}`)
  if (perms.mcpTools?.length) rows.push(`MCP tools: ${perms.mcpTools.join(', ')}`)
  // #3501. One grant, so one bullet — no tier to interpolate. This read `Memory: ${…}`
  // and rendered the declared tier verbatim, which meant a user could be shown (and
  // approve) `Memory: app-scoped` for a grant the gateway refused on every path.
  if (perms.memory) rows.push('Memory')
  if (perms.storage) rows.push('Storage')
  if (perms.cron) rows.push('Scheduled jobs')
  // An app's agent runs approve their own tool calls and hold the write grant
  // (`handlers/apps.api_app_agent_run`), so the bullet says so rather than letting "background
  // agents" read as agents that will ask you.
  if (perms.agent) rows.push('Run background agents that use any tool without asking you — they can change files and run commands')
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
  // APE-2. `eventSubscriptions` is an ENFORCED grant as of APE-2 and belongs in the
  // bullets: `apps/app_events.emit` is the only path a platform event reaches an app by,
  // and it consults `can_receive_platform_event` per app per event (deny by default, exact
  // name). It was in the pending block under APE-1, when no registry existed; leaving it
  // there now would understate a real capability — the D2 defect inverted, and just as
  // wrong, because the user would weigh a live grant as disclosure-only.
  const declaredEvents = perms.eventSubscriptions ?? []
  if (declaredEvents.length) {
    rows.push(`Receive platform events: ${declaredEvents.join(', ')}`)
  }
  // APE-3 shipped the host, so this JOINS the enforced bullets — the move APE-2 already made
  // for `eventSubscriptions`. `apps/permissions.can_run_background_tasks()` is now consulted
  // by `apps/worker_runtime`, which refuses to spawn or revive a worker without the grant, so
  // the declaration denies as well as declares. Leaving it in a "declared, not yet in effect"
  // box would now UNDERSTATE what the gateway does — the mirror image of the D2 defect that
  // kept it out of the bullets while no host existed.
  if (perms.backgroundTasks) rows.push('Run a long-lived background worker')
  // The settings `/api/config` reaches for this app — only these, named exactly
  // (`permissions.can_use_config_field`).
  const settings = perms.config ?? []
  if (settings.length) rows.push(`Read and change your settings: ${settings.join(', ')}`)
  return rows
}

export function PermissionList({ perms, hostUi, pythonDeps }: {
  perms: AppSummary['permissions']
  /** #492 — what browser code the app ships, from {@link consentHostUi}. */
  hostUi?: { page: boolean; components: boolean }
  /** The packages the install pip-installs for the gateway to load, from the disclosure's
   *  `pythonDependencies`. Omitted (an installed app's wire does not carry it) or `[]`
   *  renders NOTHING: an empty section would alarm without informing, and there is no claim
   *  in the silence — unlike `hostUi`, absence here cannot be read as a promise, because the
   *  surrounding surface already says whether the manifest was read at all. */
  pythonDeps?: AppPythonDependency[]
}) {
  const rows = permissionRows(perms)
  const messaging = perms.appMessaging ?? []
  const desktopCaps = perms.desktop ?? []
  return (
    <div>
      <div data-type="label-m" className="mb-xs text-on-surface">Permissions the gateway enforces</div>
      {rows.length === 0 ? <div data-type="body-s" className="text-on-surface-low">None — this app is granted no gateway capability.</div> : (
        <ul className="flex flex-col gap-xs">
          {rows.map((r, i) => <li key={i} data-type="body-s" className="text-on-surface-low">• {r}</li>)}
        </ul>
      )}
      {messaging.length === 0 && (
        <div data-type="body-s" className="mt-xs text-on-surface-low">
          App messaging: none — it declared no target, and the gateway broker is the only
          way one app can reach another, so it can message no other app.
        </div>
      )}
      {/* DC-2. Same reasoning as the messaging caption above: deny-by-default is the
          real behaviour, and staying silent about it would let absence read as
          "unrestricted" rather than "no native reach at all". */}
      {desktopCaps.length === 0 && (
        <div data-type="body-s" className="mt-xs text-on-surface-low">
          Desktop capabilities: none — it declared no native capability, and the gateway
          mediates every app→desktop call, so it can reach nothing native on this machine.
        </div>
      )}
      <div className="mt-s flex gap-s rounded-md border border-outline-variant bg-surface-high p-m">
        <Globe size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-on-surface-low" />
        <div data-type="body-s" className="text-on-surface-low">
          <span className="text-on-surface">Network access: {networkClaim(perms.network)}</span>
          {' — advisory only. PersonalClaw does not confine an app\'s outbound traffic: this app\'s '}
          code can reach the network either way. The declaration is disclosure, not containment.
        </div>
      </div>
      {hostUi && <HostPageRow hostUi={hostUi} />}
      {(pythonDeps ?? []).length > 0 && <PythonDepsRow deps={pythonDeps!} />}
    </div>
  )
}

// P29: the recurring jobs an app declares, shown pre-install. Each is an agent run on
// a schedule — we surface the cadence + which agent + the prompt so the user sees what
// will run unattended before granting the `cron` permission.
//
// 🔑 A CRONTAB LINE IS NOT A DISCLOSURE. This returned `cron_expr` verbatim, so the one
// screen whose job is saying what will run unattended said `23 * * * *`. The words come
// from the server (`catalog._humanized_cadence` → `schedule.format_schedule` →
// cron-descriptor), the SAME formatter the Schedule page reads, rather than a second
// hand-rolled parser here that would drift from it. The raw expression is not discarded —
// it stays on the row as its `title` (see `cadenceTitle`), so a reader who wants the
// exact expression still has it.
function fmtCadence(c: AppCronSummary): string {
  if (c.cron_expr) return c.cadence || c.cron_expr
  const s = c.every ?? 0
  if (!s) return 'on a schedule'
  if (s % 86400 === 0) { const d = s / 86400; return `every ${d === 1 ? 'day' : `${d} days`}` }
  if (s % 3600 === 0) { const h = s / 3600; return `every ${h === 1 ? 'hour' : `${h} hours`}` }
  if (s % 60 === 0) { const m = s / 60; return `every ${m === 1 ? 'minute' : `${m} minutes`}` }
  return `every ${s}s`
}

/** The exact crontab expression, as a hover title on the humanised cadence — kept so
 *  humanising loses nothing. `undefined` when the row already shows the expression itself
 *  (the server could not describe it), because a tooltip repeating the visible text is
 *  noise, and for the `every` form, which has no expression. */
function cadenceTitle(c: AppCronSummary): string | undefined {
  if (!c.cron_expr || !c.cadence) return undefined
  return `cron: ${c.cron_expr}`
}

/** The recurring jobs an app declares. Each is an AGENT run on a schedule — so the block
 *  shows the cadence, the agent and the prompt, and above all SAYS that installing turns
 *  them on: an app's crons become live, enabled triggers the moment the install commits, run
 *  unattended with no approval prompt, and until this sentence existed nothing on the consent
 *  screen said so. Whether a job is on comes from the server (`scheduled`, the trigger
 *  store's own predicate) — a job declared without the `cron` permission is inert, and it is
 *  disclosed as inert rather than as something that will run. */
export function CronConsentList({ crons, action = 'install' }: { crons: AppCronSummary[]; action?: DisclosureAction }) {
  const on = crons.filter((c) => c.scheduled !== false)
  const off = crons.length - on.length
  const jobs = (n: number) => (n === 1 ? 'a scheduled job' : `${n} scheduled jobs`)
  return (
    <div data-testid="consent-scheduled-jobs">
      <div data-type="label-m" className="mb-1 flex items-center gap-1.5 text-on-surface">
        <CalendarClock size={14} /> Scheduled jobs
      </div>
      <div data-type="body-s" className="mb-s text-on-surface-low">
        {on.length > 0 && (
          <>
            <span className="text-on-surface">
              {action === 'update' ? `After the update it runs ${jobs(on.length)}` : `Installing turns on ${jobs(on.length)}`}
            </span>
            {` — ${on.length === 1 ? 'it runs' : 'each runs'} an agent on its own, on the schedule below, without asking you first. You can pause ${on.length === 1 ? 'it' : 'them'} on the Triggers page.`}
          </>
        )}
        {off > 0 && (
          `${on.length > 0
            ? ` ${off === 1 ? 'One more is' : `${off} more are`}`
            : off === 1 ? 'This job is' : 'These jobs are'} declared but will not run: the app does not have the Scheduled jobs permission.`
        )}
      </div>
      <ul className="flex flex-col gap-1.5">
        {crons.map((c, i) => (
          <li key={c.name || i} className="rounded-md border border-outline-variant bg-surface-high p-m">
            <div className="flex items-center justify-between gap-s">
              <span data-type="body-s" className="text-on-surface">{c.name || 'job'}</span>
              <span data-type="label-s" className="shrink-0 text-on-surface-low" title={cadenceTitle(c)}>
                {c.scheduled === false ? `off · ${fmtCadence(c)}` : fmtCadence(c)}
              </span>
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

/** APE-8 "Fix with AI": shown when a failed install carried a build/hook log. Opens a chat
 *  pre-filled with the install log — already wrapped in the backend's untrusted-content
 *  fence (`fix_prompt` is built server-side; the FE only passes it through) — so the user or
 *  agent can debug the failure. Seeds the composer, never auto-sends. Renders nothing when
 *  there is no fix prompt. */
export function FixWithAiButton({ fixPrompt }: { fixPrompt: string | null }) {
  if (!fixPrompt) return null
  return (
    <Button variant="secondary" size="sm" onClick={() => launchChat({ prompt: fixPrompt })}>
      <Sparkles size={15} /> Fix with AI
    </Button>
  )
}

/** A consented install or update that failed past the gate — a pip refusal, a hook error,
 *  an "already installed" race — INSIDE the dialog, beside the button that caused it. The
 *  error and its fix-prompt are one unit and are declared once, here, for every install
 *  surface: rendered in the page behind a modal they were covered by its own backdrop, and
 *  "Install anyway" read as doing nothing (#3540). */
function InstallFailure({ error, fixPrompt }: { error: string; fixPrompt: string }) {
  return (
    <div className="flex items-center justify-between gap-m" data-testid="install-failure">
      <FieldError>{error}</FieldError>
      <FixWithAiButton fixPrompt={fixPrompt || null} />
    </div>
  )
}

/** What an install surface hands the one consent path. `source` is what the installer
 *  fetches (a registry item's `pointer`, else its `source`); `label` names it until the
 *  review reads the app's own display name; `update` is an INSTALLED app's name when the
 *  review is for updating it to `source`. */
export interface InstallTarget {
  source: string
  label: string
  update?: string
}

type Phase =
  | { k: 'reviewing' }
  | { k: 'unreadable'; error: string }
  | { k: 'review'; review: AppInstallResult; changed: boolean }
  | { k: 'installing'; review: AppInstallResult; changed: boolean }
  | { k: 'failed'; review: AppInstallResult; changed: boolean; error: string; fixPrompt: string }

/** The one install path: `begin(target)` opens the consent dialog, which reviews the source
 *  on the server BEFORE anything is installed, shows what the app gets, and installs only
 *  when the user confirms — sending back the review's `consent` digest, so what they saw is
 *  what lands. `dialog` is rendered once by the caller; `onInstalled` runs after a
 *  successful commit (a visible confirmation has already been shown by then). */
export function useAppInstall({ onInstalled }: { onInstalled: (result: AppInstallResult, target: InstallTarget) => void }) {
  const [open, setOpen] = useState<{ target: InstallTarget; phase: Phase } | null>(null)
  // Every begin/confirm/close bumps this, so an answer arriving after the user has moved on
  // (closed, or opened another app) never overwrites what they are looking at.
  const seq = useRef(0)
  const onInstalledRef = useRef(onInstalled)
  onInstalledRef.current = onInstalled

  const begin = useCallback(async (target: InstallTarget) => {
    const mine = ++seq.current
    setOpen({ target, phase: { k: 'reviewing' } })
    let phase: Phase
    try {
      phase = { k: 'review', review: await api.previewApp(target.source, target.update), changed: false }
    } catch (e) {
      phase = { k: 'unreadable', error: readableErrText(e) || `PersonalClaw could not read ${target.label}.` }
    }
    if (seq.current === mine) setOpen({ target, phase })
  }, [])

  const close = useCallback(() => {
    seq.current += 1
    setOpen(null)
  }, [])

  const confirm = useCallback(async () => {
    if (!open || (open.phase.k !== 'review' && open.phase.k !== 'failed')) return
    const { target } = open
    const { review, changed } = open.phase
    const mine = ++seq.current
    setOpen({ target, phase: { k: 'installing', review, changed } })
    const token = review.consent ?? ''
    const r = target.update
      ? await api.updateApp(target.update, target.source, token)
      : await api.installApp(target.source, token)
    const stillOpen = seq.current === mine
    // The app's own name: the server's, else the one the review read — never a pasted URL.
    const label = r.displayName || review.displayName || target.label
    if (r.ok) {
      // Confirmed whether or not the dialog is still up: the app IS installed, and closing a
      // dialog mid-install does not un-install it, so the user is told either way.
      if (stillOpen) setOpen(null)
      announce(r, target, label)
      onInstalledRef.current(r, target)
      return
    }
    if (!stillOpen) {
      if (!r.needs_consent) announceFailure(r, target, label)
      return
    }
    if (r.needs_consent && r.consent) {
      // The bytes changed after the review: show the new review; never install on the old yes.
      setOpen({ target, phase: { k: 'review', review: r, changed: true } })
      return
    }
    setOpen({
      target,
      phase: {
        k: 'failed', review, changed,
        error: sentence(r.error || `That ${target.update ? 'update' : 'install'} did not go through`),
        fixPrompt: r.fix_prompt || '',
      },
    })
  }, [open])

  const dialog = open
    ? <AppInstallDialog target={open.target} phase={open.phase} onConfirm={confirm} onClose={close} />
    : null
  return { begin, dialog, active: open?.target ?? null }
}

/** The visible confirmation of a successful install — a card that just vanished from the
 *  Store, followed by "No matching apps" on a search for it, is how the old flow ended. */
function announce(r: AppInstallResult, target: InstallTarget, label: string) {
  const done = target.update ? `Updated ${label}` : `Installed ${label}`
  window.dispatchEvent(new CustomEvent('ne:toast', {
    detail: {
      level: 'success',
      message: r.restart_required ? `${done} — restart the gateway for it to fully take effect.` : `${done}.`,
      href: `#/apps?view=library&open=${encodeURIComponent(r.name)}`,
      hrefLabel: 'Show in Library',
    },
  }))
}

function announceFailure(r: AppInstallResult, target: InstallTarget, label: string) {
  window.dispatchEvent(new CustomEvent('ne:toast', {
    detail: { level: 'error', message: `${target.update ? 'Updating' : 'Installing'} ${label} did not go through: ${sentence(r.error || 'unknown error')}` },
  }))
}

function AppInstallDialog({ target, phase, onConfirm, onClose }: {
  target: InstallTarget; phase: Phase; onConfirm: () => void; onClose: () => void
}) {
  const updating = Boolean(target.update)
  const verb = updating ? 'Update' : 'Install'
  const icon = updating ? <RefreshCw size={18} /> : <Download size={18} />
  const review = 'review' in phase ? phase.review : null
  // The app's own display name once the review has read it — never the slug, which is an
  // identifier ("Install ops"), not a name a person recognises.
  const label = review?.displayName || target.label
  const title = `${verb} ${label}`

  if (phase.k === 'reviewing') {
    return (
      <Modal title={title} icon={icon} onClose={onClose}>
        <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
          <div role="status" className="flex items-center gap-s text-on-surface-low" data-type="body-s">
            <Loader2 size={16} className="animate-spin" aria-hidden="true" />
            Checking {label} and what it gets. Nothing is installed yet.
          </div>
          <div className="flex justify-end pt-s"><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
        </div>
      </Modal>
    )
  }
  if (phase.k === 'unreadable') {
    return (
      <Modal title={title} icon={icon} onClose={onClose}>
        <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
          <FieldError>{sentence(phase.error)}</FieldError>
          <p data-type="body-s" className="text-on-surface-low">Nothing was installed.</p>
          <div className="flex justify-end pt-s"><Button variant="ghost" onClick={onClose}>Close</Button></div>
        </div>
      </Modal>
    )
  }

  const r = phase.review
  // P21: the app installs on the user's own machine, not this server — the one-liner, never a
  // consent button, and the scan beside it because the command runs outside the scanner.
  if (r.needs_client_install) {
    return (
      <Modal title={title} icon={<Terminal size={18} />} onClose={onClose}>
        <div className="flex flex-col gap-m p-l" style={{ minWidth: 460 }}>
          {/* The server's reason arrives without terminal punctuation (`app_manager` composes
              "'<name>' installs on your local machine, not this server"), and this line appends
              an instruction to it, so the boundary is supplied here. */}
          <p data-type="body-s" className="text-on-surface-low">
            {sentence(r.error || 'This app installs on your local machine, not this server.')} Run this in your terminal:
          </p>
          {r.client_install?.shell && <ClientInstallCommand label="Install command" cmd={r.client_install.shell} />}
          {r.client_install?.postInstall && <ClientInstallCommand label="Then" cmd={r.client_install.postInstall} />}
          <p data-type="label-s" className="text-on-surface-low">
            The command runs on your machine, outside PersonalClaw's security scanner — review it before running.
          </p>
          {r.scan && <ScanReport scan={r.scan} />}
          <div className="flex justify-end gap-s pt-s">
            <Button variant="ghost" onClick={onClose}>Done</Button>
          </div>
        </div>
      </Modal>
    )
  }

  // A terminal refusal (dangerous content or an invalid signature) explains itself and offers
  // no override; the grants still render, because they are why the findings matter.
  const refusal = terminalRefusalReason(r)
  const warned = r.scan?.verdict === 'warning'
  const version = r.version ? ` to v${r.version}` : ''
  const intro = refusal
    || (phase.changed
      ? `${label} changed after you opened this, so this is what it is now. Review it again before you ${verb.toLowerCase()}.`
      : updating
        ? (r.needs_consent
          ? `Updating ${label}${version} changes what it gets. Nothing changes until you choose Update.`
          : `Updating ${label}${version} does not change anything it gets.`)
        : warned
          ? `The security scanner raised warnings. Review them and what ${label} gets — install only if you trust the source.`
          : `This is everything ${label} gets. Nothing is installed until you choose Install.`)
  return (
    <Modal title={title} icon={refusal || warned ? <ShieldAlert size={18} /> : icon} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <p data-type="body-s" className={refusal ? 'text-danger' : 'text-on-surface-low'} role={phase.changed ? 'status' : undefined}>
          {intro}
        </p>
        {updating && r.previous && r.disclosure && (
          <DisclosureChanges previous={r.previous} current={r.disclosure} needed={r.needs_consent} />
        )}
        {r.disclosure && <AppDisclosureView disclosure={r.disclosure} action={updating ? 'update' : 'install'} />}
        {r.scan && <ScanReport scan={r.scan} />}
        {phase.k === 'failed' && <InstallFailure error={phase.error} fixPrompt={phase.fixPrompt} />}
        <div className="flex justify-end gap-s pt-s">
          {/* A terminal refusal leaves nothing to cancel — the server already said no — so its one
              button only dismisses, and says "Done" like this dialog's other dismiss-only footer. */}
          <Button variant="ghost" onClick={onClose}>{refusal ? 'Done' : 'Cancel'}</Button>
          {!refusal && (
            <Button variant="primary" loading={phase.k === 'installing'} onClick={onConfirm}>
              {warned ? <ShieldAlert size={16} /> : updating ? <RefreshCw size={16} /> : <Download size={16} />}
              {' '}{verb}{warned ? ' anyway' : ''}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  )
}

