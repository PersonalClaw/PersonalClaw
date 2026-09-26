import { useMemo, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { accentChip } from '../../design/accent'
import { motion } from 'framer-motion'
import {
  Blocks, Plus, Download, Power, Trash2, Settings2, FolderOpen,
  ShieldCheck, Server, LayoutGrid, RefreshCw, Plug, ChevronDown,
  MoreVertical, Database, Archive, HardDrive, MapPin, AlertTriangle,
  Boxes, Package, Store, KeyRound, RotateCw,
} from 'lucide-react'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { spring, expr } from '../../design/motion'
import { Popover, MenuRow } from '../../ui/Popover'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { ListControls } from '../../ui/ListControls'
import { FilterMenu, type FilterSectionDef, type FilterOption } from '../../ui/FilterMenu'
import { Modal } from '../../ui/Modal'
import { SidePanel } from '../../ui/SidePanel'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Markdown } from '../../ui/Markdown'
import { MoreRow } from '../../ui/MoreRow'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { TextInput, FieldError } from '../../ui/forms'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Segmented } from '../../ui/Segmented'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { useIsMobile } from '../../app/useIsMobile'
import { useQuery, invalidateKeys, writeQuery } from '../../lib/data'
import { useChatSocket } from '../../lib/useChatSocket'
import {
  api, type AppSummary, type AppDepClassification, type AppCatalogEntry, type AppCatalog,
} from '../../lib/api'
import { catalogApps } from '../../lib/appCatalog'
import { readableErrText } from '../../lib/errText'
import { reportingWrite } from '../../app/reportingWrite'
import { provenance, registryListing } from '../../lib/provenance'
import { dayStamp } from '../../lib/epoch'
import { AppIcon } from './appIcon'
import { QualityBadges } from './qualityBadges'
import { StoreSideRail, type RailOption } from './StoreSideRail'
import { artGradient } from './appArt'
import { AppConfigFields, useAppConfig } from './appConfigForm'
import { isInNav, setInNav } from './navApps'
import { PageTitle } from '../../ui/PageTitle'
// The ONE install-consent path, shared with the first-run essential-apps step: every
// install and update below opens its dialog through `useAppInstall`.
import { useAppInstall, AppDisclosureView, disclosureOf, PermissionList, consentHostUi } from './installConsent'
import { BUSY_REASON } from '../../ui/unavailable'

// ── Store item: the Store lists EVERY app it knows about — the available-to-
// install catalog entries UNION the already-installed apps — so it never reads
// as "empty" just because everything is installed. Both shapes normalize to this
// one row (catalog fields + an `installed`/`enabled`/`hasUI` overlay). Installed
// items render an Installed/Open affordance; available ones render Install. */
export interface StoreItem extends AppCatalogEntry {
  installed: boolean
  enabled: boolean
  hasUI: boolean
  /** A native app — always-on, locked (configure-only if it has settings, else
   *  managed elsewhere; never uninstall/disable). The SINGLE app-category flag;
   *  first-party/third-party apps have native=false. */
  native?: boolean
  /** Has a settings surface — drives whether a native app shows "Configure". */
  hasConfig?: boolean
  /** A multi-instance provider: no app-level settings — its instances are managed in
   *  Settings → Providers, which is where its "Manage instances" action goes. */
  configuredPerInstance?: boolean
  /** Provenance for the source divider: builtin/registry origin → "Built-in";
   *  a git URL or a local path → that source. */
  origin?: string
  /** APE-7: the app's source offers a newer version — card shows an "Update" badge. */
  updateAvailable?: boolean
  /** APE-7: that newer version (for the badge tooltip). */
  latestVersion?: string
  /** Where that version was found — the Update dialog starts from it. */
  latestSource?: string
}

/** An installed app (AppSummary) projected onto the catalog-entry shape so it can
 *  sit beside available entries in one list, carrying its origin/source for the
 *  source-divider grouping. */
function installedToStoreItem(a: AppSummary): StoreItem {
  return {
    name: a.name, displayName: a.displayName, description: a.description, version: a.version,
    icon: a.icon, heroUrl: a.heroUrl, author: '', source: a.source ?? '', sourceKind: 'bundled',
    isProvider: a.isProvider, providerType: a.providerType, tags: a.tags ?? [],
    installed: true, enabled: a.enabled, hasUI: a.hasUI,
    native: !!a.native, hasConfig: a.hasConfig, configuredPerInstance: !!a.configuredPerInstance, origin: a.origin,
    updateAvailable: !!a.updateAvailable, latestVersion: a.latestVersion, latestSource: a.latestSource,
    // APE-4: carried, not defaulted. Coercing an installed app's absent block to `{}`
    // here would be harmless today but would make the Library the one surface that
    // cannot tell "declared nothing" from "declared all-false".
    quality: a.quality,
  }
}

const GIT_URL_RE = /^(https?:\/\/|git@|git:\/\/|ssh:\/\/)/

/** A human heading for a local filesystem source. The Store groups apps by where
 *  they came from, but a raw absolute path is a dev/console artifact with no place
 *  in product chrome (tenet 1: companion, not console) — running the app from a git
 *  worktree surfaced "/Users/…/worktrees/ux-inspect/src/…" as a Store section title
 *  (WT-10). Show the folder name the user recognises instead
 *  ("/Users/me/projects/cool-app" → "cool-app"); the full path stays the grouping
 *  key, so filtering/URL state is unchanged. Falls back to the trimmed path only
 *  when there is no segment to show (e.g. a bare "/"). Handles both separators. */
export function localSourceLabel(path: string): string {
  const trimmed = path.replace(/[/\\]+$/, '')
  const base = trimmed.split(/[/\\]/).pop() ?? ''
  return base || path
}

/** The source a StoreItem belongs to, for the divider grouping. Returns a stable
 *  `key` (grouping/sort) + a human `label` (divider heading).
 *   • Built-in (bundled/registry origin, a bundled catalog entry, or a platform
 *     provider) → one "Built-in" group.
 *   • A git-URL source → grouped by that URL.
 *   • A local path → folded UP to the registered local source it lives under
 *     (an installed app records its own subdir, but the source the user ADDED is
 *     the parent dir), so every app from one source shares one divider. If it
 *     matches no registered source, it folds to its parent directory. The heading
 *     is the folder name (`localSourceLabel`), never the raw path.
 *   • A git clone whose URL was lost (legacy temp-clone path) → "Installed from git".
 *  `localSources` is the list of registered local source roots (catalog). */
export function sourceGroup(it: StoreItem, localSources: string[]): { key: string; label: string } {
  const src = (it.source || '').trim()
  const isBuiltin = it.native || it.origin === 'builtin' || it.origin === 'registry'
    || (it.sourceKind === 'bundled' && !it.installed)
  if (isBuiltin || !src || src === 'builtin' || src.startsWith('registry:')) {
    return { key: 'builtin', label: 'Built-in' }
  }
  if (GIT_URL_RE.test(src) || src.endsWith('.git')) {
    // Strip the #subdirectory fragment so all apps from the same repo group
    // under one section (e.g. url#app1, url#app2 → both key on url).
    const base = src.replace(/#.*$/, '')
    return { key: `git:${base}`, label: base }
  }
  // A git clone whose original URL wasn't recorded (resolved to a throwaway temp
  // dir) — can't attribute it to a URL, so bucket all such under one heading.
  if (it.origin === 'external') return { key: 'git:external', label: 'Installed from git' }
  // A filesystem path. Fold up to the registered source that contains it…
  const root = localSources.find((s) => src === s || src.startsWith(s.replace(/\/$/, '') + '/'))
  const key = root ?? (src.replace(/\/[^/]+\/?$/, '') || src)  // …else the parent dir
  return { key: `local:${key}`, label: localSourceLabel(key) }
}

/** Group items by source, ordered: Built-in first, then the rest alphabetically
 *  by label. Item order within a group is preserved (already sorted by the caller). */
function groupBySource(items: StoreItem[], localSources: string[] = []): { key: string; label: string; items: StoreItem[] }[] {
  const groups = new Map<string, { key: string; label: string; items: StoreItem[] }>()
  for (const it of items) {
    const g = sourceGroup(it, localSources)
    let bucket = groups.get(g.key)
    if (!bucket) { bucket = { key: g.key, label: g.label, items: [] }; groups.set(g.key, bucket) }
    bucket.items.push(it)
  }
  return [...groups.values()].sort((a, b) =>
    a.key === 'builtin' ? -1 : b.key === 'builtin' ? 1 : a.label.localeCompare(b.label))
}

/** A source-category heading rendered above each group's card grid. */
function SourceDivider({ label, count }: { label: string; count: number }) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">{label}</span>
      <span className="text-on-surface-low text-[0.75rem] tabular-nums">{count}</span>
      <span className="ml-1 h-px flex-1 bg-outline-variant/30" />
    </div>
  )
}

// ── Shared app actions ──────────────────────────────────────────────────────
// The card + the detail panel both DISPATCH the same real actions instead of the
// card silently opening the sidebar. Enable/disable runs inline; configure /
// update / uninstall / force-uninstall open their modals; open navigates to the
// app's page.
//
// 'uninstall' is the middle removal rung (issue #2541): the app's files go, the
// user's `data/` is kept. It sits between 'toggle' (nothing leaves disk) and
// 'force-uninstall' (everything goes, data included), and it is the control the
// force-uninstall dialog has always told users to reach for.
type AppActionKind = 'open' | 'toggle' | 'configure' | 'update' | 'uninstall' | 'force-uninstall'
// Carries the DISPLAY NAME as well as the slug: the slug is the API's identifier, the display
// name is the only one a person recognises, and a dialog title is a sentence for the person.
type DispatchAppAction = (app: {
  name: string; displayName: string; enabled: boolean; hasUI: boolean; configuredPerInstance?: boolean
  updateAvailable?: boolean; latestVersion?: string; latestSource?: string
}, action: AppActionKind) => void

/** Owns the app-action modal state + the enable/disable call, and renders the
 *  modals ONCE at the host level. Returns a `dispatch` both the cards and the
 *  detail panel call, the `busyName` (app mid-toggle), and the `modals` node. */
function useAppActions(nav: (p: string) => void, reload: () => void) {
  const [busyName, setBusyName] = useState<string | null>(null)
  const [configFor, setConfigFor] = useState<{ name: string; displayName: string } | null>(null)
  // Each carries the display name beside the slug: the slug is the API's identifier, and a
  // dialog title is a sentence for a person ("Update research-lab" named nobody's app).
  type Named = { name: string; displayName: string }
  const [updateFor, setUpdateFor] = useState<(Named & { found?: FoundUpdate }) | null>(null)
  const [uninstallFor, setUninstallFor] = useState<Named | null>(null)
  const [removeFor, setRemoveFor] = useState<Named | null>(null)

  const dispatch: DispatchAppAction = (app, action) => {
    switch (action) {
      case 'open': nav(`app/${encodeURIComponent(app.name)}`); return
      // An app configured per instance has no app-level settings to open: its instances are
      // managed in Settings → Providers, and its Configure goes there instead.
      case 'configure':
        if (app.configuredPerInstance) { nav('settings/providers'); return }
        setConfigFor({ name: app.name, displayName: app.displayName }); return
      case 'update': setUpdateFor({ name: app.name, displayName: app.displayName, found: foundUpdate(app) }); return
      case 'uninstall': setRemoveFor({ name: app.name, displayName: app.displayName }); return
      case 'force-uninstall': setUninstallFor({ name: app.name, displayName: app.displayName }); return
      case 'toggle': {
        // The CARD/menu twin of `AppDetailPanel.toggle()` below, and it had the same defect in a
        // different syntactic dress: `p.then(reload).finally(clear)` attaches no rejection handler
        // at all, so a refused activate/deactivate was an unhandled rejection — the row stopped
        // spinning with its old label and nothing said why. Reported and gated, same sentence, so
        // the two routes to one action cannot answer differently.
        setBusyName(app.name)
        const verb = app.enabled ? 'deactivate' : 'activate'
        const run = () => (app.enabled ? api.disableApp(app.name) : api.enableApp(app.name))
        void reportingWrite(`${verb} ${app.name}`, run)
          .then((ok) => { if (ok) reload() })
          .finally(() => setBusyName(null))
        return
      }
    }
  }

  const modals = (
    <>
      {updateFor && <UpdateModal name={updateFor.name} displayName={updateFor.displayName} found={updateFor.found}
        onClose={() => setUpdateFor(null)} onUpdated={() => { setUpdateFor(null); reload() }} />}
      {configFor && <ConfigModal name={configFor.name} displayName={configFor.displayName} onClose={() => setConfigFor(null)} />}
      {removeFor && <RemoveAppModal name={removeFor.name} displayName={removeFor.displayName} onClose={() => setRemoveFor(null)}
        onDone={() => { setRemoveFor(null); reload() }} />}
      {uninstallFor && <UninstallModal name={uninstallFor.name} displayName={uninstallFor.displayName} onClose={() => setUninstallFor(null)}
        onDone={() => { setUninstallFor(null); reload() }} />}
    </>
  )
  return { dispatch, busyName, modals }
}

/** The per-app "⋯" action menu (kebab) — the REAL actions, shared by cards + the
 *  detail panel. Enable/disable, configure, update, open, force-uninstall; a
 *  platform provider shows only "Open page" (it has no install lifecycle). */
