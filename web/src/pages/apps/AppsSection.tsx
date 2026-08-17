import { useMemo, useRef, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { accentChip } from '../../design/accent'
import { motion } from 'framer-motion'
import {
  Blocks, Plus, Download, Loader2, Power, Trash2, Settings2, FolderOpen,
  ShieldAlert, ShieldCheck, Server, LayoutGrid, RefreshCw, Plug, ChevronDown,
  MoreVertical, Database, Sparkles,
} from 'lucide-react'
import { launchChat } from '../../app/appSdk'
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
import { RowHitTarget } from '../../ui/RowHitTarget'
import { TextInput } from '../../ui/forms'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Segmented } from '../../ui/Segmented'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import {
  api, type AppSummary, type AppDepClassification, type AppCatalogEntry,
} from '../../lib/api'
import {
  useGuardedInstall, guardedFromApp, isBlockingResult, terminalRefusalReason, type GuardedResult,
} from '../../lib/useGuardedInstall'
import { AppIcon } from './appIcon'
import { AppConfigFields, useAppConfig } from './appConfigForm'
import { isInNav, setInNav } from './navApps'
import { PageTitle } from '../../ui/PageTitle'
// The install-consent surface is shared with the first-run essential-apps step.
import { ScanReport, ConsentModal, PermissionList, CronConsentList } from './installConsent'

// ── Store item: the Store lists EVERY app it knows about — the available-to-
// install catalog entries UNION the already-installed apps — so it never reads
// as "empty" just because everything is installed. Both shapes normalize to this
// one row (catalog fields + an `installed`/`enabled`/`hasUI` overlay). Installed
// items render an Installed/Open affordance; available ones render Install. */
interface StoreItem extends AppCatalogEntry {
  installed: boolean
  enabled: boolean
  hasUI: boolean
  /** A native app — always-on, locked (configure-only if it has settings, else
   *  managed elsewhere; never uninstall/disable). The SINGLE app-category flag;
   *  first-party/third-party apps have native=false. */
  native?: boolean
  /** Has a settings surface — drives whether a native app shows "Configure". */
  hasConfig?: boolean
  /** Provenance for the source divider: builtin/registry origin → "Built-in";
   *  a git URL or a local path → that source. */
  origin?: string
  /** APE-7: the app's source offers a newer version — card shows an "Update" badge. */
  updateAvailable?: boolean
  /** APE-7: that newer version (for the badge tooltip). */
  latestVersion?: string
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
    native: !!a.native, hasConfig: a.hasConfig, origin: a.origin,
    updateAvailable: !!a.updateAvailable, latestVersion: a.latestVersion,
  }
}

const GIT_URL_RE = /^(https?:\/\/|git@|git:\/\/|ssh:\/\/)/

/** The source a StoreItem belongs to, for the divider grouping. Returns a stable
 *  `key` (grouping/sort) + a human `label` (divider heading).
 *   • Built-in (bundled/registry origin, a bundled catalog entry, or a platform
 *     provider) → one "Built-in" group.
 *   • A git-URL source → grouped by that URL.
 *   • A local path → folded UP to the registered local source it lives under
 *     (an installed app records its own subdir, but the source the user ADDED is
 *     the parent dir), so every app from one source shares one divider. If it
 *     matches no registered source, it folds to its parent directory.
 *   • A git clone whose URL was lost (legacy temp-clone path) → "Installed from git".
 *  `localSources` is the list of registered local source roots (catalog). */
