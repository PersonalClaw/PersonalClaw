import {
  Palette, Plug, Bell, Cpu, Shield, ShieldAlert, User, MessageSquare, Bot, Inbox,
  FolderSync, ScrollText, Archive, AudioLines, DownloadCloud, FileText, ChevronRight, Search, Blocks, Activity, Scissors, Compass, Stethoscope, ThumbsUp,
  HardDriveDownload, Coins, Route, LayoutDashboard, Rss, Package, Smartphone, Smile, MonitorSmartphone, Plug2, FileType2,
  FlaskConical, KeyRound, MessageCircle, Workflow, RefreshCcw, Gauge,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions } from '../../ui/HeaderActions'
import { HeaderSearch } from '../../ui/HeaderSearch'
import { IconButton } from '../../ui/IconButton'
import { ArrowLeft } from 'lucide-react'
import { DesignPanel } from './DesignPanel'
import { AccountPanel } from './AccountPanel'
import { NotificationsPanel } from './NotificationsPanel'
import { SecurityPanel } from './SecurityPanel'
import { SecretsPanel } from './SecretsPanel'
import { ProvidersPanel } from './ProvidersPanel'
import { ModelsPanel } from './ModelsPanel'
import { SearchPanel } from './SearchPanel'
import { PromptsPanel } from './PromptsPanel'
import { AgentDefaultsPanel } from './AgentDefaultsPanel'
import { ChatPanel } from './ChatPanel'
import { InboxSettingsPanel } from './InboxSettingsPanel'
import { DocumentsPanel } from './DocumentsPanel'
import { AuditPanel } from './AuditPanel'
import { ArchivePanel } from './ArchivePanel'
import { PortabilityPanel } from './PortabilityPanel'
import { DurabilityPanel } from './DurabilityPanel'
import { VoicePanel } from './VoicePanel'
import { UpdatesPanel } from './UpdatesPanel'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { SystemPanel } from './SystemPanel'
import { FeedbackPanel } from './FeedbackPanel'
import { UsagePanel } from './UsagePanel'
import { RoutingPanel } from './RoutingPanel'
import { ProjectionRulesPanel } from './ProjectionRulesPanel'
import { LegibilityPanel } from './LegibilityPanel'
import { EvalsPanel } from './EvalsPanel'
import { AmbientPanel } from './AmbientPanel'
import { WorkflowsPanel } from './WorkflowsPanel'
import { LoopsPanel } from './LoopsPanel'
import { CompanionPanel } from './CompanionPanel'
import { BuddyPanel } from './BuddyPanel'
import { DevicesPanel } from './DevicesPanel'
import { SenderTrustPanel } from './SenderTrustPanel'
import { SourcesPanel } from './SourcesPanel'
import { PacksPanel } from './PacksPanel'
import { ExternalAccessPanel } from './ExternalAccessPanel'
import { GuardrailsPanel } from './GuardrailsPanel'
import { DoctorPanel } from './DoctorPanel'
import { AppsPanel } from './AppsPanel'
import { SettingsHome } from './SettingsHome'
import { SectionDepthProvider } from './settingsUI'
import { pageById, pageOfSection, sectionAddress, type SettingsPageDef } from './settingsPages'
import type { RouteProps } from '../../app/useQueryState'
import { fvs } from '../../design/fontWeight'
import { PageTitle } from '../../ui/PageTitle'
import { TextLink } from '../../ui/TextLink'
import { OPEN_DURING_SETUP } from '../../app/onboarding/openDuringSetup'

// A subpage panel's render context: `go` navigates between subpages, `navigate` is
// the raw router (for cross-section links), and `query`/`setQuery` let a panel put
// its OWN nested state (which tab, which provider is expanded) in the URL — so
// #/settings/voice?section=vocabulary and an expanded provider survive refresh + Back.
interface PanelCtx {
  go: (id: string) => void
  navigate?: RouteProps['navigate']
  query: RouteProps['query']
  setQuery: RouteProps['setQuery']
}