function AppActionMenu({ item, onAction }: { item: StoreItem; onAction: DispatchAppAction }) {
  const app = { name: item.name, displayName: item.displayName, enabled: item.enabled, hasUI: item.hasUI, configuredPerInstance: item.configuredPerInstance }
  return (
    <Popover align="right" placement="bottom" width={200}
      // 🔴 PORTAL, or the card cuts this menu off. Measured on `#/apps` at 1440×900: the flyout is
      // 175px tall inside a card whose own `overflow-hidden` box ends 56px earlier, so the LAST row
      // — "Force uninstall", the destructive one — was clipped away on every card, and the strip it
      // occupied belongs to the card underneath (which is itself clickable). At 430px two of the
      // seven menus were clipped by 166px: invisible entirely.
      portal
      trigger={(open, toggle) => (
        <button type="button" aria-label={`Actions for ${item.displayName}`} title="Actions"
          aria-expanded={open} onClick={(e) => { e.stopPropagation(); toggle() }}
          className={`grid size-8 shrink-0 place-items-center rounded-pill transition-colors ${open ? 'bg-surface-high text-on-surface' : 'text-on-surface-low hover:bg-surface-high hover:text-on-surface'}`}>
          <MoreVertical size={16} />
        </button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5" onClick={(e) => e.stopPropagation()}>
          {item.hasUI && item.enabled && (
            <MenuRow icon={<LayoutGrid size={15} />} label="Open page" onClick={() => { onAction(app, 'open'); close() }} />
          )}
          {item.native ? (
            // Native app — always-on, locked: no uninstall/disable. "Configure"
            // only when it has a settings surface (hasConfig); a config-less provider
            // (filesystem/tools) is managed from the Tools page instead.
            <>
              {item.configuredPerInstance
                ? <MenuRow icon={<Settings2 size={15} />} label="Manage instances" onClick={() => { onAction(app, 'configure'); close() }} />
                : item.hasConfig
                  ? <MenuRow icon={<Settings2 size={15} />} label="Configure" onClick={() => { onAction(app, 'configure'); close() }} />
                  : <div className="px-m py-2 text-on-surface-low text-[0.75rem]">Always on — manage its tools from the Tools page.</div>}
              <MenuRow icon={<RefreshCw size={15} />} label="Update…" onClick={() => { onAction(app, 'update'); close() }} />
              <div className="px-m py-1.5 text-on-surface-low text-[0.75rem]">Native app — always on, can't be deactivated.</div>
            </>
          ) : (
            <>
              {item.enabled && <MenuRow icon={<Settings2 size={15} />} label={item.configuredPerInstance ? 'Manage instances' : 'Configure'} onClick={() => { onAction(app, 'configure'); close() }} />}
              <MenuRow icon={<RefreshCw size={15} />} label="Update…" onClick={() => { onAction(app, 'update'); close() }} />
              <MenuRow icon={<Power size={15} />} label={item.enabled ? 'Deactivate' : 'Activate'} onClick={() => { onAction(app, 'toggle'); close() }} />
              <div className="my-1 border-t border-outline-variant/30" />
              {/* The safe removal rung sits ABOVE the destructive one and outside the
                  danger styling. A menu that offered only "Force uninstall" made the
                  data-destroying path the ONLY way to get rid of an app — the inverse
                  of "make dangerous actions harder to reach than safe ones". */}
              <MenuRow icon={<Archive size={15} />} label="Uninstall…" onClick={() => { onAction(app, 'uninstall'); close() }} />
              <div className="[&_button]:text-danger">
                <MenuRow icon={<Trash2 size={15} />} label="Force uninstall…" onClick={() => { onAction(app, 'force-uninstall'); close() }} />
              </div>
            </>
          )}
        </div>
      )}
    </Popover>
  )
}

// ── Filter / sort vocabulary (shared by Library + Store) ────────────────────
// Each list dimension is a single-select FilterMenu section; defaults below are
// the "not filtering" key so the active-count badge + URL stay clean.

type LibSortKey = 'name' | 'updated' | 'installed' | 'status' | 'type'
const LIB_SORTS: { key: LibSortKey; label: string }[] = [
  { key: 'name', label: 'Name (A–Z)' },
  { key: 'updated', label: 'Recently updated' },
  { key: 'installed', label: 'Recently installed' },
  { key: 'status', label: 'Enabled first' },
  { key: 'type', label: 'Type' },
]
const LIB_STATUS = [
  { key: 'all', label: 'All' },
  { key: 'enabled', label: 'Enabled' },
  { key: 'disabled', label: 'Disabled' },
]
const LIB_TYPES = [
  { key: 'all', label: 'All' },
  { key: 'standard', label: 'Standard apps' },
  { key: 'provider', label: 'Provider apps' },
]
const LIB_CAPS = [
  { key: 'all', label: 'Any capability' },
  { key: 'ui', label: 'Has a UI page' },
  { key: 'backend', label: 'Runs a backend' },
  { key: 'config', label: 'Configurable' },
]
type StoreSortKey = 'name' | 'author' | 'type'
const STORE_SORTS: { key: StoreSortKey; label: string }[] = [
  { key: 'name', label: 'Name (A–Z)' },
  { key: 'author', label: 'Author' },
  { key: 'type', label: 'Type' },
]
const STORE_TYPES = [
  { key: 'all', label: 'All' },
  { key: 'standard', label: 'Standard apps' },
  { key: 'provider', label: 'Provider apps' },
]

/** Coarse kind for an installed app, used by the Type filter + grouping. Category
 *  (native vs first/third-party) is the tab; this is the within-tab shape. */
function libKind(a: AppSummary): 'provider' | 'standard' {
  if (a.isProvider) return 'provider'
  return 'standard'
}
const KIND_ORDER: Record<string, number> = { standard: 0, provider: 1 }

/** Compare two ISO timestamps descending; blanks sink to the end. */
function timeDesc(a: string | undefined, b: string | undefined): number {
  const av = a || '', bv = b || ''
  if (av === bv) return 0
  return bv.localeCompare(av)
}

/** Build the dynamic "Provider entity" options from whatever provider apps are
 *  present (Model / Search / Agent / …), most-common first, each with a count. */
function entityOptions(types: string[]): FilterOption[] {
  const counts = new Map<string, number>()
  for (const t of types) if (t) counts.set(t, (counts.get(t) ?? 0) + 1)
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type, count]) => ({ key: type, label: PROVIDER_ENTITY_LABEL[type] ?? type, count, icon: Plug }))
}

/** Build dynamic Tag options (Store), most-common first. Capped so the menu stays
 *  scannable; the search box still reaches any tag by text. */
function tagOptions(tagLists: string[][], cap = 16): FilterOption[] {
  const counts = new Map<string, number>()
  for (const tags of tagLists) for (const t of tags) if (t) counts.set(t, (counts.get(t) ?? 0) + 1)
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, cap)
    .map(([tag, count]) => ({ key: tag, label: tag, count }))
}

/** A manifest tag rendered as a CATEGORY name (PEP-3). Tags are author-controlled
 *  slugs — `productivity`, `dev-tools`, `local_model` — and a rail heading that reads
 *  "dev-tools" beside "All apps" looks like a leaked identifier. The KEY stays the raw
 *  tag (it is what the URL carries and what the grid matches); only the label changes. */
/** Words whose correct rendering is not "capitalise the first letter" — acronyms, plus one brand.
 *
 *  🔑 The function below already exists because a raw slug in a rail heading "looks like a leaked
 *  identifier". An ACRONYM sentence-cased is that same defect wearing a different hat: measured over
 *  all 54 shipped manifests, the Categories rail rendered **`Llm` (16 apps), `Acp` (4), `Tts` (3),
 *  `Stt` (2), `Onnx`** — and because the rail shows the 16 most common tags, four of its sixteen
 *  entries read as typos. `Macos`, `Gemini cli` and `Kiro cli` are the same defect on rarer tags.
 *
 *  🪤 KEYED PER WORD, NOT PER TAG. A whole-tag allowlist fixes `llm` and leaves `gemini-cli` reading
 *  "Gemini cli" — there the acronym is the SECOND word. Keying on words fixes both from one map.
 *
 *  🪤 AND THE RESULT STAYS SENTENCE CASE. Title-casing every word would turn `image_gen` into
 *  "Image Gen", against a house convention that measures 1,122 of 1,130 multi-word labels in sentence
 *  case. Only a word in this map departs from it — which is also what makes `macos` → `macOS` work:
 *  the map supplies the entire spelling, including a deliberately lowercase first letter. */
const TAG_WORD_CASING: Record<string, string> = {
  llm: 'LLM', acp: 'ACP', tts: 'TTS', stt: 'STT', onnx: 'ONNX',
  // In the shipped tag vocabulary today via `gemini-cli` / `kiro-cli` and `macos`.
  cli: 'CLI', macos: 'macOS',
  // Not in any shipped tag yet, but the same shape and cheap to be right about in advance.
  mcp: 'MCP', a2a: 'A2A', api: 'API', ui: 'UI', sdk: 'SDK', http: 'HTTP',
  url: 'URL', json: 'JSON', ocr: 'OCR', ios: 'iOS', s3: 'S3',
}

