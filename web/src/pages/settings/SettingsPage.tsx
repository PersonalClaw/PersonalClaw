import { useEffect } from 'react'
import {
  Palette, Plug, Bell, Cpu, Shield, Database, User, MessageSquare, Bot, Inbox,
  FolderSync, ScrollText, Archive, AudioLines, DownloadCloud, FileText, ChevronRight, Search, Blocks, Activity, Scissors,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { ArrowLeft } from 'lucide-react'
import { DesignPanel } from './DesignPanel'
import { AccountPanel } from './AccountPanel'
import { NotificationsPanel } from './NotificationsPanel'
import { MemoryPanel } from './MemoryPanel'
import { SecurityPanel } from './SecurityPanel'
import { ProvidersPanel } from './ProvidersPanel'
import { ModelsPanel } from './ModelsPanel'
import { SearchPanel } from './SearchPanel'
import { PromptsPanel } from './PromptsPanel'
import { AgentDefaultsPanel } from './AgentDefaultsPanel'
import { ChatPanel } from './ChatPanel'
import { InboxSettingsPanel } from './InboxSettingsPanel'
import { AuditPanel } from './AuditPanel'
import { ArchivePanel } from './ArchivePanel'
import { PortabilityPanel } from './PortabilityPanel'
import { VoicePanel } from './VoicePanel'
import { UpdatesPanel } from './UpdatesPanel'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { ProjectionRulesPanel } from './ProjectionRulesPanel'
import { AppsPanel } from './AppsPanel'
import { SettingsHome } from './SettingsHome'
import type { RouteProps } from '../../app/useQueryState'

// A subpage panel's render context: `go` navigates between subpages, `navigate` is
// the raw router (for cross-section links), and `query`/`setQuery` let a panel put
// its OWN nested state (which tab, which provider is expanded) in the URL — so
// #/settings/memory?tab=audit and an expanded provider survive refresh + Back.
interface PanelCtx {
  go: (id: string) => void
  navigate?: RouteProps['navigate']
  query: RouteProps['query']
  setQuery: RouteProps['setQuery']
}

// Each subpage: id + label + icon (for the breadcrumb) + its panel renderer. The
// home page (bento grid of widgets) is the default; a subpage opens on
// #/settings/<id> with a Settings › <Label> breadcrumb header (no left-nav).
interface SubPage { id: string; label: string; icon: LucideIcon; render: (ctx: PanelCtx) => React.ReactNode }

const SUBPAGES: SubPage[] = [
  { id: 'account', label: 'Account', icon: User, render: () => <AccountPanel /> },
  { id: 'design', label: 'Design', icon: Palette, render: () => <DesignPanel /> },
  { id: 'chat', label: 'Chat', icon: MessageSquare, render: () => <ChatPanel /> },
  { id: 'providers', label: 'Providers', icon: Plug, render: (c) => <ProvidersPanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'models', label: 'Models', icon: Cpu, render: () => <ModelsPanel /> },
  { id: 'search', label: 'Search', icon: Search, render: () => <SearchPanel /> },
  { id: 'prompts', label: 'Prompts', icon: FileText, render: () => <PromptsPanel /> },
  { id: 'memory', label: 'Memory', icon: Database, render: (c) => <MemoryPanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'agent', label: 'Agent defaults', icon: Bot, render: () => <AgentDefaultsPanel /> },
  { id: 'voice', label: 'Speech & Transcription', icon: AudioLines, render: (c) => <VoicePanel go={c.go} query={c.query} /> },
  { id: 'apps', label: 'Apps', icon: Blocks, render: (c) => <AppsPanel navigate={c.navigate} /> },
  { id: 'inbox', label: 'Inbox', icon: Inbox, render: () => <InboxSettingsPanel /> },
  { id: 'notifications', label: 'Notifications', icon: Bell, render: () => <NotificationsPanel /> },
  { id: 'security', label: 'Security', icon: Shield, render: () => <SecurityPanel /> },
  { id: 'audit', label: 'Audit log', icon: ScrollText, render: () => <AuditPanel /> },
  { id: 'diagnostics', label: 'Diagnostics', icon: Activity, render: () => <DiagnosticsPanel /> },
  { id: 'tool-output', label: 'Tool output', icon: Scissors, render: () => <ProjectionRulesPanel /> },
  { id: 'archive', label: 'Archive', icon: Archive, render: () => <ArchivePanel /> },
  { id: 'portability', label: 'Import / Export', icon: FolderSync, render: () => <PortabilityPanel /> },
  { id: 'updates', label: 'Updates', icon: DownloadCloud, render: () => <UpdatesPanel /> },
]

/** Settings — a multi-page navigation. The landing page is a bento grid of
 *  per-subpage widgets (SettingsHome) with one full-width search; clicking a card
 *  opens that subpage at #/settings/<id> with a Settings › <Label> breadcrumb. No
 *  left-nav: the home grid IS the navigation. A subpage's own nested state (tabs,
 *  expanded provider) rides `?query` so it's deep-linkable + refresh/Back-safe. */
export function SettingsPage({ sub, navigate, query, setQuery }: RouteProps) {
  const go = (id: string) => navigate?.(id ? `settings/${id}` : 'settings')
  const current = sub ? SUBPAGES.find((s) => s.id === sub) : undefined

  // Legacy deep-link: Vocabulary merged into Speech & Transcription (the `voice`
  // subpage). #/settings/vocabulary → #/settings/voice?section=vocabulary, which
  // scrolls to the merged Vocabulary & corrections section. Replace (not push) so
  // Back doesn't bounce through the dead route.
  const legacyVocabulary = sub === 'vocabulary'
  useEffect(() => {
    if (legacyVocabulary) navigate?.('settings/voice?section=vocabulary', { replace: true })
  }, [legacyVocabulary, navigate])
  if (legacyVocabulary) return null

  // Home (no valid sub, or bare #/settings): the bento grid.
  if (!current) {
    return (
      <div className="flex h-full flex-col">
        <TopBar left={<span data-type="title-l" className="text-on-surface">Settings</span>} />
        <SettingsHome go={go} />
      </div>
    )
  }

  // A subpage: breadcrumb header + the panel, centered to the content width.
  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={
          <div className="flex items-center gap-1 min-w-0">
            <IconButton icon={ArrowLeft} label="Back to Settings" size={36} onClick={() => go('')} />
            <button type="button" onClick={() => go('')}
              className="text-on-surface-low text-[1.0625rem] transition-colors hover:text-on-surface" style={{ fontVariationSettings: '"wght" 470' }}>Settings</button>
            <ChevronRight size={16} className="shrink-0 text-on-surface-low/60" />
            <span className="flex items-center gap-1.5 min-w-0 text-on-surface text-[1.0625rem]" style={{ fontVariationSettings: '"wght" 470' }}>
              <current.icon size={16} className="shrink-0 text-on-surface-low" />
              <span className="truncate">{current.label}</span>
            </span>
          </div>
        }
      />
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto px-2xl py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {current.render({ go, navigate, query, setQuery })}
        </div>
      </div>
    </div>
  )
}