// Each section: id + label + icon + its panel renderer. A section lives on the page that holds it
// (`settingsPages.ts`, #5897): seventeen pages, each one card on the home grid, opening on
// #/settings/<page> with a Settings › <Label> breadcrumb header (no left-nav). A section that was a
// page of its own until then keeps its id, and its old address opens it on its page.
export interface SettingsSection { id: string; label: string; icon: LucideIcon; render: (ctx: PanelCtx) => React.ReactNode }

export const SECTIONS: SettingsSection[] = [
  { id: 'account', label: 'Account', icon: User, render: () => <AccountPanel /> },
  { id: 'design', label: 'Design', icon: Palette, render: () => <DesignPanel /> },
  { id: 'chat', label: 'Chat', icon: MessageSquare, render: () => <ChatPanel /> },
  { id: 'providers', label: 'Providers', icon: Plug, render: (c) => <ProvidersPanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'models', label: 'Models', icon: Cpu, render: () => <ModelsPanel /> },
  { id: 'search', label: 'Search', icon: Search, render: () => <SearchPanel /> },
  { id: 'prompts', label: 'Prompts', icon: FileText, render: () => <PromptsPanel /> },
  // Memory was here. It is Library's Memory tab now (#3815), and its old address a row of
  // `routeRedirects.ts`.
  // Grouped with Prompts/Models rather than under System: a study measures a prompt template
  // against another one, and its judge is a model — the things this page is about are the things
  // those two pages configure. Its RESULTS live on `#/learning`; this page is only the five knobs
  // that decide whether they can be produced at all.
  { id: 'evals', label: 'Evaluations', icon: FlaskConical, render: () => <EvalsPanel /> },
  { id: 'agent', label: 'Agent defaults', icon: Bot, render: () => <AgentDefaultsPanel /> },
  { id: 'voice', label: 'Speech & Transcription', icon: AudioLines, render: (c) => <VoicePanel go={c.go} query={c.query} /> },
  { id: 'apps', label: 'Apps', icon: Blocks, render: (c) => <AppsPanel navigate={c.navigate} /> },
  { id: 'inbox', label: 'Inbox', icon: Inbox, render: () => <InboxSettingsPanel /> },
  // Beside Apps/Inbox in Workspace, not in Chat: the flag it owns governs the ARTIFACT
  // surface (an office document opened from the library), not anything about a session.
  { id: 'documents', label: 'Documents', icon: FileType2, render: () => <DocumentsPanel /> },
  { id: 'notifications', label: 'Notifications', icon: Bell, render: () => <NotificationsPanel /> },
  { id: 'security', label: 'Security', icon: Shield, render: () => <SecurityPanel /> },
  // Beside Security, not inside it: Security is about ENFORCEMENT posture (what is denied, what is
  // redacted), and this is the inventory of credentials the instance holds. A user asking "what
  // tokens does this thing have" is asking a different question from "what does it block", and
  // burying a per-secret list inside the enforcement page would make the inventory unfindable.
  { id: 'secrets', label: 'Secrets', icon: KeyRound, render: () => <SecretsPanel /> },
  // Next to Security on purpose: "what is paired to my gateway, and can I cut it off" is a
  // security question. This is the ONE device registry — `companion` above is the LAN/PWA
  // transport settings, not a list of devices.
  { id: 'devices', label: 'Devices', icon: MonitorSmartphone, render: () => <DevicesPanel /> },
  // Beside Devices on purpose: both answer "who can reach me, and can I cut them off". Devices
  // is what holds a session to this gateway; this is who may talk to the agent through a
  // messaging channel. The allowlist was writable from two places and readable from none
  // before this panel — an access-control list you cannot enumerate is one you cannot audit.
  { id: 'sender-trust', label: 'Sender trust', icon: MessageCircle, render: (c) => <SenderTrustPanel navigate={c.navigate} /> },
  { id: 'guardrails', label: 'Guardrails', icon: ShieldAlert, render: () => <GuardrailsPanel /> },
  // Beside Guardrails on purpose: "what can reach in, and can I cut it off" is the same
  // question Guardrails asks about what can act.
  { id: 'external-access', label: 'External access', icon: Plug2, render: () => <ExternalAccessPanel /> },
  { id: 'audit', label: 'Audit log', icon: ScrollText, render: () => <AuditPanel /> },
  // The machine's own state, read live: the health strip and what is holding memory. It used to
  // sit on Home; beside Doctor and Diagnostics because all three answer "how is this machine".
  { id: 'system', label: 'System', icon: Gauge, render: (c) => <SystemPanel navigate={c.navigate} /> },
  { id: 'doctor', label: 'Doctor', icon: Stethoscope, render: () => <DoctorPanel /> },
  { id: 'diagnostics', label: 'Diagnostics', icon: Activity, render: () => <DiagnosticsPanel /> },
  { id: 'tool-output', label: 'Tool output', icon: Scissors, render: () => <ProjectionRulesPanel /> },
  { id: 'feedback', label: 'AI feedback', icon: ThumbsUp, render: () => <FeedbackPanel /> },
  { id: 'usage', label: 'Usage', icon: Coins, render: (c) => <UsagePanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'routing', label: 'Routing & Efficiency', icon: Route, render: (c) => <RoutingPanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'legibility', label: 'Legibility', icon: Compass, render: () => <LegibilityPanel /> },
  // Beside Ambient surfaces in Workspace: both are about what the workspace DOES on its own.
  // Their runs/definitions live on `#/workflows` and `#/loops`; these are the engine settings
  // those surfaces run under, and every field on both was PATCH-editable with no control
  // anywhere in `web/` before this (issues #2801, #752's siblings).
  { id: 'workflows', label: 'Workflows', icon: Workflow, render: () => <WorkflowsPanel /> },
  { id: 'loops', label: 'Autonomous loops', icon: RefreshCcw, render: () => <LoopsPanel /> },
  { id: 'ambient', label: 'Ambient surfaces', icon: LayoutDashboard, render: () => <AmbientPanel /> },
  { id: 'companion', label: 'Companion apps', icon: Smartphone, render: () => <CompanionPanel /> },
  // Every buddy setting in one place (#3901): its status, its surfaces, when it steps back.
  { id: 'buddy', label: 'Buddy', icon: Smile, render: () => <BuddyPanel /> },
  { id: 'sources', label: 'Watched sources', icon: Rss, render: () => <SourcesPanel /> },
  { id: 'packs', label: 'Packs', icon: Package, render: () => <PacksPanel /> },
  { id: 'archive', label: 'Archive', icon: Archive, render: () => <ArchivePanel /> },
  { id: 'portability', label: 'Import / Export', icon: FolderSync, render: () => <PortabilityPanel /> },
  { id: 'durability', label: 'Backups', icon: HardDriveDownload, render: () => <DurabilityPanel /> },
  { id: 'updates', label: 'Updates', icon: DownloadCloud, render: () => <UpdatesPanel /> },
]