export function categoryLabel(tag: string): string {
  return tag.replace(/[-_]+/g, ' ').trim().split(' ')
    .map((w, i) => TAG_WORD_CASING[w.toLowerCase()]
      // Sentence case: capitalise the first word only, and only where the map does not own it.
      ?? (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ')
}

function matchesText(haystack: string, q: string): boolean {
  return !q || haystack.toLowerCase().includes(q)
}

/** The Library reads no socket frame; it only needs to hear that the socket reopened. */
const ignoreFrame = () => {}

export function AppsSection({ query, setQuery, navigate }: Pick<RouteProps, 'query' | 'setQuery' | 'navigate'>) {
  const q = query
  const sq = setQuery
  const nav = navigate
  // No `.catch(() => [])`. An empty list is a CLAIM ("you have no apps"), and this surface makes it
  // out loud: measured against a 500 on `/api/apps*` with a cold sessionStorage, the Library rendered
  // "No apps installed — Browse the Store to add apps" plus a Browse Store CTA, with no error text
  // anywhere and no live region. Letting the rejection through is what makes `error` exist.
  // `stale` is read, not dropped. Both reads here are `persist: true`, and a persisted entry is
  // restored with its recorded age — which on a reload is older than the namespace's window — so
  // the FIRST paint after a reload is a cached one. Measured in a browser (#2515): the Store
  // painted "All apps 67" and 67 cards with `[data-stale]` = 0, `[aria-busy]` = 0 and no
  // "updating" copy anywhere, then silently became 68 when the catalog fetch landed 2.8s later.
  // The reporter saw the same two numbers (57 → 58) and read it as the count being wrong. Both
  // were true; the screen just never said which one it was showing.
  const { data: apps, error: appsErr, stale: appsStale, refresh } = useQuery<AppSummary[]>(
    'apps', () => api.apps(), { persist: true },
  )
  // A reopened socket is how this tab learns the gateway came back, from a restart among other
  // things. Part of what the Library says about an app belongs to the PROCESS (its backend, and
  // the restart an update asked for), so re-read the list then. Otherwise "Restart the gateway
  // to finish" stayed on screen after the restart it asked for, and read as a restart that failed.
  useChatSocket(ignoreFrame, refresh)
  // Store catalog is lifted here (was inside StoreView) so the shared, pinned
  // controls bar can host the Store's search + Filter&sort too — same idiom as
  // the Library, instead of a second control bar that scrolls with the body.
  const { data: catalog, error: catalogErr, stale: catalogStale, refresh: refreshCatalog } = useQuery(
    'app-catalog', () => api.appCatalog(), { persist: true },
  )
  const [search, setSearch] = useQueryParam(q, sq, 'q', '', { replace: true })
  const [openName, setOpenName] = useQueryParam(q, sq, 'open', '')
  // Three tabs (user 2026-07-05): Native (bundled apps — locked on, not
  // installable/uninstallable), Library (the user's OWN installed apps), Store
  // (installable catalog). Native is split OUT of Library since it's a different
  // lifecycle. Default to Library (the user's apps).
  const [view, setView] = useQueryParam(q, sq, 'view', 'library', { replace: true })  // 'native' | 'library' | 'store'
  const [installing, setInstalling] = useState(false)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  // Apps installed from the Store during THIS visit. The Store lists only what can still be
  // installed (installed apps live in the Library), so a successful install used to make its
  // card vanish — and a search for it then said "No matching apps". These stay on their card,
  // shown installed, until the Store is left.
  const [installedHere, setInstalledHere] = useState<string[]>([])
  const noteInstalled = (name: string) => {
    setInstalledHere((cur) => (cur.includes(name) ? cur : [...cur, name]))
    reload()
  }

  // Library filter/sort state (deep-linked; defaults drop out of the URL).
  const [libSort, setLibSort] = useQueryParam(q, sq, 'sort', 'name', { replace: true })
  const [libStatus, setLibStatus] = useQueryParam(q, sq, 'status', 'all', { replace: true })
  const [libType, setLibType] = useQueryParam(q, sq, 'type', 'all', { replace: true })
  const [libCap, setLibCap] = useQueryParam(q, sq, 'cap', 'all', { replace: true })
  const [libEntity, setLibEntity] = useQueryParam(q, sq, 'entity', 'all', { replace: true })
  // Store filter/sort state (distinct keys so the two views never bleed).
  const [storeSort, setStoreSort] = useQueryParam(q, sq, 'ssort', 'name', { replace: true })
  const [storeType, setStoreType] = useQueryParam(q, sq, 'stype', 'all', { replace: true })
  const [storeEntity, setStoreEntity] = useQueryParam(q, sq, 'sentity', 'all', { replace: true })
  const [storeTag, setStoreTag] = useQueryParam(q, sq, 'stag', 'all', { replace: true })
  // PEP-3: the Store's SOURCE filter. Keyed on `sourceGroup().key` (the same key the
  // source dividers group by), so the rail, the dividers and the grid cannot disagree
  // about what "this source" means. URL-backed like every other filter here, which is
  // what makes a rail selection survive a reload.
  const [storeSrc, setStoreSrc] = useQueryParam(q, sq, 'ssrc', 'all', { replace: true })
  // PEP-3: below the shell's own rail threshold the Store rail is replaced by the
  // FilterMenu dropdown sections. One media query, the same one the nav rail uses —
  // when the shell collapses its rail to a drawer, this one collapses to a dropdown.
  const isMobile = useIsMobile()

  const reload = () => { invalidateKeys('apps'); invalidateKeys('app-catalog'); refresh(); refreshCatalog() }
  const reloadCatalog = () => { invalidateKeys('app-catalog'); refreshCatalog() }
  const isStore = view === 'store'
  const isNative = view === 'native'

  // ── Library: search → filter → sort ──
  const n = search.trim().toLowerCase()
  const libResult = useMemo(() => {
    if (!apps) return null
    // Native tab shows native apps; Library tab shows the rest (the user's
    // own installed apps). One filtered list drives whichever of the two is active.
    let out = apps.filter((a) => (isNative ? !!a.native : !a.native))
    out = out.filter((a) =>
      matchesText(`${a.displayName} ${a.name} ${a.description} ${(a.tags ?? []).join(' ')}`, n))
    if (libStatus !== 'all') out = out.filter((a) => (libStatus === 'enabled') === a.enabled)
    if (libType !== 'all') out = out.filter((a) => libKind(a) === libType)
    if (libCap !== 'all') out = out.filter((a) =>
      libCap === 'ui' ? a.hasUI : libCap === 'backend' ? a.hasBackend : a.hasConfig)
    if (libEntity !== 'all') out = out.filter((a) => a.isProvider && a.providerType === libEntity)
    const byName = (a: AppSummary, b: AppSummary) => a.displayName.localeCompare(b.displayName)
    out = [...out].sort((a, b) => {
      switch (libSort as LibSortKey) {
        case 'updated': return timeDesc(a.updatedAt, b.updatedAt) || byName(a, b)
        case 'installed': return timeDesc(a.installedAt, b.installedAt) || byName(a, b)
        case 'status': return Number(b.enabled) - Number(a.enabled) || byName(a, b)
        case 'type': return KIND_ORDER[libKind(a)] - KIND_ORDER[libKind(b)] || byName(a, b)
        default: return byName(a, b)
      }
    })
    return out
  }, [apps, n, isNative, libStatus, libType, libCap, libEntity, libSort])

  // ── Store: the FULL known-app universe = available-to-install catalog entries
  //    UNION already-installed apps (deduped by name), so the Store shows every
  //    app it knows about regardless of install status — then search → filter →
  //    sort over that union. ──
  const storeUniverse = useMemo<StoreItem[]>(() => {
    // The Store lists ONLY apps that can still be INSTALLED (user decision
    // 2026-07-05) — already-installed apps live in the Library tab, not here. So
    // we take the available catalog (bundled + local-dir sources) and exclude any
    // whose name is already installed. (Previously this unioned installed ∪ catalog
    // "so it never reads empty"; that's superseded — an all-installed Store now
    // correctly shows its empty state, directing the user to the Library.)
    const installedNames = new Set((apps ?? []).map((a) => a.name))
    const byName = new Map<string, StoreItem>()
    // bundled + local-dir-scanned + P20 registry-indexed (remoteApps) + git-scanned
    // multi-app repos (gitApps) — the union of every installable app the catalog surfaced,
    // flattened by the ONE merge every consumer shares (`lib/appCatalog`) rather than by a
    // concatenation order of this surface's own (#2528).
    // remoteApps/gitApps carry a `pointer` (repo[#sub]) that install uses instead of source.
    for (const e of catalogApps(catalog)) {
      if (installedNames.has(e.name) || byName.has(e.name)) continue
      // #492: `hasUI` comes off the WIRE, it is not a placeholder. This read
      // `hasUI: false` — safe while the field existed only for installed apps ("we cannot
      // know yet"), and a fabricated answer the moment the catalog started carrying it:
      // measured live, the Store detail panel for `minutes` (which declares a UI page)
      // said "Runs in this dashboard page: no". A disclosure handed a hard-coded value is
      // worse than no disclosure, so the overlay now only sets what it actually knows.
      byName.set(e.name, { ...e, installed: false, enabled: false, hasUI: Boolean(e.hasUI), native: false })
    }
    // …plus what was installed from here this visit, as the installed app it now is.
    for (const a of apps ?? []) {
      if (installedHere.includes(a.name) && !byName.has(a.name)) byName.set(a.name, installedToStoreItem(a))
    }
    return [...byName.values()]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apps, catalog, installedHere])
  const storeResult = useMemo(() => {
    let out = storeUniverse.filter((e) =>
      matchesText(`${e.displayName} ${e.name} ${e.description} ${e.author} ${(e.tags ?? []).join(' ')}`, n))
    if (storeType !== 'all') out = out.filter((e) => (storeType === 'provider') === e.isProvider)
    if (storeEntity !== 'all') out = out.filter((e) => e.isProvider && e.providerType === storeEntity)
    if (storeTag !== 'all') out = out.filter((e) => (e.tags ?? []).includes(storeTag))
    if (storeSrc !== 'all') out = out.filter((e) => sourceGroup(e, catalog?.localSources ?? []).key === storeSrc)
    const byName = (a: StoreItem, b: StoreItem) => a.displayName.localeCompare(b.displayName)
    out = [...out].sort((a, b) => {
      switch (storeSort as StoreSortKey) {
        case 'author': return (a.author || '').localeCompare(b.author || '') || byName(a, b)
        case 'type': return Number(b.isProvider) - Number(a.isProvider) || byName(a, b)
        default: return byName(a, b)
      }
    })
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeUniverse, n, storeType, storeEntity, storeTag, storeSrc, storeSort, catalog?.localSources])

  // The panel opens for BOTH installed apps (full detail + lifecycle actions) and
  // not-yet-installed Store entries (metadata + Install). `open` is the installed
  // AppSummary; `openStore` is the catalog entry when the clicked card isn't
  // installed. Exactly one is set (installed apps aren't in the Store universe).
  const open = apps?.find((a) => a.name === openName) ?? null
  const openStore = open ? null : (storeUniverse.find((e) => e.name === openName) ?? null)

  // Shared, real app actions (enable/disable inline; configure/update/force-
  // uninstall modals; open→app page) — dispatched by BOTH the cards and the
  // detail panel. Modals render once, below.
  const appActions = useAppActions(nav, reload)

  // ── Filter&sort menu sections (counts over the full set so users see what
  //    exists; the search box still reaches anything by text) ──
  const cnt = <T,>(xs: T[], p: (x: T) => boolean) => xs.filter(p).length
  const libSections: FilterSectionDef[] = useMemo(() => {
    const all = apps ?? []
    const provTypes = all.filter((a) => a.isProvider).map((a) => a.providerType)
    const s: FilterSectionDef[] = [
      { title: 'Sort by', value: libSort, defaultKey: 'name', onChange: setLibSort,
        options: LIB_SORTS.map((o) => ({ key: o.key, label: o.label })) },
      { title: 'Status', value: libStatus, defaultKey: 'all', onChange: setLibStatus,
        options: LIB_STATUS.map((o) => ({ ...o, count: o.key === 'all' ? undefined : cnt(all, (a) => (o.key === 'enabled') === a.enabled) })) },
      { title: 'Type', value: libType, defaultKey: 'all', onChange: setLibType,
        options: LIB_TYPES.map((o) => ({ ...o, count: o.key === 'all' ? undefined : cnt(all, (a) => libKind(a) === o.key) })) },
      { title: 'Capability', value: libCap, defaultKey: 'all', onChange: setLibCap,
        options: LIB_CAPS.map((o) => ({ ...o, count: o.key === 'all' ? undefined : cnt(all, (a) => o.key === 'ui' ? a.hasUI : o.key === 'backend' ? a.hasBackend : a.hasConfig) })) },
    ]
    const entities = entityOptions(provTypes)
    if (entities.length) s.push({ title: 'Provider entity', value: libEntity, defaultKey: 'all', onChange: setLibEntity,
      options: [{ key: 'all', label: 'Any entity' }, ...entities] })
    return s
  }, [apps, libSort, libStatus, libType, libCap, libEntity])

  // ── PEP-3: ONE derivation of the Store's category + source vocabulary, consumed by
  //    BOTH the wide-viewport rail and the narrow-viewport dropdown. Two derivations is
  //    how the two presentations start disagreeing about what exists.
  //    🔑 Counts are over `storeUniverse` — the exact set the grid can render — not over
  //    installed ∪ catalog. The Store deliberately EXCLUDES installed apps (they live in
  //    the Library), so a category counted over installed apps would offer a filter whose
  //    grid comes back empty: a count maintained beside a table it does not describe.
  const storeCategories: RailOption[] = useMemo(
    () => tagOptions(storeUniverse.map((e) => e.tags ?? []))
      .map((o) => ({ key: o.key, label: categoryLabel(o.label), count: o.count ?? 0 })),
    [storeUniverse])
  const storeSources: RailOption[] = useMemo(
    () => groupBySource(storeUniverse, catalog?.localSources ?? [])
      .map((g) => ({ key: g.key, label: g.label, count: g.items.length })),
    [storeUniverse, catalog?.localSources])

  const storeSections: FilterSectionDef[] = useMemo(() => {
    const s: FilterSectionDef[] = [
      { title: 'Sort by', value: storeSort, defaultKey: 'name', onChange: setStoreSort,
        options: STORE_SORTS.map((o) => ({ key: o.key, label: o.label })) },
      { title: 'Type', value: storeType, defaultKey: 'all', onChange: setStoreType,
        options: STORE_TYPES.map((o) => ({ ...o, count: o.key === 'all' ? undefined : cnt(storeUniverse, (e) => (o.key === 'provider') === e.isProvider) })) },
    ]
    const entities = entityOptions(storeUniverse.filter((e) => e.isProvider).map((e) => e.providerType))
    if (entities.length) s.push({ title: 'Provider entity', value: storeEntity, defaultKey: 'all', onChange: setStoreEntity,
      options: [{ key: 'all', label: 'Any entity' }, ...entities] })
    // PEP-3: the two rail dimensions appear in this dropdown ONLY on a narrow viewport.
    // The rail owns them whenever it is on screen, so a wide user never meets the same
    // filter twice — and a narrow user, who has no rail, still reaches both.
    if (isMobile && storeCategories.length) {
      s.push({ title: 'Categories', value: storeTag, defaultKey: 'all', onChange: setStoreTag,
        options: [{ key: 'all', label: 'All apps' }, ...storeCategories] })
    }
    if (isMobile && storeSources.length) {
      s.push({ title: 'Sources', value: storeSrc, defaultKey: 'all', onChange: setStoreSrc,
        options: [{ key: 'all', label: 'All sources' }, ...storeSources] })
    }
    return s
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeUniverse, storeSort, storeType, storeEntity, storeTag, storeSrc, isMobile, storeCategories, storeSources])

  // 🔑 ONE definition of "narrowed" per view, named once and used by BOTH the empty state and the
  // announcement. Each was already written out — the library's inside its empty-state condition, the
  // store's inline in `filtersActive` below — and a second copy is how the two start disagreeing
  // about whether a filter is on.
  const libNarrowed = !!n || libStatus !== 'all' || libType !== 'all' || libCap !== 'all' || libEntity !== 'all'
  const storeNarrowed = !!n || storeType !== 'all' || storeEntity !== 'all' || storeTag !== 'all' || storeSrc !== 'all'

  // Controls show whenever there's a populated list to act on (or while loading).
  const showLibControls = !isStore && (apps === undefined || apps.length > 0)
  // The Store now lists the full known-app universe (installed + available), so the
  // controls show whenever it's non-empty (or still loading).
  const showStoreControls = isStore && (catalog === undefined || storeUniverse.length > 0)

  return (
    <>
      <WorkbenchLayout
        topBar={<TopBar
          keepCornerPadding
          left={
            <div className="flex items-center gap-3 min-w-0">
              {/* `shrink-0`: the row's other item is a `Segmented` that DECLARES a collapse
                  strategy (below), so when the two compete the strip is the one meant to give
                  way — one pill naming the active view — and the page's own name is the one
                  thing on the row that cannot be abbreviated. Left to the default the title is
                  a shrinkable flex item and the row resolves the squeeze against the heading.
                  Deliberately NOT in `PageTitle` itself: ~30 destinations render a title beside
                  header actions where truncating a long name IS the right answer, so a
                  primitive-wide `shrink-0` would trade this defect for thirty. */}
              <PageTitle className="shrink-0">Apps</PageTitle>
              {/* 🔴 THE ONLY HEADER-ROW `Segmented` IN THE APP WITH NO COLLAPSE STRATEGY, and it is the
                  widest thing in this row. Measured at 390px: the strip spans 86..276 (**190px**) inside a
                  ~199px slot (the header is 390 wide with 191px of shell-corner reservation), so it
                  overflowed and axe reported `target-size` **serious** — `Library` ∩ `Install from URL`
                  overlapping by **40×32**, plus the `Store` tab running under the terminal and
                  notification corner controls by 26×26 each. A tap in those regions hits the wrong
                  control.
                  `collapse="menu"` is the same strategy every sibling Segmented in a header row already
                  declares (`LoopComposer`'s Granularity, Mode and Project kind) and which that row's own
                  comment prescribes: "the row can only fit by each wide control having its OWN collapse
                  strategy". Below the fit threshold this becomes one pill naming the active view, opening
                  the three in a popover — roles and arrow-key behaviour are untouched.

                  🔑 `collapse="menu"` ALONE IS NOT ENOUGH HERE, and the primitive's own docstring says why:
                  "labelled, the pill is ~119px, which still overflowed a phone header's ~42px control rail".
                  Re-measured at 390px with only the collapse strategy added, the folded pill was **84×32 at
                  113..197** inside a **115px** left slot (44..159) — so it still ran 38px into the action
                  cluster and still overlapped `Install from URL` by **38×32**. The overlap shrank from 40×32
                  to 38×32; it did not go away. Its exact width tracks the active option's label, so the
                  same variant measures **105×32 at 113..218** on `Library` — a wider label is a worse clash,
                  not a better one.

                  🔑 AND THE LABELLED PILL IS THE WEAKER TAP TARGET, not the stronger one. `iconOnly` looks
                  like it trades target size for title space, so it was measured rather than argued:
                  `document.elementFromPoint` at the labelled pill's OWN CENTRE returns `Install from URL`,
                  and Playwright cannot click the control at all (57 retries, `subtree intercepts pointer
                  events`). A 105×32 box whose centre belongs to another control is a 0px target. The bare
                  form measures 32×32 at 113..145 with nothing overlapping it and its centre resolving to
                  itself, so this RESTORES a reachable target.
                  So take the primitive's LAST rung too. `iconOnly` on mobile makes the folded pill `size-8`
                  (**32×32** at 113..145, clear of the action cluster and above the 24px SC 2.5.8 floor — not
                  a crushed target), which is the first form that fits the **44px** the title leaves.
                  That needs every option to carry an icon, because `CollapsedSegmented` deliberately falls
                  back to the labelled pill when the active option has no glyph — the icons are what unlocks
                  the rung, not decoration. Same shape as `SkillsPage`'s `ModeToggle`, the sibling that
                  already resolved this exact squeeze.
                  The icons widen the expanded strip (186 → 249px), so the fit decision was swept across
                  widths to check that did not trade the phone clash for a laptop one — `need` is the hidden
                  probe's intrinsic width, `avail` the slot this control actually gets:

                      ≥1180px   need 249  avail 261   full strip, tabs 73×32
                       1024px   need 249  avail 117   folded pill "Library" 105px
                        900px   need 249  avail  79   folded pill "Library" 105px
                       ≤768px   need 108  avail  44   folded pill, bare 32×32   ← iconOnly
                        320px   need 108  avail -20   folded pill, bare 32×32

                  No band regressed: at 1024/900 the OLD 186px strip already exceeded a 117/79px slot, so
                  that width used to crush its tabs rather than fold — folding is the improvement there too.
                  The ≥1180px margin is 12px, and running out of it folds to a named pill, which is the
                  designed rung rather than a clip. */}
              <Segmented ariaLabel="Native, Library, or Store" collapse="menu" iconOnly={isMobile}
                value={view} onChange={setView}
                options={[
                  { key: 'native', label: 'Native', icon: Boxes },
                  { key: 'library', label: 'Library', icon: Package },
                  { key: 'store', label: 'Store', icon: Store },
                ]} />
            </div>
          }
          right={<HeaderActions>
            {isStore && <HeaderControl icon={Database} label="Manage Sources" priority="default" onClick={() => setSourcesOpen(true)} />}
            <HeaderControl icon={Plus} label="Install from URL" variant="primary" priority="primary" onClick={() => setInstalling(true)} />
          </HeaderActions>}
        />}
        controls={showLibControls ? (
          <ListControls search={{ value: search, onChange: setSearch, placeholder: 'Search installed apps', label: 'Search apps' }}
            results={{ count: (libResult ?? []).length, noun: 'apps', active: apps !== undefined && libNarrowed }}
            stale={apps !== undefined && appsStale}>
            <FilterMenu sections={libSections} label="Filter & sort" />
          </ListControls>
        ) : showStoreControls ? (
          <ListControls search={{ value: search, onChange: setSearch, placeholder: 'Search the Store', label: 'Search Store' }}
            results={{ count: storeResult.length, noun: 'apps', active: catalog !== undefined && storeNarrowed }}
            stale={catalog !== undefined && catalogStale}>
            <FilterMenu sections={storeSections} label="Filter & sort" />
          </ListControls>
        ) : undefined}
        panel={(open || openStore) ? (
          <SidePanel key={(open ?? openStore)!.name} fillHeight storeKey="app-panel-w"
            title={(open ?? openStore)!.displayName} icon={<AppIcon name={(open ?? openStore)!.icon} size={18} />}
            onClose={() => setOpenName('')}>
            {open ? (
              <AppDetailPanel app={open} onClose={() => setOpenName('')} onChanged={reload}
                onOpen={() => nav(`app/${encodeURIComponent(open.name)}`)}
                onManageInstances={() => nav('settings/providers')} />
            ) : (
              <StoreDetailPanel item={openStore!} onInstalled={noteInstalled} />
            )}
          </SidePanel>
        ) : sourcesOpen && (
          <SidePanel key="sources" fillHeight storeKey="app-sources-panel-w"
            title="Manage Sources" icon={<Database size={18} />}
            onClose={() => setSourcesOpen(false)}>
            <SourcesPanel catalog={catalog} catalogError={catalogErr} reloadCatalog={reloadCatalog} onInstalled={noteInstalled} />
          </SidePanel>
        )}
      >
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {isStore ? (
            /* PEP-3: the Store is a two-column surface on a wide viewport — the
               persistent category/source rail, then the card grid. The rail is
               rendered HERE, by the page a user actually reaches (`#/apps?view=store`),
               not only inside its own test: `storeRail.test.tsx` mounts this component
               and would go red if this line were deleted. Below the mobile threshold the
               rail is absent and its two dimensions move into the FilterMenu dropdown. */
            <div className="flex items-start gap-l">
              {!isMobile && (
                <StoreSideRail
                  categories={storeCategories} category={storeTag} onCategory={setStoreTag}
                  categoryTotal={storeUniverse.length}
                  sources={storeSources} source={storeSrc} onSource={setStoreSrc}
                  sourceTotal={storeUniverse.length}
                  onAddSource={() => setSourcesOpen(true)} />
              )}
              <div className="min-w-0 flex-1">
                <StoreView catalog={catalog} catalogError={catalogErr} result={storeResult} totalKnown={storeUniverse.length}
                  installedCount={(apps ?? []).filter((a) => !a.native).length}
                  onInstalled={noteInstalled} reloadCatalog={reloadCatalog} onClearFilters={clearStoreFilters(setSearch, setStoreType, setStoreEntity, setStoreTag, setStoreSrc)}
                  filtersActive={storeNarrowed}
                  onOpen={(name) => setOpenName(name)} onAction={appActions.dispatch}
                  onOpenSources={() => setSourcesOpen(true)} />
              </div>
            </div>
          ) : apps === undefined && appsErr ? (
            // Before the skeleton branch, or a failed fetch spins it forever.
            <LoadError what="apps" error={appsErr} onRetry={reload} />
          ) : apps === undefined ? <ListSkeleton rows={4} what="apps" />
            : libResult && libResult.length === 0 ? (
              // Empty state, tab-aware: Native (should never be empty in practice —
              // native apps always ship), Library (no user-installed apps), each
              // honoring an active search/filter.
              !libNarrowed ? (
                isNative ? (
                  <EmptyState icon={Blocks} title="No native apps"
                    hint="Native tools ship with PersonalClaw and are always on." />
                ) : (
                  <EmptyState icon={Blocks} title="No apps installed"
                    hint="Browse the Store to add apps, or install one from a local path or git URL."
                    action={{ label: 'Browse Store', onClick: () => setView('store'), icon: Blocks }} />
                )
              ) : (
                <EmptyState icon={Blocks} title="No matching apps"
                  hint={`No ${isNative ? 'native' : 'installed'} app matches the current search and filters.`}
                  action={{ label: 'Clear filters', onClick: () => { setSearch(''); setLibStatus('all'); setLibType('all'); setLibCap('all'); setLibEntity('all') }, icon: RefreshCw }} />
              )
            ) : (
              <>
                <ResultCount n={libResult?.length ?? 0} total={(apps ?? []).filter((a) => (isNative ? !!a.native : !a.native)).length} noun="app" />
                {/* Library = installed apps as cards, grouped under their source
                    divider, with the same real ⋯ actions as the Store. */}
                <div className="flex flex-col gap-xl">
                  {groupBySource((libResult ?? []).map(installedToStoreItem), catalog?.localSources ?? []).map((g) => (
                    <div key={g.key}>
                      <SourceDivider label={g.label} count={g.items.length} />
                      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
                        {g.items.map((it, i) => (
                          <AppCard key={it.name} item={it} index={i}
                            onInstall={() => {}} onOpen={() => setOpenName(it.name)} onAction={appActions.dispatch} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
        </div>
      </WorkbenchLayout>
      {appActions.modals}

      {installing && <InstallModal onClose={() => setInstalling(false)} onInstalled={(name) => { setInstalling(false); noteInstalled(name) }} />}
    </>
  )
}

/** Curried "reset every Store filter" used by the no-match empty state. Every
 *  dimension in `storeNarrowed` must appear here — a filter that narrows but that
 *  "Clear filters" does not reset leaves the user stuck on an empty grid. */
function clearStoreFilters(
  setSearch: (v: string) => void, setType: (v: string) => void,
  setEntity: (v: string) => void, setTag: (v: string) => void,
  setSrc: (v: string) => void,
): () => void {
  return () => { setSearch(''); setType('all'); setEntity('all'); setTag('all'); setSrc('all') }
}

/** A small "showing N of M" line above a filtered list — only when a filter is
 *  actually narrowing the set, so an unfiltered list stays clean. */
function ResultCount({ n, total, noun }: { n: number; total: number; noun: string }) {
  if (n === total) return null
  return (
    <div data-type="label-s" className="mb-2 px-1 text-on-surface-low">
      Showing {n} of {total} {noun}{total === 1 ? '' : 's'}
    </div>
  )
}

/** Store view — the FULL known-app universe as a CARD GRID: every app the store
 *  knows about (already-installed AND available-to-install), each card showing its
 *  install state (Installed/Open vs Install). Plus the configured git + local
 *  sources (add/remove + install by URL/path).
 *
 *  Search / filter / sort live in the parent's pinned controls bar (same idiom as
 *  the Library); this presenter renders the already-filtered `result` cards plus
 *  the always-shown source sections.
 *
 *  Exported for the same reason `SourcesPanel` is: what a CARD-FOOTER install discloses at
 *  consent is a claim only a driven render can make. It is the fastest install path in the
 *  product and the one that used to disclose the least. */
export function StoreView({ catalog, catalogError, result, totalKnown, installedCount, onInstalled, reloadCatalog, onClearFilters, filtersActive, onOpen, onAction, onOpenSources }: {
  catalog: AppCatalog | null | undefined
  /** The catalog fetch's rejection. A Store that cannot reach its catalog must say so rather than
   *  render as an empty shelf — "nothing to install" and "we could not ask" are different answers. */
  catalogError?: unknown
  result: StoreItem[]
  totalKnown: number
  // installed non-native apps — the ones that LEFT the Store for the Library. Lets the
  // empty state say "all installed" (they're in the Library) vs "nothing discovered".
  installedCount: number
  /** An app finished installing from this grid (its card now shows it installed). */
  onInstalled: (name: string) => void
  reloadCatalog: () => void
  onClearFilters: () => void
  filtersActive: boolean
  onOpen: (name: string) => void
  onAction: DispatchAppAction
  onOpenSources: () => void
}) {
  // Every card-footer Install opens the ONE consent dialog, and nothing is installed until
  // the user confirms there. This was the fastest install path in the product and, on a clean
  // scan, the one that disclosed nothing at all.
  const install = useAppInstall({ onInstalled: (r) => onInstalled(r.name) })

  if (catalog === undefined && catalogError) {
    return <LoadError what="Store catalog" error={catalogError} onRetry={reloadCatalog} />
  }
  if (catalog === undefined) return <ListSkeleton rows={3} what="Store catalog" />

  return (
    <div className="flex flex-col gap-2xl">
      {install.dialog}

      {totalKnown === 0 ? (
        <div className="rounded-lg bg-surface-container px-l py-l text-on-surface-low text-[0.8125rem]">
          {installedCount > 0
            ? <>All available apps are installed — find them in the <strong className="text-on-surface">Library</strong> tab. <TextLink onClick={onOpenSources}>Manage Sources</TextLink> to discover more.</>
            : <>No apps found. <TextLink onClick={onOpenSources}>Manage Sources</TextLink> to add a git or local source and discover apps.</>}
        </div>
      ) : result.length === 0 ? (
        <EmptyState icon={Blocks} title="No matching apps"
          hint="No app matches the current search and filters."
          action={filtersActive ? { label: 'Clear filters', onClick: onClearFilters, icon: RefreshCw } : undefined} />
      ) : (
        <div className="flex flex-col gap-xl">
          {/* One card grid per SOURCE, under its own divider — Built-in first,
              then each configured git/local source. */}
          {groupBySource(result, catalog?.localSources ?? []).map((g) => (
            <div key={g.key}>
              <SourceDivider label={g.label} count={g.items.length} />
              <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
                {g.items.map((e, i) => (
                  <AppCard key={e.name} item={e} index={i}
                    onInstall={() => install.begin({ source: e.pointer || e.source, label: e.displayName || e.name })}
                    onOpen={() => onOpen(e.name)} onAction={onAction} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Right-sidebar panel for managing app sources (git URLs and local paths). Opened
 *  via the "Manage Sources" button in the Store header area, keeping the main page
 *  area free for the app card grid. */
/** The Store's source list. Exported so the default/removable labelling of a shipped source
 *  can be driven at the level a user meets it (`sourceLabels.test.tsx`) — a badge and a missing
 *  remove control are exactly the kind of claim no backend test can make. */
export function SourcesPanel({ catalog, catalogError, reloadCatalog, onInstalled }: {
  catalog: AppCatalog | null | undefined
  /** The `app-catalog` rejection, so this panel can tell a FAILED read from an empty one.
   *  Same prop `StoreView` already takes, from the same `useQuery` — see the guard below. */
  catalogError?: unknown
  reloadCatalog: () => void
  /** An app finished installing from one of these sources. */
  onInstalled: (name: string) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [newSource, setNewSource] = useState('')
  const [newLocal, setNewLocal] = useState('')
  // Installing by SOURCE is the same consent path as a card: the dialog reviews the source on
  // the server, which reads the manifest whether or not the Store has indexed it.
  const install = useAppInstall({ onInstalled: (r) => onInstalled(r.name) })

  /** The name to title the dialog with until its review reads the app's own: the display name
   *  of the catalog row that installs FROM this exact location (its `pointer`, else its
   *  `source`), else the source itself. A registry row's `source` is the registry it was listed
   *  in, not where it installs from — matching on that titled the registry's own row after the
   *  first app it lists. Found through the ONE merge (`lib/appCatalog`) — a git-first lookup of
   *  its own once named a different copy of the app than the grid had shown (#2528). */
  function labelForSource(source: string): string {
    const entry = catalogApps(catalog).find((e) => (e.pointer || e.source) === source)
    return entry?.displayName || source
  }

  /** Repaint this panel's source lists from the WRITE'S OWN ANSWER, not from a re-read.
   *
   *  🔑 A SUCCESSFUL ADD LEFT THE PANEL SAYING "No local sources" (#2627's second
   *  observation, which the committed harness already flags as `listedInPanel: false`).
   *  The list here IS `catalog.localSources`, and `reloadCatalog()` does invalidate that
   *  key — but `invalidateKeys` deliberately KEEPS the cached value so open panels don't
   *  blank, so this one keeps painting the pre-write array for however long the re-read
   *  takes. And that re-read is the most expensive request in the app: `available_catalog()`
   *  also scans registries and shallow-clones every git source behind a 5-minute TTL. Slow,
   *  and the panel contradicts itself; failed, and the cache holds the pre-write value
   *  indefinitely with nothing on screen saying so.
   *
   *  🪤 THE AUTHORITATIVE LIST WAS ALREADY IN HAND AND BEING THROWN AWAY. Both add endpoints
   *  return `{ ok, sources }` — the post-write list, straight from the same file the GET
   *  would re-read. So this is not a second source of truth; it is the same one, arriving
   *  earlier. `reloadCatalog()` still runs and still lands last, because the real catalog
   *  read carries `localApps`/`gitApps` for the Store grid, which this cannot synthesise. */
  function paintSources(patch: { gitSources?: string[]; localSources?: string[] }) {
    // No catalog yet ⇒ nothing to merge onto, and inventing a partial one would strip the
    // grid's other slices. The pending read is the only correct answer in that state.
    if (!catalog) return
    writeQuery('app-catalog', { ...catalog, ...patch }, true)
  }

  async function addSource() {
    const u = newSource.trim()
    if (!u) return
    setBusy('add-source'); setErr(null)
    try {
      const r = await api.addAppSource(u)
      setNewSource(''); reloadCatalog(); paintSources({ gitSources: r.sources })
    }
    catch (e) { setErr(String((e as Error).message || e)) }
    finally { setBusy(null) }
  }

  async function addLocalSource() {
    const p = newLocal.trim()
    if (!p) return
    setBusy('add-local'); setErr(null)
    try {
      const r = await api.addLocalAppSource(p)
      setNewLocal(''); reloadCatalog(); paintSources({ localSources: r.sources })
    }
    catch (e) { setErr(String((e as Error).message || e)) }
    finally { setBusy(null) }
  }

  const sources = catalog?.gitSources ?? []
  const localSources = catalog?.localSources ?? []
  const firstPartySources = new Set(catalog?.firstPartySources ?? [])
  const defaultSources = new Set(catalog?.defaultGitSources ?? [])
  const builtinSources = new Set(catalog?.builtinGitSources ?? [])
  const networkSources = catalog?.networkSources ?? []
  // #408: which source failed, keyed by URL. Without this the Store showed fewer apps (or,
  // before the scan budget, spun for two minutes) with nothing naming the source at fault,
  // so the natural diagnosis was "the Store is broken" rather than "remove that one".
  const unavailableReason = new Map(
    (catalog?.unavailableSources ?? []).map((u) => [u.source, u.reason]),
  )

  // 🔴 THE EMPTY STATES BELOW ARE CLAIMS ABOUT CONFIGURATION, AND THEY WERE RENDERED ON A FACT
  // ABOUT LOADING (#2629). `sources.length === 0` is `catalog?.gitSources ?? []` — so an
  // in-flight read and a failed one both collapse to `[]` and the panel stated *"No git
  // sources configured"* / *"No local sources"*. Absent and zero are different facts, and on
  // THIS panel the difference is security-relevant: Manage Sources is the egress-disclosure
  // surface (#2528), and the disclosure paragraph below is gated on `networkSources.length > 0`
  // from the same absent object. So during the catalog fetch a user read "no git sources" with
  // no egress notice, concluded nothing reaches the network, and closed a panel that then
  // populated with the sources that were there all along — the panel briefly asserted the
  // OPPOSITE of the fact it exists to disclose. It also cost real time: the first screenshot
  // taken for #2528 captured this state and read as a genuine "the Store has no sources" bug.
  //
  // 🔑 NOT A NEW EMPTY-STATE STRING — the THIRD case the kit already documents. `ListScaffold`'s
  // own header spells the condition out ("Pair with the ONE condition that distinguishes the two
  // states"), and `StoreView` above already uses it verbatim; this panel was the consumer that
  // didn't. Both branches now sit behind one settled-ness gate, so they cannot diverge.
  //
  // 🪤 THE FAILED READ WAS THE WORSE HALF, and it is why this takes `catalogError` rather than
  // just a boolean. Measured at this head: `StoreView` gets `catalogError` and renders
  // `LoadError` (`:840`), but `SourcesPanel` was handed `catalog` alone — so a 500 on
  // `/api/apps/catalog` was INDISTINGUISHABLE here from a deliberately empty configuration,
  // and it retried nothing. The grid said "couldn't load"; the panel beside it said "none
  // configured", about the same failed request.
  if (catalog === undefined && catalogError) {
    return <LoadError what="app sources" error={catalogError} onRetry={reloadCatalog} />
  }
  if (catalog === undefined) return <ListSkeleton rows={2} what="app sources" />

  return (
    <div className="flex flex-col gap-xl">
      {err && <FieldError>{err}</FieldError>}
      {install.dialog}

      <section>
        <div className="mb-2 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Git sources</div>
        <div className="mb-2 flex items-center gap-2">
          <TextInput value={newSource} onChange={setNewSource} name="app-git-source"
            placeholder="https://github.com/owner/app.git" />
          <Button variant="secondary" size="sm" loading={busy === 'add-source'} disabled={busy === 'add-source' || !newSource.trim()} onClick={addSource}
            disabledReason={!newSource.trim() ? 'Enter a source URL first' : undefined}>
            <Plus size={15} /> Add
          </Button>
        </div>
        {sources.length === 0 ? (
          <div className="text-on-surface-low text-[0.8125rem]">No git sources configured. Add a git URL to discover apps from it.</div>
        ) : (
          <div className="flex flex-col gap-1">
            {sources.map((url) => {
              // Two independent bits, both from the backend: shipped-by-us (label it, so a
              // user can tell the curated registry from a URL they typed) and removable.
              // A BUNDLED default is folded into every read server-side, so its DELETE is a
              // no-op — showing that button would be a control that silently does nothing.
              // The seeded registry default IS removable and its removal persists.
              const isDefault = defaultSources.has(url)
              const isBuiltin = builtinSources.has(url)
              const unavailable = unavailableReason.get(url)
              return (
              <div key={url} className="flex items-center gap-3 rounded-lg bg-surface-container px-l py-m">
                <Download size={15} className="shrink-0 text-on-surface-low" />
                {/* `title` because the row truncates: with a Default label, an Unavailable
                    badge and two controls beside it a long URL renders as "htt…", and naming
                    the source at fault is the entire point of the badge (issue 408). */}
                <span title={url} className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{url}</span>
                {/* `no-git` may NOT promise the automatic retry: a git source is read by
                    shelling out to git, so with no git on PATH every retry fails the same
                    way forever. Naming the missing dependency is the only thing that gets
                    the user out of it. */}
                {unavailable && (
                  <span data-testid="store-source-unavailable" title={unavailable === 'budget'
                    ? 'Skipped — the catalog scan ran out of time before reaching this source.'
                    : unavailable === 'no-git'
                      ? 'Needs git, which is not installed on this machine. Install git to read this source.'
                      : 'Could not be reached on the last listing read. It will be retried automatically.'}
                    className="shrink-0 rounded-pill bg-surface-highest px-2 py-0.5 text-warn text-[0.75rem]">
                    {unavailable === 'budget' ? 'Skipped' : unavailable === 'no-git' ? 'Needs git' : 'Unavailable'}
                  </span>
                )}
                {isDefault && (
                  <span className="shrink-0 rounded-pill bg-surface-highest px-2 py-0.5 text-on-surface-low text-[0.75rem]">Default</span>
                )}
                <Button variant="ghost" size="sm" onClick={() => install.begin({ source: url, label: labelForSource(url) })}><Download size={14} /> Install
                </Button>
                {!isBuiltin && (
                  <SquareIconButton icon={Trash2} tone="danger" label="Remove source" className="shrink-0"
                    onClick={async () => { await api.removeAppSource(url); reloadCatalog() }} />
                )}
              </div>
            )})}
          </div>
        )}
        {/* 🔴 The egress disclosure (issue 2528, finding 1). A brand-new home lists a shipped git
            source, so opening the Store contacts github.com before the user has configured
            anything. The hosts are named on the surface that triggers the fetch, and only when a
            network source is actually listed — with an all-local configuration this says nothing
            rather than warning about nothing.
            The second sentence is the other half: a shipped source has no ROW to delete (it is
            folded into every backend read), so a hidden remove button is not an explanation. It
            appears only when such a source is listed, and it names where the switch is. */}
        {networkSources.length > 0 && (
          <p data-testid="store-egress-disclosure" data-type="caption" className="mt-2 text-on-surface-low">
            Reading these listings contacts {networkSources.join(', ')}. Only listings are
            fetched — nothing is installed or run without your consent.
            {builtinSources.size > 0 && ' A source that ships with PersonalClaw has no remove button; turn it off in Settings → Apps.'}
          </p>
        )}
        <p className="mt-2 text-on-surface-low text-[0.75rem]">
          Installing shows you what the app gets and what the security scanner found first — nothing
          installs until you confirm, and a dangerous verdict is always refused.
        </p>
      </section>

      <section>
        <div className="mb-2 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Local sources</div>
        <div className="mb-2 flex items-center gap-2">
          <TextInput value={newLocal} onChange={setNewLocal} name="app-local-source"
            placeholder="/path/to/apps  (a directory of app subdirs)" />
          <Button variant="secondary" size="sm" loading={busy === 'add-local'} disabled={busy === 'add-local' || !newLocal.trim()} onClick={addLocalSource}
            disabledReason={!newLocal.trim() ? 'Enter a folder path first' : undefined}>
            <Plus size={15} /> Add
          </Button>
        </div>
        {localSources.length === 0 ? (
          <div className="text-on-surface-low text-[0.8125rem]">No local sources. Add a directory of app bundles (dev loop, or a checked-out apps/ tree).</div>
        ) : (
          <div className="flex flex-col gap-1">
            {localSources.map((path) => {
              const isFirstParty = firstPartySources.has(path)
              return (
              <div key={path} className="flex items-center gap-3 rounded-lg bg-surface-container px-l py-m">
                <FolderOpen size={15} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 flex-1 truncate font-mono text-on-surface text-[0.75rem]">{path}</span>
                {isFirstParty ? (
                  <span className="shrink-0 rounded-pill bg-surface-highest px-2 py-0.5 text-on-surface-low text-[0.75rem]">First-party</span>
                ) : (
                  <SquareIconButton icon={Trash2} tone="danger" label="Remove local source" className="shrink-0"
                    onClick={async () => { await api.removeLocalAppSource(path); reloadCatalog() }} />
                )}
              </div>
            )})}
          </div>
        )}
      </section>
    </div>
  )
}

/** One Store card. Identity (icon/name/version/provider) + description + tags,
 *  and a footer of REAL, direct actions (each does its thing — nothing here just
 *  opens the sidebar):
 *   • available  → "Install" (opens the consent dialog; nothing installs until confirmed).
 *   • installed  → primary "Open" (→ the app's page) when it has a UI, plus a "⋯"
 *     menu of the real lifecycle actions (Configure / Update / Enable-Disable /
 *     Force-uninstall). The card's NAME is a link to the detail panel (explicit
 *     "details" affordance), so the action buttons never double as "open panel". */
function AppCard({ item, index, onInstall, onOpen, onAction }: {
  item: StoreItem; index: number; onInstall: () => void; onOpen: () => void; onAction: DispatchAppAction
}) {
  const providerLabel = item.isProvider
    ? `${PROVIDER_ENTITY_LABEL[item.providerType] ?? item.providerType} provider` : ''
  const app = { name: item.name, displayName: item.displayName, enabled: item.enabled, hasUI: item.hasUI, configuredPerInstance: item.configuredPerInstance }
  // Right-click / long-press → the SAME real actions this card dispatches. A native
  // app is always-on (no install lifecycle): omit uninstall/toggle + force-uninstall,
  // and show "Configure" only when it has settings (hasConfig) — a config-less native
  // provider (filesystem/tools) is managed from the Tools page.
  const menuItems: ContextMenuItem[] = item.installed
    ? [
        { icon: <Blocks size={15} />, label: 'Details', onSelect: onOpen },
        ...(item.hasUI && item.enabled ? [{ icon: <LayoutGrid size={15} />, label: 'Open page', onSelect: () => onAction(app, 'open') }] : []),
        ...((item.enabled && (!item.native || item.hasConfig || item.configuredPerInstance)) ? [{ icon: <Settings2 size={15} />, label: item.configuredPerInstance ? 'Manage instances' : 'Configure', onSelect: () => onAction(app, 'configure') }] : []),
        { icon: <RefreshCw size={15} />, label: 'Update…', onSelect: () => onAction(app, 'update') },
        // A native app is locked on — omit uninstall/disable + force-uninstall.
        ...(item.native ? [] : [
          { icon: <Power size={15} />, label: item.enabled ? 'Deactivate' : 'Activate', onSelect: () => onAction(app, 'toggle') },
          // Safe removal (files go, the user's data/ stays) before the destructive one.
          { icon: <Archive size={15} />, label: 'Uninstall…', onSelect: () => onAction(app, 'uninstall') },
          { icon: <Trash2 size={15} />, label: 'Force uninstall…', onSelect: () => onAction(app, 'force-uninstall'), danger: true },
        ]),
      ]
    : [
      { icon: <Blocks size={15} />, label: 'Details', onSelect: onOpen },
      { icon: <Download size={15} />, label: 'Install', onSelect: onInstall },
    ]
  // PEP-3 — ONE card anatomy, art-forward, whatever the manifest declares. Previously
  // the card had four shapes (hero+icon · hero-only · icon-only · neither) and only the
  // hero ones were banner-topped, so a grid mixing hero and hero-less apps read as two
  // different components — the hero-less card looked like the image had failed to load.
  // Now every card is: banner → icon avatar over its lower edge → name → description →
  // quality → footer. The two art paths differ only in WHAT fills the banner:
  //   • the app declares `heroUrl` → its own image, object-cover.
  //   • it declares none → its deterministic token gradient (`appArt.ts`), keyed on the
  //     app name so the art is stable across reloads/machines and neighbours differ.
  // The icon tile is unconditional too: `AppIcon` already resolves an absent/legacy
  // icon to the Blocks app glyph, so "no icon" is a rendered fallback, not an empty slot.
  const hero = item.heroUrl
  // Clicking the card opens the detail panel; interactive controls inside
  // (action menu, primary button) stop propagation so they act, not navigate.
  const stop = (e: React.MouseEvent) => e.stopPropagation()

  const iconTile = (
    <div className="grid size-12 shrink-0 place-items-center rounded-lg bg-surface-high text-on-surface-low ring-2 ring-surface-container">
      <AppIcon name={item.icon} size={24} />
    </div>
  )

  // 🔴 Provenance, as TEXT, BEFORE install (#2528). The only pre-install signal that a card
  // came from a local source used to be the divider heading (a folder basename) and the
  // Sources rail — nothing on the card itself said where its bytes came from, which is why a
  // remote card standing in for a local bundle was invisible to the user and visible only to
  // whoever read the catalog code. Rendered from the ONE provenance owner
  // (`lib/provenance`), and only pre-install: an installed app's origin is already named in
  // the detail panel, and repeating it in the Library's card row would be noise.
  const origin = item.installed ? null : provenance({ sourceKind: item.sourceKind })

  // ET-5. `origin` above says "git" — the bytes came over the network. It cannot say WHO listed
  // them or whether anyone has looked, which is the question a community listing actually raises.
  // The index publishes three fields that answer it and the catalog used to drop all three.
  //
  // Pre-install only, for the same reason as `origin`: once installed, the app's real scan
  // verdict is on the record and a listing's month-old claim would be the weaker of two facts
  // sitting next to each other. And `null` for a card that is not registry-sourced comes from
  // the data, not from a test here — only `_pointer_to_entry` fills these fields in.
  const listing = item.installed
    ? null
    : registryListing({
        maintainer: item.maintainer,
        lastValidated: item.lastValidated,
        lastScanVerdict: item.lastScanVerdict,
        day: dayStamp,
      })

  return (
    <ContextMenu items={menuItems}>
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      // physical liftable feature card: rises + gains shadow on hover (depth via
      // expr), consistent with the ListRow/Surface/TaskCard treatment. The whole
      // card is a click target → the app detail panel.
      whileHover={{ y: -expr(4, 0.3), boxShadow: 'var(--shadow-lift)' }}
      // No role/tabIndex/onKeyDown here: the card carries its own Actions menu button, so a
      // role="button" wrapper is `nested-interactive` (axe, serious). The tab stop is the
      // empty overlay below — the ListRow resolution. `whileHover`/`whileTap` still make
      // Motion mark the wrapper focusable, hence tabIndex={-1} rather than no attribute.
      tabIndex={-1}
      onClick={onOpen}
      className="group relative flex min-h-[11rem] cursor-pointer flex-col overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary"
      style={{ borderRadius: 'var(--radius-lg)' }}>
      {/* The name was carried by `title` on the wrapper before. */}
      <RowHitTarget label={`${item.displayName} — details`} />

      {/* Banner — the full-bleed cap EVERY card carries. `data-art` names which of the
          two paths drew it, so a test can tell "the app's own hero" from "the generated
          fallback" without reading a background string. A subtle scrim keeps the overlaid
          icon legible over a busy image or a saturated gradient. */}
      <div className="relative h-28 w-full shrink-0 overflow-hidden bg-surface-high"
        data-art={hero ? 'hero' : 'generated'}
        style={hero ? undefined : { background: artGradient(item.name) }}>
        {hero && (
          <img src={hero} alt="" loading="lazy"
            className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-surface-container/50 to-transparent" />
      </div>

      <div className="flex flex-1 flex-col gap-2.5 p-4 pt-0">
        {/* header: icon avatar floating over the banner's lower edge + name + version */}
        <div className="-mt-6 flex items-start gap-3">
          {iconTile}
          <div className="min-w-0 flex-1 pt-6">
            <div className="flex items-center gap-1.5">
              <span data-type="body-l" className="truncate text-on-surface transition-colors group-hover:text-primary" style={fvs(550)}>{item.displayName}</span>
              {item.version && <span data-type="label-s" className="shrink-0 text-on-surface-low">v{item.version}</span>}
              {item.installed && item.updateAvailable && (
                <span data-type="label-s" title={item.latestVersion ? `Update to v${item.latestVersion} available` : 'Update available'}
                  className="inline-flex shrink-0 items-center gap-1 rounded-pill px-1.5 py-0.5"
                  style={accentChip}>
                  <RefreshCw size={11} /> Update
                </span>
              )}
            </div>
            {/* 🔴 3.97:1 (need 4.5) before this, measured by BOTH `ux-audit` and axe at light/phone on
                  every card: coral ink on a 14% coral tint. `design/accent.ts` already documents this
                  exact failure — a tint is not symmetric across modes, and in light it lifts the
                  backdrop TOWARD the dark accent until ink and background converge (14% → 3.62 by its
                  own table) — and ships the pair that fixes it: `primary-container` /
                  `on-primary-container`, 13.1:1 light and 10.43:1 dark, guaranteed for all 12 schemes
                  by `schemeContrast.test.ts`. This chip was the last accent-carrying TEXT left on the
                  old spelling. */}
            <div className="mt-0.5 flex flex-wrap items-center gap-1">
              {providerLabel && (
                <span className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5" data-type="label-s"
                  style={accentChip}>
                  <Plug size={11} />{providerLabel}
                </span>
              )}
              {origin && (
                <span data-testid="store-card-origin" title={origin.title}
                  className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-var"
                  data-type="label-s">
                  <MapPin size={11} />{origin.label}
                </span>
              )}
            </div>
          </div>
          {/* installed apps get the real ⋯ actions menu, top-right */}
          {item.installed && <span onClick={stop}><AppActionMenu item={item} onAction={onAction} /></span>}
        </div>

        {/* description (clamped to 2 lines) + author.
            `inline` Markdown, not the block renderer: `line-clamp` is `-webkit-box`-based, so
            it clamps the element that CARRIES the flow — a block child would escape the clamp
            and the card would grow. The author suffix stays OUTSIDE the markdown so a name
            with an underscore in it is a name, not emphasis. */}
        <p className="line-clamp-2 flex-1 text-on-surface-low" data-type="body-s">
          <Markdown inline>{item.description || item.name}</Markdown>{item.author ? ` · by ${item.author}` : ''}
        </p>

        {/* APE-4: the app's DECLARED quality bar. Renders nothing at all when the app
            declared no block — an unbadged app and a failing app are different states. */}
        <QualityBadges quality={item.quality} />

        {/* ET-5 — a registry listing's own provenance. Two lines, and the ORDER is the control:
            the non-endorsement is the first thing read, the facts second. Reversing them would
            put "clean" above "community-listed" and turn a stale third-party check into what
            looks like our verdict. All wording comes from `lib/provenance` — the one owner — so
            it cannot be softened per-card. */}
        {listing && (
          <div data-testid="store-card-listing" title={listing.title} className="flex flex-col gap-0.5">
            <span data-testid="store-card-listing-headline" data-type="label-s"
              className={listing.clean ? 'text-on-surface-var' : 'text-warn'}>
              {listing.headline}
            </span>
            {/* The verdict-bearing line. A non-clean check does NOT get the same low-emphasis ink
                as a clean one — a grey "flagged" where a grey "clean" sat reads as equally fine. */}
            <span data-testid="store-card-listing-detail" data-type="label-s"
              className={listing.clean ? 'text-on-surface-low' : 'text-warn'}>
              {listing.detail}
            </span>
          </div>
        )}

        {/* footer: tags + the state-appropriate PRIMARY action */}
        <div className="flex items-center gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
            {(item.tags ?? []).slice(0, 3).map((t) => (
              <span key={t} className="inline-flex h-6 items-center rounded-pill bg-surface-high px-2 text-on-surface-var text-[0.75rem]">{t}</span>
            ))}
            {/* A silently dropped 4th tag was indistinguishable from an app that has three —
                the same undisclosed-cap defect `MoreRow` exists to close, so it is the SAME
                sentence here as everywhere else rather than a card-local `+N` pill. */}
            <MoreRow total={(item.tags ?? []).length} shown={3} noun="tags" />
          </div>
          {item.installed ? (
            item.hasUI && item.enabled ? (
              <span onClick={stop}><Button variant="secondary" size="sm" onClick={() => onAction(app, 'open')}><LayoutGrid size={14} /> Open</Button></span>
            ) : item.enabled ? (
              // `text-positive` was inert too — `--color-positive` is undefined; `ok` is the token,
              // so this chip has been rendering in plain body ink rather than green.
              <span className="inline-flex items-center gap-1 text-ok" data-type="label-s"><ShieldCheck size={13} /> Installed</span>
            ) : (
              // Deactivated (uninstalled, files kept): state must be visible on the
              // card — a green "Installed" here hid the fact the app is off. One
              // click re-activates (the same Activate/Deactivate toggle every surface shares).
              <span onClick={stop}><Button variant="primary" size="sm" onClick={() => onAction(app, 'toggle')}><Power size={14} /> Activate</Button></span>
            )
          ) : (
            <span onClick={stop}><Button variant="secondary" size="sm" onClick={onInstall}><Download size={14} /> Install
            </Button></span>
          )}
        </div>
      </div>
    </motion.div>
    </ContextMenu>
  )
}

// Human label for the entity a provider app plugs into (App Store indicator).
const PROVIDER_ENTITY_LABEL: Record<string, string> = {
  model: 'Model', agent: 'Agent', search: 'Search', channel: 'Channel',
  inbox: 'Inbox', notification: 'Notification', tool: 'Tool', task: 'Task',
  action: 'Action', skills: 'Skills', knowledge: 'Knowledge', memory: 'Memory',
  prompt: 'Prompt', workflow: 'Workflow',
  // Types whose label was missing fell through to the raw snake_case key in the Store indicator
  // ("trigger_source provider", "duty_gate provider"). Named here so the install-consent card reads
  // as prose — the card is where a user decides what to grant, so its wording is part of the control.
  trigger_source: 'Trigger source', duty_gate: 'Duty gate', sync: 'Sync', sandbox: 'Sandbox',
  // 🔴 AND `trigger` FELL THROUGH THE SAME WAY, which is why the comment above is now a pattern and
  // not an anecdote. `shared-automations` ships `provider.type: "trigger"` (TSE-4 — a STORE of trigger
  // rows, distinct from `trigger_source`, which supplies the stimulus), so its Store card read
  // "trigger provider" and its rail facet read a bare lowercase "trigger".
  //
  // 🔑 The Python side is guarded — `test_manifest_types_match_handlers` pins `PROVIDER_TYPES` to the
  // handler registry — but NOTHING guarded this map against `PROVIDER_TYPES`, which is exactly how a
  // type reaches the UI unlabelled. `storeProviderLabels.test.ts` now closes that: it parses the
  // Python set and requires an entry here for every member. Measured before adding this line: 19
  // provider types, and `trigger` was the only one missing.
  trigger: 'Trigger',
  // 🔴 FOURTH occurrence of the class above — `ocr` (KOCR-1) reached `PROVIDER_TYPES` with no entry
  // here, so the guard below caught it before a user saw "ocr provider". Acronym, so it is shouted:
  // `TAG_WORD_CASING` already renders the `ocr` TAG as "OCR", and the two must agree.
  ocr: 'OCR',
  // 🔴 FIFTH occurrence — `vector_store` (KBVS-1) joined `PROVIDER_TYPES` with no entry here, so the
  // guard below caught "vector_store provider" before a user read it. Sentence case, matching
  // `trigger_source` → "Trigger source": the house convention is the first word only.
  vector_store: 'Vector store',
}


// ── Install from URL: the source is typed here; the review and the consent are the dialog's ──
function InstallModal({ onClose, onInstalled }: { onClose: () => void; onInstalled: (name: string) => void }) {
  const [source, setSource] = useState('')
  const install = useAppInstall({ onInstalled: (r) => onInstalled(r.name) })
  // While the consent dialog is up it is the ONLY modal. Closing it comes back here with the
  // typed source intact, so a mistyped URL is one edit away rather than a retyping.
  if (install.active) return install.dialog
  const s = source.trim()
  return (
    <Modal title="Install app" icon={<Download size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <label data-type="body-s" className="text-on-surface-low">Source — local path or git URL</label>
        <TextInput value={source} onChange={setSource} autoFocus name="app-install-source"
          placeholder="/path/to/app  or  https://github.com/owner/app.git" />
        <p data-type="label-s" className="text-on-surface-low">
          You will see what the app gets and what the security scanner found before anything is installed.
        </p>
        <div className="flex justify-end gap-2 pt-s">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!s} disabledReason={!s ? 'Enter a source first' : undefined}
            onClick={() => install.begin({ source: s, label: s })}><Download size={16} /> Review
          </Button>
        </div>
      </div>
    </Modal>
  )
}

// ── Update: the new source is typed here; what it changes, and the consent, are the dialog's ──
/** Where the gateway found an app's newer version, when it found one. */
interface FoundUpdate { source: string; version?: string }

/** The found update an Update dialog starts from — none when the app has no update the
 *  gateway located, and then the owner types the source. */
function foundUpdate(app: { updateAvailable?: boolean; latestSource?: string; latestVersion?: string }): FoundUpdate | undefined {
  return app.updateAvailable && app.latestSource ? { source: app.latestSource, version: app.latestVersion } : undefined
}

function UpdateModal({ name, displayName, found, onClose, onUpdated }: {
  /** The app SLUG — the update API's identifier. */
  name: string
  /** What the title says: a person recognises "Research Lab", not `research-lab`. */
  displayName: string
  /** Where the gateway found the newer version. The field starts with it and stays editable:
   *  the owner should not have to type where the gateway just looked. */
  found?: FoundUpdate
  onClose: () => void
  onUpdated: () => void
}) {
  const [source, setSource] = useState(found?.source ?? '')
  const install = useAppInstall({ onInstalled: () => onUpdated() })
  if (install.active) return install.dialog
  const s = source.trim()
  return (
    <Modal title={`Update ${displayName}`} icon={<RefreshCw size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <label htmlFor="app-update-source" data-type="body-s" className="text-on-surface-low">New source — local path or git URL (data is preserved)</label>
        <TextInput id="app-update-source" value={source} onChange={setSource} autoFocus name="app-install-source"
          placeholder="/path/to/app  or  https://github.com/owner/app.git" />
        {found && (
          <p data-type="label-s" className="text-on-surface-low">
            {`PersonalClaw found ${found.version ? `version ${found.version}` : 'the newer version'} there. Change it to update from somewhere else.`}
          </p>
        )}
        <p data-type="label-s" className="text-on-surface-low">
          You will see what the new version changes, and what the security scanner found, before anything is updated.
        </p>
        <div className="flex justify-end gap-2 pt-s">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!s} disabledReason={!s ? 'Enter a source first' : undefined}
            onClick={() => install.begin({ source: s, label: displayName, update: name })}><RefreshCw size={16} /> Review update
          </Button>
        </div>
      </div>
    </Modal>
  )
}


// ── Detail panel ──
function AppDetailPanel({ app, onClose, onChanged, onOpen, onManageInstances }: {
  app: AppSummary; onClose: () => void; onChanged: () => void; onOpen: () => void
  /** Where an app configured per instance is configured: Settings → Providers. */
  onManageInstances: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [confirmUninstall, setConfirmUninstall] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [updateOpen, setUpdateOpen] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [inNav, setInNavState] = useState(() => isInNav(app.name))

  async function toggle() {
    setBusy(true)
    try {
      // Reported, and the repaint GATED on the answer. This was a bare `try { … } finally { … }`
      // with no catch: a refused activate/deactivate rejected unhandled, the spinner stopped, the
      // Activate/Deactivate button kept its old label and nothing named the reason — which is
      // exactly what a successful no-op would look like. Toast rather than an inline slot because
      // this is a button ROW with nowhere to put a sentence (the two dialogs below report inline,
      // where they do have somewhere); the same split `dashboard/PinnedTiles` already draws.
      const verb = app.enabled ? 'deactivate' : 'activate'
      const run = () => (app.enabled ? api.disableApp(app.name) : api.enableApp(app.name))
      if (!(await reportingWrite(`${verb} ${app.name}`, run))) return
      onChanged()
    } finally { setBusy(false) }
  }

  const toggleNav = () => { const next = !inNav; setInNav(app.name, next); setInNavState(next) }

  // `sourceKind` is resolved by the backend for installed apps too (`/api/apps`), so this
  // surface never translates between the `origin` and `sourceKind` vocabularies itself.
  const installedOrigin = provenance({ sourceKind: app.sourceKind })

  return (
    <>
      <div className="flex flex-col gap-l p-l">
        <div>
          {/* The panel CAN hold blocks, so the description gets the real (block) renderer — an
              `app.json` description with paragraphs or a bullet list is structured prose, and
              this is the surface a person reads before deciding to keep the app. */}
          <div data-type="body-s"><Markdown>{app.description || app.name}</Markdown></div>
          {/* Provenance through the ONE owner (`lib/provenance`) rather than the raw `origin`
              string with a `|| 'local'` fallback — that fallback CLAIMED "local" for an app
              whose origin the record did not carry, which is the same false-provenance defect
              as issue 2514 one surface over. No origin ⇒ the version stands alone. */}
          <div data-type="label-s" className="mt-1 text-on-surface-low">
            v{app.version}{installedOrigin ? ` · ${installedOrigin.label}` : ''}
          </div>
          {/* APE-4: same badge row, same component, as the Store card and the pre-install
              panel — one declaration rendered one way across every surface that shows it. */}
          <div className="mt-2"><QualityBadges quality={app.quality} /></div>
        </div>

        {app.updateAvailable && (
          <div className="flex items-center justify-between gap-3 rounded-md border border-primary/40 bg-surface-high p-m"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 8%, var(--color-surface-high))' }}>
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-primary" data-type="body-s"><RefreshCw size={14} /> Update available</div>
              <div className="mt-0.5 text-on-surface-low" data-type="label-s">
                {app.latestVersion ? `Version ${app.latestVersion} is available (you have ${app.version}).` : 'A newer version is available.'}
              </div>
            </div>
            <Button variant="primary" size="sm" className="shrink-0" onClick={() => setUpdateOpen(true)}><RefreshCw size={15} /> Update</Button>
          </div>
        )}

        {/* What an update or reinstall could not take out of the gateway's process, stated
            until a restart does — the toast that first said it is gone by now. */}
        {app.restartReason && (
          <div role="status" className="rounded-md border border-outline-variant bg-surface-high p-m" data-type="body-s">
            <div className="flex items-center gap-2 text-on-surface"><RotateCw size={14} /> Restart the gateway to finish</div>
            <div className="mt-1 text-on-surface-low" data-type="label-s">
              {`The installed version is running, but ${app.restartReason}. Restart it from System status, top right.`}
            </div>
          </div>
        )}

        <PermissionList perms={app.permissions} hostUi={consentHostUi(app)} />

        {app.hasBackend && (
          <div className="rounded-md border border-outline-variant bg-surface-high p-m" data-type="body-s">
            <div className="flex items-center gap-2 text-on-surface"><Server size={14} /> Backend</div>
            <div className="mt-1 text-on-surface-low">
              {app.backendRunning ? `running on port ${app.backendPort}` : 'not running'}
            </div>
          </div>
        )}

        {app.hasUI && app.enabled && (
          <label className="flex items-center justify-between gap-3 rounded-md border border-outline-variant bg-surface-high p-m">
            <span className="min-w-0">
              <span className="flex items-center gap-2 text-on-surface" data-type="body-s"><LayoutGrid size={14} /> Show in navigation</span>
              <span className="mt-0.5 block text-on-surface-low" data-type="label-s">Pin this app's page to the Apps section of the nav rail.</span>
            </span>
            <button type="button" role="switch" aria-checked={inNav} aria-label="Show in navigation" onClick={toggleNav}
              className={`h-6 w-11 shrink-0 rounded-pill transition-colors ${inNav ? 'bg-primary' : 'bg-surface-highest'}`}>
              <span className={`block size-5 rounded-full bg-white transition-transform ${inNav ? 'translate-x-5' : 'translate-x-0.5'}`} />
            </button>
          </label>
        )}

        {/* A NATIVE app (the always-on filesystem/shell bundle, the native entity
            providers, the MCP/OpenAI adapters, seeded natives) ships with the baseline —
            no install/uninstall lifecycle. Always-on notice; "Configure" only when it
            has settings (hasConfig) — a config-less native provider is managed from the
            Tools page. */}
        {app.native ? (
          <>
            <div className="rounded-md border border-outline-variant bg-surface-high p-m" data-type="body-s">
              <div className="flex items-center gap-2 text-on-surface"><Power size={14} /> Native app — always on</div>
              <div className="mt-1 text-on-surface-low" data-type="label-s">
                {app.configuredPerInstance
                  ? "Ships with PersonalClaw as part of the baseline; it can't be deactivated or disabled. Its settings live on each of its instances, which you add, edit, test and remove in Settings → Providers."
                  : app.hasConfig
                    ? "Ships with PersonalClaw as part of the baseline; it can't be deactivated or disabled. You can change its settings below."
                    : "Ships with PersonalClaw as part of the baseline; it can't be deactivated or disabled. Manage its individual tools from the Tools page."}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {app.hasUI && app.enabled && (
                <Button variant="primary" size="sm" onClick={onOpen}><LayoutGrid size={15} /> Open</Button>
              )}
              {app.configuredPerInstance
                ? <Button variant="ghost" size="sm" onClick={onManageInstances}><Settings2 size={15} /> Manage instances</Button>
                : app.hasConfig && <Button variant="ghost" size="sm" onClick={() => setConfigOpen(true)}><Settings2 size={15} /> Configure</Button>}
              <Button variant="ghost" size="sm" onClick={() => setUpdateOpen(true)}><RefreshCw size={15} /> Update</Button>
            </div>
          </>
        ) : (<>
          {/* ONE vocabulary for the state toggle: Activate / Deactivate, on every
              surface (card, menus, this panel). "Install" is reserved for a real
              store download — a deactivated app's files never left disk, so
              offering "Install" here promised a fetch that would not happen. */}
          <div className="flex flex-wrap gap-2">
            {app.hasUI && app.enabled && (
              <Button variant="primary" size="sm" onClick={onOpen}><LayoutGrid size={15} /> Open</Button>
            )}
            <Button variant={app.enabled ? 'secondary' : 'primary'} size="sm" disabled={busy} disabledReason={BUSY_REASON} onClick={toggle}>
              <Power size={15} /> {app.enabled ? 'Deactivate' : 'Activate'}
            </Button>
            {app.enabled && (app.configuredPerInstance
              ? <Button variant="ghost" size="sm" onClick={onManageInstances}><Settings2 size={15} /> Manage instances</Button>
              : <Button variant="ghost" size="sm" onClick={() => setConfigOpen(true)}><Settings2 size={15} /> Configure</Button>)}
            <Button variant="ghost" size="sm" onClick={() => setUpdateOpen(true)}><RefreshCw size={15} /> Update</Button>
            {/* The middle removal rung, and a REAL control at last: the force-uninstall
                dialog has always told users to "use Uninstall instead", and until issue
                2541 there was no such button anywhere in this panel — the one screen
                warning about data loss pointed at nothing. Files go, data/ stays. */}
            <Button variant="ghost" size="sm" onClick={() => setConfirmRemove(true)}><Archive size={15} /> Uninstall</Button>
          </div>

          {/* Advanced → the destructive force-uninstall (removes files AND the user's
              data/). Hidden behind an expander so it's deliberate, not accidental. */}
          <div className="border-t border-outline-variant/40 pt-3">
            <button type="button" onClick={() => setAdvancedOpen((o) => !o)} aria-expanded={advancedOpen}
              className="flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] transition-colors hover:text-on-surface">
              <ChevronDown size={14} className="transition-transform" style={{ transform: advancedOpen ? 'rotate(180deg)' : 'none' }} /> Advanced
            </button>
            {advancedOpen && (
              <div className="mt-2 rounded-md border border-outline-variant bg-surface-high p-m">
                <div data-type="body-s" className="text-on-surface">Force uninstall</div>
                <div data-type="label-s" className="mt-0.5 text-on-surface-low">
                  Remove this app's files <span className="text-on-surface">and everything it stored for you</span> — notes, history,
                  logs, saved credentials. Deactivate keeps the files; Uninstall removes them and keeps your data, but not its saved credentials. This can't be undone.
                </div>
                <Button variant="danger" size="sm" className="mt-2" onClick={() => setConfirmUninstall(true)}>
                  <Trash2 size={15} /> Force uninstall
                </Button>
              </div>
            )}
          </div>
        </>)}
      </div>

      {updateOpen && <UpdateModal name={app.name} displayName={app.displayName} found={foundUpdate(app)}
        onClose={() => setUpdateOpen(false)} onUpdated={() => { setUpdateOpen(false); onChanged() }} />}
      {configOpen && <ConfigModal name={app.name} displayName={app.displayName} onClose={() => setConfigOpen(false)} />}
      {confirmRemove && <RemoveAppModal name={app.name} displayName={app.displayName}
        onClose={() => setConfirmRemove(false)}
        onDone={() => { setConfirmRemove(false); onClose(); onChanged() }} />}
      {confirmUninstall && <UninstallModal name={app.name} displayName={app.displayName}
        onClose={() => setConfirmUninstall(false)}
        onDone={() => { setConfirmUninstall(false); onClose(); onChanged() }} />}
    </>
  )
}

// ── Store detail panel — the not-yet-installed side of a card click. Shows the
// hero/icon + metadata, what the app gets, and an Install that opens the SAME consent
// dialog as the card, so the panel is a full parallel to AppDetailPanel for uninstalled
// catalog entries. */
function StoreDetailPanel({ item, onInstalled }: { item: StoreItem; onInstalled: (name: string) => void }) {
  const providerLabel = item.isProvider
    ? `${PROVIDER_ENTITY_LABEL[item.providerType] ?? item.providerType} provider` : ''
  // A registry-indexed (P20) item installs from its `pointer` (repo[#subdirectory]); a
  // dir-scanned/bundled item from its `source`. Both are reviewed before anything installs.
  const install = useAppInstall({ onInstalled: (r) => onInstalled(r.name) })
  const disclosure = disclosureOf(item)

  return (
    <div className="flex flex-col gap-l p-l">
      {/* Hero banner — genuinely the same two-path treatment as the card: the app's own
          image when it ships one, its deterministic token gradient when it does not. The
          card and this panel are one continuous gesture (click card → panel), so a banner
          that vanished for hero-less apps re-created the two-shapes defect the card fixed. */}
      <div className="relative -mx-l -mt-l h-36 shrink-0 overflow-hidden bg-surface-high"
        data-art={item.heroUrl ? 'hero' : 'generated'}
        style={item.heroUrl ? undefined : { background: artGradient(item.name) }}>
        {item.heroUrl && <img src={item.heroUrl} alt="" className="size-full object-cover" />}
        <div className="absolute inset-0 bg-gradient-to-t from-surface/60 to-transparent" />
      </div>
      <div>
        {/* Same renderer as the installed panel above: the pre-install and post-install
            descriptions are the same authored field, so they cannot read differently. */}
        <div data-type="body-s"><Markdown>{item.description || item.name}</Markdown></div>
        <div data-type="label-s" className="mt-1 text-on-surface-low">
          v{item.version || '—'}{item.author ? ` · by ${item.author}` : ''}
        </div>
        {providerLabel && (
          <span className="mt-2 inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5" data-type="label-s"
            style={accentChip}>
            <Plug size={11} />{providerLabel}
          </span>
        )}
      </div>

      {/* APE-4: the declared quality bar, shown BEFORE install alongside the
          permissions/crons consent surface — it is part of what you are choosing. */}
      <QualityBadges quality={item.quality} />

      {(item.tags ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {(item.tags ?? []).map((t) => (
            <span key={t} className="inline-flex h-6 items-center rounded-pill bg-surface-high px-2 text-on-surface-var text-[0.75rem]">{t}</span>
          ))}
        </div>
      )}

      {/* P29: what this app will be GRANTED and what it will RUN, BEFORE install. Keyed on
          consentKnown through `disclosureOf`, not on permissions-emptiness (issue 614): a
          scanned manifest that declares nothing gets PermissionList's own "None — no gateway
          capability" disclosure, while a registry pointer — whose manifest is read when the
          install is reviewed — says so rather than pretending it asks for nothing. */}
      {disclosure ? (
        <AppDisclosureView disclosure={disclosure} action="install" />
      ) : (
        <div data-type="body-s" className="text-on-surface-low">
          Permissions: not known yet — this is a registry listing, and its manifest is read when
          you choose Install. You will see everything it gets before anything is installed.
        </div>
      )}

      <div className="rounded-md border border-outline-variant bg-surface-high p-m" data-type="body-s">
        <div className="flex items-center gap-2 text-on-surface"><Download size={14} /> Not installed</div>
        <div className="mt-1 text-on-surface-low" data-type="label-s">
          Installing shows you what this app gets and what the security scanner found first — nothing
          installs until you confirm, and a dangerous verdict is always refused.
        </div>
      </div>

      <div>
        <Button variant="primary" size="sm"
          onClick={() => install.begin({ source: item.pointer || item.source, label: item.displayName })}>
          <Download size={15} /> Install
        </Button>
      </div>
      {install.dialog}
    </div>
  )
}





function ConfigModal({ name, displayName, onClose }: {
  /** The app SLUG — the config API's identifier. Never the title: `weather-forecast` is a
   *  path segment, and every other surface in this page already says “Weather Forecast”. */
  name: string
  displayName: string
  onClose: () => void
}) {
  const cfg = useAppConfig(name)

  return (
    <Modal title={`Configure ${displayName}`} icon={<Settings2 size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 440 }}>
        {cfg.error ? (
          // A failed read used to leave "Loading…" on screen forever, with Save still live over an
          // empty form. Say what happened and offer the retry the hook now exposes.
          <LoadError what="app configuration" error={cfg.error} onRetry={cfg.reload} />
        ) : cfg.loading ? <div data-type="body-s" className="text-on-surface-low">Loading…</div>
          : !cfg.hasSchema ? (
            <div data-type="body-s" className="text-on-surface-low">This app declares no configurable options.</div>
          ) : (
            <AppConfigFields appName={name} props={cfg.props} cur={cfg.cur} set={cfg.set} secretSet={cfg.secretSet} required={cfg.required} />
          )}
        {/* 🔴 The one with a data cost. `cfg.err` is `appConfigForm`'s save guard, which exists because
            the backend's `write_config` REPLACES the file — so a save from a form that never loaded
            would erase this app's stored config, secrets included. Its refusal ("…Retry the load
            first.") was the only evidence the click did nothing, and it rendered in `text-negative`:
            a class Tailwind compiles to NOTHING, so it inherited `--color-on-surface` — the app's
            primary body ink, indistinguishable from a hint — with no live region either. */}
        {cfg.err && <FieldError>{cfg.err}</FieldError>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {/* Save stays out of reach until the config it would REPLACE has actually loaded — the
              footer sits outside the branch above, so this button was clickable during the load. */}
          <Button variant="primary" disabled={cfg.busy || cfg.loading || !!cfg.error || cfg.missing.length > 0}
            disabledReason={cfg.error ? 'The configuration failed to load'
              : cfg.loading ? 'Still loading the configuration'
              // #491: naming the LABELS is the fix — the old feedback was a server 400 quoting the
              // schema key, which the user then had to map to a form row themselves.
              : cfg.missing.length > 0 ? `Fill in ${cfg.missingLabels.join(', ')}` : undefined}
            onClick={() => cfg.save(onClose)}>Save</Button>
        </div>
      </div>
    </Modal>
  )
}

/** The kept-dependency list both removal dialogs show. Identical either way: the
 *  dependency ledger's answer does not depend on which rung removed the app. */
function KeptDepsList({ kept }: { kept: AppDepClassification[] }) {
  if (kept.length === 0) return null
  return (
    <ul className="flex flex-col gap-1">
      {kept.map((d) => (
        <li key={d.key} data-type="body-s" className="text-on-surface-low">
          • Keeping {d.kind} <span className="text-on-surface">{d.id}</span> ({d.disposition})
        </li>
      ))}
    </ul>
  )
}

/** The MIDDLE removal rung (issue #2541): the app's files go, the user's `data/`
 *  stays and comes back if they reinstall.
 *
 *  It states which of the three data facts applies rather than one hedged sentence,
 *  because "we kept your 4 notes" and "this app had nothing stored" and "it had a
 *  data folder and it was empty" are three different promises, and a dialog that
 *  makes the same one in all three cases is wrong in two of them. */
export function RemoveAppModal({ name, displayName, onClose, onDone }: { name: string; displayName: string; onClose: () => void; onDone: () => void }) {
  const { data } = useQuery(`app-uninstall:${name}`, () => api.appUninstallPreview(name), { persist: false })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const kept = (data?.dependencies ?? []).filter((d) => d.disposition !== 'removable')
  const facts = data?.data
  // Issue 2585. An earlier copy of this app's data/ still on disk makes the backend REFUSE
  // — fail-closed, so it does not guess which copy the user wants kept. The endpoint
  // renders that refusal as `404 app not installed`, so pressing Uninstall would produce a
  // message that is false and tells the user nothing about the data it just protected.
  // Stated here, before the click, with the paths. `?? []` is safe for the gate (an older
  // gateway omitting the key leaves the button enabled and the backend still refuses
  // safely); it must not be read as a positive "there are none".
  const unconsumed = facts?.unconsumed ?? []
  const blocked = unconsumed.length > 0

  async function remove() {
    setBusy(true)
    setErr('')
    // 🔑 REPORTED INSIDE THE DIALOG, and the dialog deliberately STAYS OPEN. This was a bare
    // `try { … } finally { setBusy(false) }`: a refused uninstall rejected unhandled, so the
    // spinner stopped, the dialog sat there, `onDone()` never ran and nothing said why — the
    // #3540 symptom on the uninstall path. And the refusal it hides is one this very dialog
    // documents above: the backend fails CLOSED on an earlier data/ copy and renders that as
    // `404 app not installed`, which a user can only act on if they are shown it.
    // Not a toast: the eye is on the dialog, and `refusedWriteVisible`'s ruling is that a
    // surface with a place to put the sentence puts it there. Not closing either — closing is
    // this dialog's success signal, and the app is still installed.
    try { await api.removeApp(name); onDone() }
    catch (e) { setErr(readableErrText(e) || 'That uninstall did not go through, and the app is still installed.') }
    finally { setBusy(false) }
  }

  return (
    <Modal title={`Uninstall ${displayName}?`} icon={<Archive size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 400 }}>
        <div data-type="body-s" className="text-on-surface-low">
          This removes the app's files and providers from disk. To just turn it off and leave the
          files in place, use <span className="text-on-surface">Deactivate</span> instead.
        </div>
        {blocked && (
          <div role="alert" className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/5 p-m">
            <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" />
            <div data-type="body-s" className="min-w-0 text-on-surface-low">
              <span className="font-medium">An earlier copy of this app's data is still here.</span>{' '}
              Uninstalling would have to overwrite or delete it, so it is refused until you decide
              what to keep. Move {unconsumed.length === 1 ? 'it' : 'them'} somewhere else (or delete
              {unconsumed.length === 1 ? ' it' : ' them'}, if you already have what you need), then
              try again. <span className="text-on-surface">Force uninstall</span> deletes
              {unconsumed.length === 1 ? ' it' : ' them'} deliberately.
              {unconsumed.map((p) => (
                <div key={p} data-type="label-s" className="mt-1 break-all opacity-80">{p}</div>
              ))}
            </div>
          </div>
        )}
        {/* `present` and `entries` are separate facts — see AppDataFacts. Absent data/
            and empty data/ get different sentences on purpose. */}
        <div className="flex items-start gap-2 rounded-md border border-outline-variant bg-surface-high p-m">
          <HardDrive size={15} className="mt-0.5 shrink-0 text-on-surface-low" />
          <div data-type="body-s" className="min-w-0 text-on-surface-low">
            {facts === undefined
              ? 'Checking what this app has stored…'
              : !facts.present
                ? <>This app keeps no data of its own, so there is nothing to preserve.</>
                : facts.entries === 0
                  ? <>This app has a data folder and it is currently <span className="text-on-surface">empty</span>. It is kept anyway, so reinstalling picks up where you left off.</>
                  : <><span className="text-on-surface">Your data is kept</span> — {facts.entries} {facts.entries === 1 ? 'item' : 'items'} in this app's data folder. Reinstall it and your data comes back.</>}
            {facts?.present && facts.path && (
              <div data-type="label-s" className="mt-1 break-all text-on-surface-low/80">Kept at {facts.path}</div>
            )}
          </div>
        </div>
        {/* The data is kept; the credentials are NOT. Both removal rungs delete what the app keeps
            in the credential store (`app_manager.force_uninstall` → `secret_refs.purge`), so
            "your data is kept" must not be read as "your tokens are kept". Stated only when the
            preview counted some — a count of names, no value on the wire. */}
        {!!facts?.secrets && (
          <div className="flex items-start gap-s rounded-md border border-outline-variant bg-surface-high p-m">
            <KeyRound size={15} className="mt-0.5 shrink-0 text-on-surface-low" />
            <div data-type="body-s" className="min-w-0 text-on-surface-low">
              <span className="text-on-surface">{facts.secrets === 1 ? 'Its saved credential is deleted' : `Its ${facts.secrets} saved credentials are deleted`}</span>
              {' '}— the tokens and keys entered in its settings. Reinstall it and you enter {facts.secrets === 1 ? 'it' : 'them'} again.
            </div>
          </div>
        )}
        <KeptDepsList kept={kept} />
        {err && <FieldError>{err}</FieldError>}
        <div className="flex justify-end gap-2">
          {/* Cancel first in tab order — the safe option gets the focus, not the one
              that removes things.
              `loading`, not a hand-rolled Loader2 swap: the primitive carries aria-busy,
              so a screen reader hears the in-flight state instead of watching a silent
              spin. (The force dialog below still hand-rolls its own; converting the
              existing population is a separate visual call — see
              ui/transientStateAnnouncement.test.tsx.) */}
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {/* `disabledReason`, not `title`: this gate is a state the user CAN fix, so the
              primitive swaps native `disabled` for `aria-disabled` and keeps the tab stop —
              a keyboard user can land on the button and hear why. A wrapper title, or a
              bare `title` on a natively-disabled button, is unreachable for exactly the
              reader it was written for (see ui/disabledReasonTriage.test.ts). */}
          <Button
            variant="primary"
            loading={busy}
            disabled={blocked}
            disabledReason={blocked ? 'An earlier copy of this app’s data is still on disk — resolve it first' : undefined}
            onClick={remove}
          >
            <Archive size={16} /> Uninstall
          </Button>
        </div>
      </div>
    </Modal>
  )
}

export function UninstallModal({ name, displayName, onClose, onDone }: { name: string; displayName: string; onClose: () => void; onDone: () => void }) {
  const { data } = useQuery(`app-uninstall:${name}`, () => api.appUninstallPreview(name), { persist: false })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const deps: AppDepClassification[] = data?.dependencies ?? []
  const kept = deps.filter((d) => d.disposition !== 'removable')
  const facts = data?.data

  async function forceUninstall() {
    setBusy(true)
    setErr('')
    // Same fix and same reasoning as `RemoveAppModal.remove()` above — and it matters more here,
    // because this dialog's own copy promises "it cannot be undone": a silent failure leaves the
    // user unable to tell a deletion that happened from one that did not.
    try { await api.uninstallApp(name, true); onDone() }  // force=true → delete files
    catch (e) { setErr(readableErrText(e) || 'That force uninstall did not go through, and nothing was deleted.') }
    finally { setBusy(false) }
  }

  return (
    <Modal title={`Force uninstall ${displayName}?`} icon={<Trash2 size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 400 }}>
        {/* This paragraph used to end "use Uninstall instead" while no Uninstall
            control existed anywhere in the Library — the one screen warning a user
            about data loss sent them to a button that was not there, and it described
            Deactivate's behaviour under Uninstall's name. Both lesser rungs are real
            now, so both are named, each by what it actually does. */}
        <div data-type="body-s" className="text-on-surface-low">
          This permanently removes the app's files and providers from disk
          {facts?.present && facts.entries > 0
            ? <>, <span className="text-danger">including the {facts.entries} {facts.entries === 1 ? 'item' : 'items'} it stored for you</span></>
            : <> and anything it stored for you</>}
          {facts?.secrets
            ? <>, and <span className="text-danger">{facts.secrets === 1 ? 'the credential' : `the ${facts.secrets} credentials`} saved for it</span></>
            : null}
          {' '}— it cannot be undone. To keep your data, use <span className="text-on-surface">Uninstall</span>;
          to just turn the app off and leave everything on disk, use <span className="text-on-surface">Deactivate</span>.
          {kept.length > 0 && ' Shared dependencies still used by other apps will be kept.'}
        </div>
        <KeptDepsList kept={kept} />
        {err && <FieldError>{err}</FieldError>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={busy} onClick={forceUninstall}><Trash2 size={16} /> Force uninstall
          </Button>
        </div>
      </div>
    </Modal>
  )
}