function sourceGroup(it: StoreItem, localSources: string[]): { key: string; label: string } {
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
  return { key: `local:${key}`, label: key }
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
// update / force-uninstall open their modals; open navigates to the app's page.
type AppActionKind = 'open' | 'toggle' | 'configure' | 'update' | 'force-uninstall'
type DispatchAppAction = (app: { name: string; enabled: boolean; hasUI: boolean }, action: AppActionKind) => void

/** Owns the app-action modal state + the enable/disable call, and renders the
 *  modals ONCE at the host level. Returns a `dispatch` both the cards and the
 *  detail panel call, the `busyName` (app mid-toggle), and the `modals` node. */
function useAppActions(nav: (p: string) => void, reload: () => void) {
  const [busyName, setBusyName] = useState<string | null>(null)
  const [configFor, setConfigFor] = useState<string | null>(null)
  const [updateFor, setUpdateFor] = useState<string | null>(null)
  const [uninstallFor, setUninstallFor] = useState<string | null>(null)

  const dispatch: DispatchAppAction = (app, action) => {
    switch (action) {
      case 'open': nav(`app/${encodeURIComponent(app.name)}`); return
      case 'configure': setConfigFor(app.name); return
      case 'update': setUpdateFor(app.name); return
      case 'force-uninstall': setUninstallFor(app.name); return
      case 'toggle': {
        setBusyName(app.name)
        const p = app.enabled ? api.disableApp(app.name) : api.enableApp(app.name)
        p.then(() => reload()).finally(() => setBusyName(null))
        return
      }
    }
  }

  const modals = (
    <>
      {updateFor && <UpdateModal name={updateFor} onClose={() => setUpdateFor(null)}
        onUpdated={() => { setUpdateFor(null); reload() }} />}
      {configFor && <ConfigModal name={configFor} onClose={() => setConfigFor(null)} />}
      {uninstallFor && <UninstallModal name={uninstallFor} onClose={() => setUninstallFor(null)}
        onDone={() => { setUninstallFor(null); reload() }} />}
    </>
  )
  return { dispatch, busyName, modals }
}

/** The per-app "⋯" action menu (kebab) — the REAL actions, shared by cards + the
 *  detail panel. Enable/disable, configure, update, open, force-uninstall; a
 *  platform provider shows only "Open page" (it has no install lifecycle). */
function AppActionMenu({ item, onAction }: { item: StoreItem; onAction: DispatchAppAction }) {
  const app = { name: item.name, enabled: item.enabled, hasUI: item.hasUI }
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
              {item.hasConfig
                ? <MenuRow icon={<Settings2 size={15} />} label="Configure" onClick={() => { onAction(app, 'configure'); close() }} />
                : <div className="px-m py-2 text-on-surface-low text-[0.75rem]">Always on — manage its tools from the Tools page.</div>}
              <MenuRow icon={<RefreshCw size={15} />} label="Update…" onClick={() => { onAction(app, 'update'); close() }} />
              <div className="px-m py-1.5 text-on-surface-low text-[0.75rem]">Native app — always on, can't be uninstalled.</div>
            </>
          ) : (
            <>
              {item.enabled && <MenuRow icon={<Settings2 size={15} />} label="Configure" onClick={() => { onAction(app, 'configure'); close() }} />}
              <MenuRow icon={<RefreshCw size={15} />} label="Update…" onClick={() => { onAction(app, 'update'); close() }} />
              <MenuRow icon={<Power size={15} />} label={item.enabled ? 'Uninstall (deactivate)' : 'Install (activate)'} onClick={() => { onAction(app, 'toggle'); close() }} />
              <div className="my-1 border-t border-outline-variant/30" />
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

function matchesText(haystack: string, q: string): boolean {
  return !q || haystack.toLowerCase().includes(q)
}

export function AppsSection({ query, setQuery, navigate }: Pick<RouteProps, 'query' | 'setQuery' | 'navigate'>) {
  const q = query
  const sq = setQuery
  const nav = navigate
  // No `.catch(() => [])`. An empty list is a CLAIM ("you have no apps"), and this surface makes it
  // out loud: measured against a 500 on `/api/apps*` with a cold sessionStorage, the Library rendered
  // "No apps installed — Browse the Store to add apps" plus a Browse Store CTA, with no error text
  // anywhere and no live region. Letting the rejection through is what makes `error` exist.
  const { data: apps, error: appsErr, refresh } = useCachedData<AppSummary[]>(
    'apps', () => api.apps(), { persist: true },
  )
  // Store catalog is lifted here (was inside StoreView) so the shared, pinned
  // controls bar can host the Store's search + Filter&sort too — same idiom as
  // the Library, instead of a second control bar that scrolls with the body.
  const { data: catalog, error: catalogErr, refresh: refreshCatalog } = useCachedData(
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

  const reload = () => { invalidateCache('apps'); invalidateCache('app-catalog'); refresh(); refreshCatalog() }
  const reloadCatalog = () => { invalidateCache('app-catalog'); refreshCatalog() }
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
  const bundled = catalog?.bundled ?? []
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
    // multi-app repos (gitApps) — the union of every installable app the catalog surfaced.
    // remoteApps/gitApps carry a `pointer` (repo[#sub]) that install uses instead of source.
    const available = [...bundled, ...(catalog?.localApps ?? []), ...(catalog?.remoteApps ?? []), ...(catalog?.gitApps ?? [])]
    for (const e of available) {
      if (installedNames.has(e.name) || byName.has(e.name)) continue
      byName.set(e.name, { ...e, installed: false, enabled: false, hasUI: false, native: false })
    }
    return [...byName.values()]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apps, catalog])
  const storeResult = useMemo(() => {
    let out = storeUniverse.filter((e) =>
      matchesText(`${e.displayName} ${e.name} ${e.description} ${e.author} ${(e.tags ?? []).join(' ')}`, n))
    if (storeType !== 'all') out = out.filter((e) => (storeType === 'provider') === e.isProvider)
    if (storeEntity !== 'all') out = out.filter((e) => e.isProvider && e.providerType === storeEntity)
    if (storeTag !== 'all') out = out.filter((e) => (e.tags ?? []).includes(storeTag))
    const byName = (a: StoreItem, b: StoreItem) => a.displayName.localeCompare(b.displayName)
    out = [...out].sort((a, b) => {
      switch (storeSort as StoreSortKey) {
        case 'author': return (a.author || '').localeCompare(b.author || '') || byName(a, b)
        case 'type': return Number(b.isProvider) - Number(a.isProvider) || byName(a, b)
        default: return byName(a, b)
      }
    })
    return out
  }, [storeUniverse, n, storeType, storeEntity, storeTag, storeSort])

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
    const tags = tagOptions(storeUniverse.map((e) => e.tags ?? []))
    if (tags.length) s.push({ title: 'Tags', value: storeTag, defaultKey: 'all', onChange: setStoreTag,
      options: [{ key: 'all', label: 'All tags' }, ...tags] })
    return s
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeUniverse, storeSort, storeType, storeEntity, storeTag])

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
              <PageTitle>Apps</PageTitle>
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
                  the three in a popover — roles and arrow-key behaviour are untouched. */}
              <Segmented ariaLabel="Native, Library, or Store" collapse="menu" value={view} onChange={setView}
                options={[{ key: 'native', label: 'Native' }, { key: 'library', label: 'Library' }, { key: 'store', label: 'Store' }]} />
            </div>
          }
          right={<HeaderActions>
            {isStore && <HeaderControl icon={Database} label="Manage Sources" priority="default" onClick={() => setSourcesOpen(true)} />}
            <HeaderControl icon={Plus} label="Install from URL" variant="primary" priority="primary" onClick={() => setInstalling(true)} />
          </HeaderActions>}
        />}
        controls={showLibControls ? (
          <ListControls search={{ value: search, onChange: setSearch, placeholder: 'Search installed apps', label: 'Search apps' }}>
            <FilterMenu sections={libSections} label="Filter & sort" />
          </ListControls>
        ) : showStoreControls ? (
          <ListControls search={{ value: search, onChange: setSearch, placeholder: 'Search the Store', label: 'Search Store' }}>
            <FilterMenu sections={storeSections} label="Filter & sort" />
          </ListControls>
        ) : undefined}
        panel={(open || openStore) ? (
          <SidePanel key={(open ?? openStore)!.name} fillHeight storeKey="app-panel-w"
            title={(open ?? openStore)!.displayName} icon={<AppIcon name={(open ?? openStore)!.icon} size={18} />}
            onClose={() => setOpenName('')}>
            {open ? (
              <AppDetailPanel app={open} onClose={() => setOpenName('')} onChanged={reload}
                onOpen={() => nav(`app/${encodeURIComponent(open.name)}`)} />
            ) : (
              <StoreDetailPanel item={openStore!} onInstalled={() => { setOpenName(''); reload() }} />
            )}
          </SidePanel>
        ) : sourcesOpen && (
          <SidePanel key="sources" fillHeight storeKey="app-sources-panel-w"
            title="Manage Sources" icon={<Database size={18} />}
            onClose={() => setSourcesOpen(false)}>
            <SourcesPanel catalog={catalog} reloadCatalog={reloadCatalog} onInstalled={reload} />
          </SidePanel>
        )}
      >
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {isStore ? (
            <StoreView catalog={catalog} catalogError={catalogErr} result={storeResult} totalKnown={storeUniverse.length}
              installedCount={(apps ?? []).filter((a) => !a.native).length}
              onInstalled={reload} reloadCatalog={reloadCatalog} onClearFilters={clearStoreFilters(setSearch, setStoreType, setStoreEntity, setStoreTag)}
              filtersActive={!!n || storeType !== 'all' || storeEntity !== 'all' || storeTag !== 'all'}
              onOpen={(name) => setOpenName(name)} onAction={appActions.dispatch}
              onOpenSources={() => setSourcesOpen(true)} />
          ) : apps === undefined && appsErr ? (
            // Before the skeleton branch, or a failed fetch spins it forever.
            <LoadError what="apps" error={appsErr} onRetry={reload} />
          ) : apps === undefined ? <ListSkeleton rows={4} what="apps" />
            : libResult && libResult.length === 0 ? (
              // Empty state, tab-aware: Native (should never be empty in practice —
              // native apps always ship), Library (no user-installed apps), each
              // honoring an active search/filter.
              (!n && libStatus === 'all' && libType === 'all' && libCap === 'all' && libEntity === 'all') ? (
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
                          <AppCard key={it.name} item={it} index={i} busy={false}
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

      {installing && <InstallModal onClose={() => setInstalling(false)} onInstalled={() => { setInstalling(false); reload() }} />}
    </>
  )
}

/** Curried "reset every Store filter" used by the no-match empty state. */
function clearStoreFilters(
  setSearch: (v: string) => void, setType: (v: string) => void,
  setEntity: (v: string) => void, setTag: (v: string) => void,
): () => void {
  return () => { setSearch(''); setType('all'); setEntity('all'); setTag('all') }
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
 *  the always-shown source sections. */
function StoreView({ catalog, catalogError, result, totalKnown, installedCount, onInstalled, reloadCatalog, onClearFilters, filtersActive, onOpen, onAction, onOpenSources }: {
  catalog: { bundled: AppCatalogEntry[]; gitSources: string[]; localSources?: string[]; firstPartySources?: string[]; localApps?: AppCatalogEntry[]; remoteApps?: AppCatalogEntry[]; gitApps?: AppCatalogEntry[] } | null | undefined
  /** The catalog fetch's rejection. A Store that cannot reach its catalog must say so rather than
   *  render as an empty shelf — "nothing to install" and "we could not ask" are different answers. */
  catalogError?: unknown
  result: StoreItem[]
  totalKnown: number
  // installed non-native apps — the ones that LEFT the Store for the Library. Lets the
  // empty state say "all installed" (they're in the Library) vs "nothing discovered".
  installedCount: number
  onInstalled: () => void
  reloadCatalog: () => void
  onClearFilters: () => void
  filtersActive: boolean
  onOpen: (name: string) => void
  onAction: DispatchAppAction
  onOpenSources: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  // The install currently held at the consent gate: a warning-verdict app that
  // needs "Install anyway". Card/source-list installs route through the SAME
  // guarded state machine as the modal, so a warning surfaces its findings +
  // consent action instead of dead-ending on a bare error string.
  const [pending, setPending] = useState<{ source: string; label: string } | null>(null)
  const guarded = useGuardedInstall((confirm) =>
    api.installApp(pendingRef.current?.source ?? '', confirm).then(guardedFromApp))
  const pendingRef = useRef<{ source: string; label: string } | null>(null)

  async function installFrom(source: string, label: string) {
    setBusy(label); guarded.reset()
    pendingRef.current = { source, label }
    const r = await guarded.install()
    setBusy(null)
    if (r?.ok) { onInstalled(); reloadCatalog(); return }
    // A consentable warning, a terminal refusal (dangerous content or an invalid
    // signature), OR a P21 client-install directive opens the panel; a plain error
    // already surfaced via `guarded.error`.
    if (isBlockingResult(r)) setPending({ source, label })
  }

  async function confirmPending() {
    const r = await guarded.confirmInstall()
    if (r?.ok) { setPending(null); onInstalled(); reloadCatalog() }
  }

  if (catalog === undefined && catalogError) {
    return <LoadError what="the Store catalog" error={catalogError} onRetry={reloadCatalog} />
  }
  if (catalog === undefined) return <ListSkeleton rows={3} what="the Store catalog" />

  return (
    <div className="flex flex-col gap-2xl">
      {guarded.error && (
        <div className="flex items-center justify-between gap-3">
          <div data-type="body-s" className="text-negative">{guarded.error}</div>
          <FixWithAiButton fixPrompt={guarded.fixPrompt} />
        </div>
      )}
      {pending && guarded.blocked && (
        <ConsentModal
          label={pending.label}
          result={guarded.blocked}
          busy={guarded.busy}
          onConfirm={confirmPending}
          onClose={() => { setPending(null); guarded.reset() }}
        />
      )}

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
                  <AppCard key={e.name} item={e} index={i} busy={busy === e.name}
                    onInstall={() => installFrom(e.pointer || e.source, e.name)}
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
function SourcesPanel({ catalog, reloadCatalog, onInstalled }: {
  catalog: { bundled: AppCatalogEntry[]; gitSources: string[]; localSources?: string[]; firstPartySources?: string[]; localApps?: AppCatalogEntry[]; remoteApps?: AppCatalogEntry[]; gitApps?: AppCatalogEntry[] } | null | undefined
  reloadCatalog: () => void
  onInstalled: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [newSource, setNewSource] = useState('')
  const [newLocal, setNewLocal] = useState('')
  const [pending, setPending] = useState<{ source: string; label: string } | null>(null)
  const guarded = useGuardedInstall((confirm) =>
    api.installApp(pendingRef.current?.source ?? '', confirm).then(guardedFromApp))
  const pendingRef = useRef<{ source: string; label: string } | null>(null)

  async function installFrom(source: string, label: string) {
    setBusy(label); setErr(null); guarded.reset()
    pendingRef.current = { source, label }
    const r = await guarded.install()
    setBusy(null)
    if (r?.ok) { onInstalled(); reloadCatalog(); return }
    if (isBlockingResult(r)) setPending({ source, label })
  }

  async function confirmPending() {
    const r = await guarded.confirmInstall()
    if (r?.ok) { setPending(null); onInstalled(); reloadCatalog() }
  }

  async function addSource() {
    const u = newSource.trim()
    if (!u) return
    setBusy('add-source'); setErr(null)
    try { await api.addAppSource(u); setNewSource(''); reloadCatalog() }
    catch (e) { setErr(String((e as Error).message || e)) }
    finally { setBusy(null) }
  }

  async function addLocalSource() {
    const p = newLocal.trim()
    if (!p) return
    setBusy('add-local'); setErr(null)
    try { await api.addLocalAppSource(p); setNewLocal(''); reloadCatalog() }
    catch (e) { setErr(String((e as Error).message || e)) }
    finally { setBusy(null) }
  }

  const sources = catalog?.gitSources ?? []
  const localSources = catalog?.localSources ?? []
  const firstPartySources = new Set(catalog?.firstPartySources ?? [])

  return (
    <div className="flex flex-col gap-xl">
      {err && <div data-type="body-s" className="text-negative">{err}</div>}
      {guarded.error && (
        <div className="flex items-center justify-between gap-3">
          <div data-type="body-s" className="text-negative">{guarded.error}</div>
          <FixWithAiButton fixPrompt={guarded.fixPrompt} />
        </div>
      )}
      {pending && guarded.blocked && (
        <ConsentModal
          label={pending.label}
          result={guarded.blocked}
          busy={guarded.busy}
          onConfirm={confirmPending}
          onClose={() => { setPending(null); guarded.reset() }}
        />
      )}

      <section>
        <div className="mb-2 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Git sources</div>
        <div className="mb-2 flex items-center gap-2">
          <TextInput value={newSource} onChange={setNewSource} name="app-git-source"
            placeholder="https://github.com/owner/app.git" />
          <Button variant="secondary" size="sm" disabled={busy === 'add-source' || !newSource.trim()} onClick={addSource}
            disabledReason={!newSource.trim() ? 'Enter a source URL first' : undefined}>
            <Plus size={15} /> Add
          </Button>
        </div>
        {sources.length === 0 ? (
          <div className="text-on-surface-low text-[0.8125rem]">No git sources configured. Add a git URL to discover apps from it.</div>
        ) : (
          <div className="flex flex-col gap-1">
            {sources.map((url) => (
              <div key={url} className="flex items-center gap-3 rounded-lg bg-surface-container px-l py-m">
                <Download size={15} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{url}</span>
                <Button variant="ghost" size="sm" disabled={busy === url} onClick={() => installFrom(url, url)}>
                  {busy === url ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Install
                </Button>
                <SquareIconButton icon={Trash2} tone="danger" label="Remove source" className="shrink-0"
                  onClick={async () => { await api.removeAppSource(url); reloadCatalog() }} />
              </div>
            ))}
          </div>
        )}
        <p className="mt-2 text-on-surface-low text-[0.75rem]">
          Installing fetches the app behind the security scanner — a dangerous verdict is always refused.
        </p>
      </section>

      <section>
        <div className="mb-2 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Local sources</div>
        <div className="mb-2 flex items-center gap-2">
          <TextInput value={newLocal} onChange={setNewLocal} name="app-local-source"
            placeholder="/path/to/apps  (a directory of app subdirs)" />
          <Button variant="secondary" size="sm" disabled={busy === 'add-local' || !newLocal.trim()} onClick={addLocalSource}
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
 *   • available  → "Install" (one click installs behind the scanner).
 *   • installed  → primary "Open" (→ the app's page) when it has a UI, plus a "⋯"
 *     menu of the real lifecycle actions (Configure / Update / Enable-Disable /
 *     Force-uninstall). The card's NAME is a link to the detail panel (explicit
 *     "details" affordance), so the action buttons never double as "open panel". */
function AppCard({ item, index, busy, onInstall, onOpen, onAction }: {
  item: StoreItem; index: number; busy: boolean; onInstall: () => void; onOpen: () => void; onAction: DispatchAppAction
}) {
  const providerLabel = item.isProvider
    ? `${PROVIDER_ENTITY_LABEL[item.providerType] ?? item.providerType} provider` : ''
  const app = { name: item.name, enabled: item.enabled, hasUI: item.hasUI }
  // Right-click / long-press → the SAME real actions this card dispatches. A native
  // app is always-on (no install lifecycle): omit uninstall/toggle + force-uninstall,
  // and show "Configure" only when it has settings (hasConfig) — a config-less native
  // provider (filesystem/tools) is managed from the Tools page.
  const menuItems: ContextMenuItem[] = item.installed
    ? [
        { icon: <Blocks size={15} />, label: 'Details', onSelect: onOpen },
        ...(item.hasUI && item.enabled ? [{ icon: <LayoutGrid size={15} />, label: 'Open page', onSelect: () => onAction(app, 'open') }] : []),
        ...((item.enabled && (!item.native || item.hasConfig)) ? [{ icon: <Settings2 size={15} />, label: 'Configure', onSelect: () => onAction(app, 'configure') }] : []),
        { icon: <RefreshCw size={15} />, label: 'Update…', onSelect: () => onAction(app, 'update') },
        // A native app is locked on — omit uninstall/disable + force-uninstall.
        ...(item.native ? [] : [
          { icon: <Power size={15} />, label: item.enabled ? 'Uninstall (deactivate)' : 'Install (activate)', onSelect: () => onAction(app, 'toggle') },
          { icon: <Trash2 size={15} />, label: 'Force uninstall…', onSelect: () => onAction(app, 'force-uninstall'), danger: true },
        ]),
      ]
    : [
      { icon: <Blocks size={15} />, label: 'Details', onSelect: onOpen },
      { icon: <Download size={15} />, label: 'Install', onSelect: onInstall, disabled: busy },
    ]
  // Card composition adapts to what the app declares — the four states the manifest
  // allows (hero+icon · hero-only · icon-only · neither) all read as one coherent
  // card, never a broken slot:
  //   • hero  → a full-bleed banner caps the card; the icon tile (if any) floats
  //             over its lower edge as an avatar, so hero+icon layers instead of
  //             competing, and hero-only just shows the banner.
  //   • icon-only → the tile sits inline in the header (the classic layout).
  //   • neither   → no empty tile; the title leads. (Every bundled app ships an
  //             icon today, but the card must not degrade if one is absent.)
  const hero = item.heroUrl
  const hasIcon = !!item.icon
  // Clicking the card opens the detail panel; interactive controls inside
  // (action menu, primary button) stop propagation so they act, not navigate.
  const stop = (e: React.MouseEvent) => e.stopPropagation()

  const iconTile = hasIcon && (
    <div className={`grid shrink-0 place-items-center bg-surface-high text-on-surface-low ${
      hero ? 'size-12 rounded-lg ring-2 ring-surface-container' : 'size-12 rounded-lg'}`}>
      <AppIcon name={item.icon} size={24} />
    </div>
  )

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
      className="group relative flex min-h-[11rem] cursor-pointer flex-col overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary/50"
      style={{ borderRadius: 'var(--radius-lg)' }}>
      {/* The name was carried by `title` on the wrapper before. */}
      <RowHitTarget label={`${item.displayName} — details`} />

      {/* hero banner (optional) — full-bleed cap; a subtle scrim keeps any overlaid
          icon/edge legible over a busy image */}
      {hero && (
        <div className="relative h-28 w-full shrink-0 overflow-hidden bg-surface-high">
          <img src={hero} alt="" loading="lazy"
            className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
          <div className="absolute inset-0 bg-gradient-to-t from-surface-container/50 to-transparent" />
        </div>
      )}

      <div className={`flex flex-1 flex-col gap-2.5 p-4 ${hero ? 'pt-0' : ''}`}>
        {/* header: icon (inline, or floating over the hero's edge) + name + version */}
        <div className={`flex items-start gap-3 ${hero && hasIcon ? '-mt-6' : ''}`}>
          {iconTile}
          <div className={`min-w-0 flex-1 ${hero && hasIcon ? 'pt-6' : ''}`}>
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
            {providerLabel && (
              <span className="mt-0.5 inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5" data-type="label-s"
                style={accentChip}>
                <Plug size={11} />{providerLabel}
              </span>
            )}
          </div>
          {/* installed apps get the real ⋯ actions menu, top-right */}
          {item.installed && <span onClick={stop}><AppActionMenu item={item} onAction={onAction} /></span>}
        </div>

        {/* description (clamped to 2 lines) + author */}
        <p className="line-clamp-2 flex-1 text-on-surface-low" data-type="body-s">
          {item.description || item.name}{item.author ? ` · by ${item.author}` : ''}
        </p>

        {/* footer: tags + the state-appropriate PRIMARY action */}
        <div className="flex items-center gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap gap-1">
            {(item.tags ?? []).slice(0, 3).map((t) => (
              <span key={t} className="inline-flex h-6 items-center rounded-pill bg-surface-high px-2 text-on-surface-var text-[0.75rem]">{t}</span>
            ))}
          </div>
          {item.installed ? (
            item.hasUI && item.enabled ? (
              <span onClick={stop}><Button variant="secondary" size="sm" onClick={() => onAction(app, 'open')}><LayoutGrid size={14} /> Open</Button></span>
            ) : item.enabled ? (
              <span className="inline-flex items-center gap-1 text-positive" data-type="label-s"><ShieldCheck size={13} /> Installed</span>
            ) : (
              // Deactivated (uninstalled, files kept): state must be visible on the
              // card — a green "Installed" here hid the fact the app is off. One
              // click re-activates (same toggle the ⋯ menu calls "Install (activate)").
              <span onClick={stop}><Button variant="primary" size="sm" onClick={() => onAction(app, 'toggle')}><Power size={14} /> Activate</Button></span>
            )
          ) : (
            <span onClick={stop}><Button variant="secondary" size="sm" disabled={busy} onClick={onInstall}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Install
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
}


// ── Install modal ──
function InstallModal({ onClose, onInstalled }: { onClose: () => void; onInstalled: () => void }) {
  const [source, setSource] = useState('')
  const guarded = useGuardedInstall((confirm) =>
    api.installApp(source.trim(), confirm).then(guardedFromApp))

  async function doInstall(confirm: boolean) {
    if (!source.trim()) return
    const r = confirm ? await guarded.confirmInstall() : await guarded.install()
    if (r?.ok) onInstalled()
  }

  const refusal = terminalRefusalReason(guarded.blocked)
  const needsConsent = guarded.blocked?.needsConsent

  return (
    <Modal title="Install app" icon={<Download size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <label data-type="body-s" className="text-on-surface-low">Source — local path or git URL</label>
        <TextInput value={source} onChange={(v) => { setSource(v); guarded.reset() }} autoFocus name="app-install-source"
          placeholder="/path/to/app  or  https://github.com/owner/app.git" />

        {guarded.blocked?.scan && <ScanReport scan={guarded.blocked.scan} />}
        {guarded.error && (
          <div className="flex items-center justify-between gap-3">
            <div data-type="body-s" className="text-negative">{guarded.error}</div>
            <FixWithAiButton fixPrompt={guarded.fixPrompt} />
          </div>
        )}

        <div className="flex justify-end gap-2 pt-s">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {needsConsent ? (
            <Button variant="primary" disabled={guarded.busy} onClick={() => doInstall(true)}>
              {guarded.busy ? <Loader2 size={16} className="animate-spin" /> : <ShieldAlert size={16} />} Install anyway
            </Button>
          ) : (
            <Button variant="primary" disabled={guarded.busy || !!refusal || !source.trim()} onClick={() => doInstall(false)}
              // A terminal refusal is a SECURITY outcome, not a missing field — it needs its own sentence.
              disabledReason={refusal || (!source.trim() ? 'Enter a source first' : undefined)}>
              {guarded.busy ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />} Install
            </Button>
          )}
        </div>
      </div>
    </Modal>
  )
}

// ── Update modal (mirrors install: source → scan → consent → atomic update) ──
function UpdateModal({ name, onClose, onUpdated }: { name: string; onClose: () => void; onUpdated: () => void }) {
  const [source, setSource] = useState('')
  const guarded = useGuardedInstall((confirm) =>
    api.updateApp(name, source.trim(), confirm).then(guardedFromApp))

  async function doUpdate(confirm: boolean) {
    if (!source.trim()) return
    const r = confirm ? await guarded.confirmInstall() : await guarded.install()
    if (r?.ok) onUpdated()
  }

  const refusal = terminalRefusalReason(guarded.blocked)
  const needsConsent = guarded.blocked?.needsConsent

  return (
    <Modal title={`Update ${name}`} icon={<RefreshCw size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <label data-type="body-s" className="text-on-surface-low">New source — local path or git URL (data is preserved)</label>
        <TextInput value={source} onChange={(v) => { setSource(v); guarded.reset() }} autoFocus name="app-install-source"
          placeholder="/path/to/app  or  https://github.com/owner/app.git" />
        {guarded.blocked?.scan && <ScanReport scan={guarded.blocked.scan} />}
        {guarded.error && (
          <div className="flex items-center justify-between gap-3">
            <div data-type="body-s" className="text-negative">{guarded.error}</div>
            <FixWithAiButton fixPrompt={guarded.fixPrompt} />
          </div>
        )}
        <div className="flex justify-end gap-2 pt-s">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {needsConsent ? (
            <Button variant="primary" disabled={guarded.busy} onClick={() => doUpdate(true)}>
              {guarded.busy ? <Loader2 size={16} className="animate-spin" /> : <ShieldAlert size={16} />} Update anyway
            </Button>
          ) : (
            <Button variant="primary" disabled={guarded.busy || !!refusal || !source.trim()} onClick={() => doUpdate(false)}
              disabledReason={refusal || (!source.trim() ? 'Enter a source first' : undefined)}>
              {guarded.busy ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />} Update
            </Button>
          )}
        </div>
      </div>
    </Modal>
  )
}



/** APE-8 "Fix with AI": shown when a failed install carried a build/hook log. Opens
 *  a chat pre-filled with the install log — already wrapped in the backend's
 *  untrusted-content fence (`fix_prompt` is built server-side; the FE only passes it
 *  through) — so the user/agent can debug the failure. Seeds the composer, never
 *  auto-sends. Renders nothing when there is no fix prompt. */
export function FixWithAiButton({ fixPrompt }: { fixPrompt: string | null }) {
  if (!fixPrompt) return null
  return (
    <Button variant="secondary" size="sm" onClick={() => launchChat({ prompt: fixPrompt })}>
      <Sparkles size={15} /> Fix with AI
    </Button>
  )
}


// ── Detail panel ──
function AppDetailPanel({ app, onClose, onChanged, onOpen }: { app: AppSummary; onClose: () => void; onChanged: () => void; onOpen: () => void }) {
  const [busy, setBusy] = useState(false)
  const [confirmUninstall, setConfirmUninstall] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [updateOpen, setUpdateOpen] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [inNav, setInNavState] = useState(() => isInNav(app.name))

  async function toggle() {
    setBusy(true)
    try { app.enabled ? await api.disableApp(app.name) : await api.enableApp(app.name); onChanged() }
    finally { setBusy(false) }
  }

  const toggleNav = () => { const next = !inNav; setInNav(app.name, next); setInNavState(next) }

  return (
    <>
      <div className="flex flex-col gap-l p-l">
        <div>
          <div data-type="body-s" className="text-on-surface-low">{app.description || app.name}</div>
          <div data-type="label-s" className="mt-1 text-on-surface-low">v{app.version} · {app.origin || 'local'}</div>
        </div>

        {app.updateAvailable && (
          <div className="flex items-center justify-between gap-3 rounded-m border border-primary/40 bg-surface-high p-m"
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

        <PermissionList perms={app.permissions} />

        {app.hasBackend && (
          <div className="rounded-m border border-outline-variant bg-surface-high p-m" data-type="body-s">
            <div className="flex items-center gap-2 text-on-surface"><Server size={14} /> Backend</div>
            <div className="mt-1 text-on-surface-low">
              {app.backendRunning ? `running on port ${app.backendPort}` : 'not running'}
            </div>
          </div>
        )}

        {app.hasUI && app.enabled && (
          <label className="flex items-center justify-between gap-3 rounded-m border border-outline-variant bg-surface-high p-m">
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
            <div className="rounded-m border border-outline-variant bg-surface-high p-m" data-type="body-s">
              <div className="flex items-center gap-2 text-on-surface"><Power size={14} /> Native app — always on</div>
              <div className="mt-1 text-on-surface-low" data-type="label-s">
                {app.hasConfig
                  ? "Ships with PersonalClaw as part of the baseline; it can't be uninstalled or disabled. You can change its settings below."
                  : "Ships with PersonalClaw as part of the baseline; it can't be uninstalled or disabled. Manage its individual tools from the Tools page."}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {app.hasUI && app.enabled && (
                <Button variant="primary" size="sm" onClick={onOpen}><LayoutGrid size={15} /> Open</Button>
              )}
              {app.hasConfig && <Button variant="ghost" size="sm" onClick={() => setConfigOpen(true)}><Settings2 size={15} /> Configure</Button>}
              <Button variant="ghost" size="sm" onClick={() => setUpdateOpen(true)}><RefreshCw size={15} /> Update</Button>
            </div>
          </>
        ) : (<>
          {/* Install IS the on-switch; uninstall is the off-switch (deactivate,
              files kept). So the primary control is activate/deactivate. */}
          <div className="flex flex-wrap gap-2">
            {app.hasUI && app.enabled && (
              <Button variant="primary" size="sm" onClick={onOpen}><LayoutGrid size={15} /> Open</Button>
            )}
            <Button variant={app.enabled ? 'secondary' : 'primary'} size="sm" disabled={busy} onClick={toggle}>
              <Power size={15} /> {app.enabled ? 'Uninstall' : 'Install'}
            </Button>
            {app.enabled && <Button variant="ghost" size="sm" onClick={() => setConfigOpen(true)}><Settings2 size={15} /> Configure</Button>}
            <Button variant="ghost" size="sm" onClick={() => setUpdateOpen(true)}><RefreshCw size={15} /> Update</Button>
          </div>

          {/* Advanced → the destructive force-uninstall (removes files from disk).
              Hidden behind an expander so it's deliberate, not accidental. */}
          <div className="border-t border-outline-variant/40 pt-3">
            <button type="button" onClick={() => setAdvancedOpen((o) => !o)} aria-expanded={advancedOpen}
              className="flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] transition-colors hover:text-on-surface">
              <ChevronDown size={14} className="transition-transform" style={{ transform: advancedOpen ? 'rotate(180deg)' : 'none' }} /> Advanced
            </button>
            {advancedOpen && (
              <div className="mt-2 rounded-m border border-outline-variant bg-surface-high p-m">
                <div data-type="body-s" className="text-on-surface">Force uninstall</div>
                <div data-type="label-s" className="mt-0.5 text-on-surface-low">
                  Remove this app's files from disk entirely. Uninstalling normally just deactivates it (files kept) — this can't be undone.
                </div>
                <Button variant="danger" size="sm" className="mt-2" onClick={() => setConfirmUninstall(true)}>
                  <Trash2 size={15} /> Force uninstall
                </Button>
              </div>
            )}
          </div>
        </>)}
      </div>

      {updateOpen && <UpdateModal name={app.name} onClose={() => setUpdateOpen(false)}
        onUpdated={() => { setUpdateOpen(false); onChanged() }} />}
      {configOpen && <ConfigModal name={app.name} onClose={() => setConfigOpen(false)} />}
      {confirmUninstall && <UninstallModal name={app.name}
        onClose={() => setConfirmUninstall(false)}
        onDone={() => { setConfirmUninstall(false); onClose(); onChanged() }} />}
    </>
  )
}

// ── Store detail panel — the not-yet-installed side of a card click. Shows the
// hero/icon + metadata and a guarded Install (consent-capable via ConsentModal),
// mirroring the card's own install path so the panel is a full parallel to
// AppDetailPanel for uninstalled catalog entries. */
function StoreDetailPanel({ item, onInstalled }: { item: StoreItem; onInstalled: () => void }) {
  const providerLabel = item.isProvider
    ? `${PROVIDER_ENTITY_LABEL[item.providerType] ?? item.providerType} provider` : ''
  const [consent, setConsent] = useState<GuardedResult | null>(null)
  // A registry-indexed (P20) item installs from its `pointer` (repo[#subdirectory]); a
  // dir-scanned/bundled item from its `source`. Both route through the scanner-gated install.
  const guarded = useGuardedInstall((confirm) => api.installApp(item.pointer || item.source, confirm).then(guardedFromApp))

  async function install(confirm: boolean) {
    const r = confirm ? await guarded.confirmInstall() : await guarded.install()
    if (r?.ok) { onInstalled(); return }
    if (isBlockingResult(r)) setConsent(r)
  }

  return (
    <div className="flex flex-col gap-l p-l">
      {/* Hero banner (optional) — the same adaptive treatment as the card. */}
      {item.heroUrl && (
        <div className="relative -mx-l -mt-l h-36 overflow-hidden bg-surface-high">
          <img src={item.heroUrl} alt="" className="size-full object-cover" />
          <div className="absolute inset-0 bg-gradient-to-t from-surface/60 to-transparent" />
        </div>
      )}
      <div>
        <div data-type="body-s" className="text-on-surface-low">{item.description || item.name}</div>
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

      {(item.tags ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {(item.tags ?? []).map((t) => (
            <span key={t} className="inline-flex h-6 items-center rounded-pill bg-surface-high px-2 text-on-surface-var text-[0.75rem]">{t}</span>
          ))}
        </div>
      )}

      {/* P29 install-consent: what this app will be GRANTED + the recurring jobs it will
          RUN, shown BEFORE install so the choice is informed. Only rendered when the
          catalog actually surfaced them (a dir-scanned/bundled entry; a registry pointer
          has no manifest yet, so these are absent and the section stays hidden). */}
      {item.permissions && Object.keys(item.permissions).length > 0 && (
        <PermissionList perms={item.permissions} />
      )}
      {(item.crons ?? []).length > 0 && <CronConsentList crons={item.crons!} />}

      <div className="rounded-m border border-outline-variant bg-surface-high p-m" data-type="body-s">
        <div className="flex items-center gap-2 text-on-surface"><Download size={14} /> Not installed</div>
        <div className="mt-1 text-on-surface-low" data-type="label-s">
          Installing fetches this app behind the security scanner — a dangerous verdict is always refused.
        </div>
      </div>

      {guarded.error && (
        <div className="flex items-center justify-between gap-3">
          <div data-type="body-s" className="text-negative">{guarded.error}</div>
          <FixWithAiButton fixPrompt={guarded.fixPrompt} />
        </div>
      )}
      <div>
        <Button variant="primary" size="sm" disabled={guarded.busy} onClick={() => install(false)}>
          {guarded.busy ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />} Install
        </Button>
      </div>

      {consent && guarded.blocked && (
        <ConsentModal label={item.displayName} result={guarded.blocked} busy={guarded.busy}
          onConfirm={async () => { const r = await guarded.confirmInstall(); if (r?.ok) { setConsent(null); onInstalled() } }}
          onClose={() => { setConsent(null); guarded.reset() }} />
      )}
    </div>
  )
}





function ConfigModal({ name, onClose }: { name: string; onClose: () => void }) {
  const cfg = useAppConfig(name)

  return (
    <Modal title={`Configure ${name}`} icon={<Settings2 size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 440 }}>
        {cfg.loading ? <div data-type="body-s" className="text-on-surface-low">Loading…</div>
          : !cfg.hasSchema ? (
            <div data-type="body-s" className="text-on-surface-low">This app declares no configurable options.</div>
          ) : (
            <AppConfigFields appName={name} props={cfg.props} cur={cfg.cur} set={cfg.set} secretSet={cfg.secretSet} />
          )}
        {cfg.err && <div data-type="body-s" className="text-negative">{cfg.err}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={cfg.busy} onClick={() => cfg.save(onClose)}>Save</Button>
        </div>
      </div>
    </Modal>
  )
}

function UninstallModal({ name, onClose, onDone }: { name: string; onClose: () => void; onDone: () => void }) {
  const { data } = useCachedData(`app-uninstall:${name}`, () => api.appUninstallPreview(name), { persist: false })
  const [busy, setBusy] = useState(false)
  const deps: AppDepClassification[] = data?.dependencies ?? []
  const kept = deps.filter((d) => d.disposition !== 'removable')

  async function forceUninstall() {
    setBusy(true)
    try { await api.uninstallApp(name, true); onDone() }  // force=true → delete files
    finally { setBusy(false) }
  }

  return (
    <Modal title={`Force uninstall ${name}?`} icon={<Trash2 size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 400 }}>
        <div data-type="body-s" className="text-on-surface-low">
          This permanently removes the app's files and providers from disk — it cannot be undone.
          To just turn the app off (keeping its files), use Uninstall instead. {kept.length > 0 &&
            'Shared dependencies still used by other apps will be kept.'}
        </div>
        {kept.length > 0 && (
          <ul className="flex flex-col gap-1">
            {kept.map((d) => (
              <li key={d.key} data-type="body-s" className="text-on-surface-low">
                • Keeping {d.kind} <span className="text-on-surface">{d.id}</span> ({d.disposition})
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="danger" disabled={busy} onClick={forceUninstall}>
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />} Force uninstall
          </Button>
        </div>
      </div>
    </Modal>
  )
}