/** A page's sections, in its order. While setup holds every other page, only the ones setup never
 *  holds (`OPEN_DURING_SETUP`), so a page that holds one of them opens without the rest. */
function sectionsOf(page: SettingsPageDef, duringSetup: boolean): SettingsSection[] {
  return page.sections
    .filter((id) => !duringSetup || OPEN_DURING_SETUP.includes(id))
    .map((id) => SECTIONS.find((s) => s.id === id)!)
}

/** The icon a page goes by: its own section's, or its first section's. */
function pageIcon(page: SettingsPageDef): LucideIcon {
  return (SECTIONS.find((s) => s.id === page.id) ?? SECTIONS.find((s) => s.id === page.sections[0])!).icon
}

/** Settings — a multi-page navigation. The landing page is a bento grid of
 *  per-page cards (SettingsHome) with one full-width search; clicking a card
 *  opens that page at #/settings/<id> with a Settings › <Label> breadcrumb. No
 *  left-nav: the home grid IS the navigation. A page's own nested state (tabs,
 *  expanded provider) rides `?query` so it's deep-linkable + refresh/Back-safe, and
 *  `?section=` opens a page that holds several sections at one of them. */
export function SettingsPage({ sub, navigate, query, setQuery, duringSetup = false }: RouteProps & {
  /** Opened while setup holds every other page (a first run not yet finished or skipped, or a
   *  re-run in progress), which the shell does only for the pages setup never holds
   *  (`app/onboarding/openDuringSetup`). The Settings home waits for setup then, so the way back
   *  leads to setup, and the page names the others setup never holds: the shell draws no rail, and
   *  this is how one of them reaches the next. */
  duringSetup?: boolean
}) {
  // Settings home's search, in its header; the home's cards read it.
  const [homeQuery, setHomeQuery] = useState('')
  // `go` takes a page id or a section id: a section opens on the page that holds it.
  const go = (id: string) => navigate?.(id ? sectionAddress(id) : 'settings')
  const backToSetup = () => navigate?.('onboarding')
  // A page by its id. A section's own id opens the page that holds it, at that section: the router
  // turns a retired address into `?section=` before any page draws, but a caller that renders this
  // page directly hands it the id as it was.
  const page = sub ? (pageById(sub) ?? pageOfSection(sub)) : undefined
  const focus = page && sub !== page.id ? sub : query?.section

  // Home (no valid sub, or bare #/settings): the bento grid, its search in the header.
  if (!page) {
    return (
      <div className="flex h-full flex-col">
        <TopBar left={<PageTitle>Settings</PageTitle>}
          right={<HeaderActions>
            {/* `tour="settings"`: the product tour's last stop. On the SEARCH, not on the page: the
                bento is as tall and wide as the viewport, so ringing the whole thing dims nothing
                and the card has nowhere to sit that is not on top of it. The search is also what
                the stop's copy is about. */}
            <HeaderSearch value={homeQuery} onChange={setHomeQuery} autoFocus tour="settings"
              placeholder="Search settings — name, description, or any value inside" label="Search settings" />
          </HeaderActions>} />
        <SettingsHome go={go} query={homeQuery} />
      </div>
    )
  }

  const Icon = pageIcon(page)
  const sections = sectionsOf(page, duringSetup)
  const ctx: PanelCtx = { go, navigate, query, setQuery }

  // A page: breadcrumb header + its sections, spanning the pane.
  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={
          <div className="flex items-center gap-1 min-w-0">
            {duringSetup
              ? <IconButton icon={ArrowLeft} label="Back to setup" size={36} onClick={backToSetup} />
              : <IconButton icon={ArrowLeft} label="Back to Settings" size={36} onClick={() => go('')} />}
            {/* The "Settings" crumb ("Setup" while setup holds the other pages) and its chevron drop
                below `sm`. They are the one duplicate in this row — the arrow to their left already
                goes to the same place and is already NAMED for it ("Back to Settings") — and at 390px
                they were eating the width the section name needs: the truncating label measured 20px
                before the arrow stopped being squeezed and 4px after. Dropping them returns that width
                to the thing that says where you are. Desktop is untouched. */}
            <span className="hidden shrink-0 items-center gap-1 sm:inline-flex">
              <button type="button" onClick={duringSetup ? backToSetup : () => go('')}
                className="text-on-surface-low text-[1.0625rem] transition-colors hover:text-on-surface" style={fvs(470)}>{duringSetup ? 'Setup' : 'Settings'}</button>
              <ChevronRight size={16} className="shrink-0 text-on-surface-low/60" />
            </span>
            <span className="flex items-center gap-1.5 min-w-0 text-on-surface text-[1.0625rem]" style={fvs(470)}>
              <Icon size={16} className="shrink-0 text-on-surface-low" />
              <span className="truncate">{page.label}</span>
            </span>
          </div>
        }
      />
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto px-2xl py-2xl">
          {duringSetup && <SecurityControlsNav current={page.id} />}
          {sections.length === 1
            ? sections[0].render(ctx)
            : <FoldedPage page={page} sections={sections} focus={focus} ctx={ctx} listed={!duringSetup} />}
        </div>
      </div>
    </div>
  )
}

/** The room left above a section's heading when a link scrolls to it, in px. */
const SECTION_TOP_GAP = 24

/** A page holding several sections: its title, a list of them, then each under its own heading.
 *  The page draws the section headings (each panel's own title gives way to it, and its headings
 *  move a level down: `SectionDepthProvider`), so the outline is h1 page, h2 section, h3 within.
 *  `focus` is the section `?section=` names: scrolled to, and its heading focused, so a link to it
 *  lands the keyboard where the eye goes. */
function FoldedPage({ page, sections, focus, ctx, listed }: {
  page: SettingsPageDef; sections: SettingsSection[]; focus?: string; ctx: PanelCtx
  /** Whether to list the sections at the top. Not while setup holds the other pages: the Security
   *  controls list above is the page's one navigation then, and names these sections already. */
  listed: boolean
}) {
  const headings = useRef(new Map<string, HTMLHeadingElement>())
  useEffect(() => {
    const heading = focus ? headings.current.get(focus) : undefined
    if (!heading) return
    // The page's own scroll box, and only it: `scrollIntoView` scrolls every scrollable ancestor,
    // and moved the shell itself up under the window, top bar and all.
    const box = heading.closest('.overflow-y-auto')
    if (box) box.scrollTop += heading.getBoundingClientRect().top - box.getBoundingClientRect().top - SECTION_TOP_GAP
    heading.focus({ preventScroll: true })
  }, [focus, page.id])
  return (
    <>
      <h1 className={`${listed ? 'mb-s' : 'mb-2xl'} text-on-surface`} data-type="title-l">{page.label}</h1>
      {listed && (
        <nav aria-label="On this page" className="mb-2xl">
          <ul className="flex flex-wrap gap-x-l gap-y-xs">
            {sections.map((s) => (
              <li key={s.id}>
                <TextLink href={`#/settings/${page.id}?section=${s.id}`} size="sm" ink="emphasis">{s.label}</TextLink>
              </li>
            ))}
          </ul>
        </nav>
      )}
      <SectionDepthProvider value={1}>
        {sections.map((s) => (
          <section key={s.id} aria-labelledby={`section-${s.id}`} className="mb-3xl">
            {/* Focusable only from code (`tabIndex={-1}`), never a tab stop: focus lands here to put a
                screen reader and the next Tab at the section, and the global ring marks it for a
                keyboard user who followed a link here. */}
            <h2 id={`section-${s.id}`} tabIndex={-1} ref={(el) => { if (el) headings.current.set(s.id, el); else headings.current.delete(s.id) }}
              data-type="title-l" className="mb-xs rounded-sm text-on-surface">{s.label}</h2>
            {s.render(ctx)}
          </section>
        ))}
      </SectionDepthProvider>
    </>
  )
}

/** The sections setup never holds, named on each page that holds one while setup holds the rest. In
 *  Settings' own order and under Settings' own labels, so they read as the places they are; the page
 *  open is named rather than linked, and marked as the one open. */
function SecurityControlsNav({ current }: { current: string }) {
  return (
    <nav aria-label="Security controls" className="mb-l">
      <p data-type="body-s" className="text-on-surface-low">
        Setup isn&rsquo;t finished. These pages never wait for it:
      </p>
      <ul className="mt-xs flex flex-wrap gap-x-l gap-y-xs">
        {SECTIONS.filter((s) => OPEN_DURING_SETUP.includes(s.id)).map((s) => (
          <li key={s.id}>
            {pageOfSection(s.id)?.id === current
              ? <span data-type="body-s" aria-current="page" className="text-on-surface">{s.label}</span>
              : <TextLink href={`#/${sectionAddress(s.id)}`} size="sm" ink="emphasis">{s.label}</TextLink>}
          </li>
        ))}
      </ul>
    </nav>
  )
}
