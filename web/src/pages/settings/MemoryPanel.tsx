import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Database, BookOpen, ScrollText, Eye, Settings2, Search, Plus, Trash2,
  Loader2, RefreshCw, HeartPulse, GraduationCap, AlertTriangle, Share2, FileEdit, Save, UploadCloud, ArrowRightLeft, Moon, type LucideIcon,
} from 'lucide-react'
import { MemoryGraph } from './MemoryGraph'
import {
  api, type MemorySettings, type SemanticEntry,
  type EpisodicEntry, type MemoryEvent, type MemoryVaultStatus, type DailyDigest,
  type MemoryLint, type MemoryObservability, type Lesson, type MemoryStats,
} from '../../lib/api'
import { PanelHeader, Section, Field, Row, Toggle, SavedToast } from './settingsUI'
import { confirm, confirmDelete } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { ListSkeleton, FormSkeleton } from '../../ui/ListScaffold'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'

// Two-level tab model (MEM-i3): the exploration surfaces — every "look at what's
// stored" view — nest under Browse; the top level keeps the distinct destinations
// (Browse · Graph · Health · Editors · Settings). ?tab holds the leaf id, so a deep
// link like ?tab=recall still lands on the right sub-tab (Browse auto-selected).
// The Memory Studio (studio) folds the old exploration surfaces (Semantic/Episodic/
// Lessons/Graph) + the Editors docs into ONE 3-pane explorer. The remaining tabs are
// FOCUSED TOOLS, not browsing: Health (lint/dream), Recall (scored recall), Inspect
// (context preview), Audit (WAL), Settings (retention/consolidation). Flat tab bar —
// no more tabs-under-tabs. ?tab holds the id so deep links + refresh survive.
type Tab = 'studio' | 'recall' | 'health' | 'audit' | 'inspect' | 'settings'
const TOP_TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
  { id: 'studio', label: 'Studio', icon: Share2 },
  { id: 'health', label: 'Health', icon: HeartPulse },
  { id: 'recall', label: 'Recall', icon: Search },
  { id: 'inspect', label: 'Inspect', icon: Eye },
  { id: 'audit', label: 'Audit', icon: ScrollText },
  { id: 'settings', label: 'Settings', icon: Settings2 },
]

/** Memory — a full explorer over the vector-memory store. Browse/edit semantic
 *  key-values, search episodic memories, audit every memory op, preview injected
 *  context, and tune retention + run consolidation. The active tab rides ?tab
 *  (replace — an in-place view switch), so #/settings/memory?tab=audit deep-links
 *  and survives refresh. */
const ALL_TABS: Tab[] = ['studio', 'recall', 'health', 'audit', 'inspect', 'settings']
// Old deep-links (?tab=browse/episodic/graph/lessons/editors) fold into the Studio,
// which subsumed all of them — so a bookmarked pre-Studio URL still lands sensibly.
const LEGACY_TAB_ALIAS: Record<string, Tab> = {
  browse: 'studio', episodic: 'studio', graph: 'studio', lessons: 'studio', editors: 'studio',
}

export function MemoryPanel({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [tabRaw, setTabRaw] = useQueryParam(query, setQuery, 'tab', 'studio', { replace: true })
  const resolved = LEGACY_TAB_ALIAS[tabRaw as string] ?? tabRaw
  const tab = (ALL_TABS.includes(resolved as Tab) ? resolved : 'studio') as Tab
  const setTab = (t: Tab) => setTabRaw(t)
  const { data: stats, refresh: refreshStats } = useCachedData(
    'settings:memory-stats', () => api.memoryStats().catch(() => null), { persist: true },
  )
  const reloadStats = () => { invalidateCache('settings:memory-stats'); refreshStats() }

  return (
    <div className="flex flex-col" style={{ minHeight: 0 }}>
      <PanelHeader title="Memory" hint="Explore and manage what the system remembers — a studio over semantic facts, episodes, lessons, and documents, plus health, recall, and the audit trail." />

      {stats && (
        <div className="mb-l grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Semantic" value={stats.semantic_active} />
          <Stat label="Episodic" value={stats.episodic_active} />
          <Stat label="Events" value={stats.events_count} />
          <Stat label="Embedded" value={stats.embedded_count} sub={stats.embedding_provider} />
        </div>
      )}

      {/* flat tab bar — Studio (explore) + the focused tools (no more tabs-under-tabs) */}
      <div className="mb-l flex gap-0.5 border-b border-outline-variant/40">
        {TOP_TABS.map((t) => {
          const on = t.id === tab
          return (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
              className="-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[0.82rem] transition-colors"
              style={on
                ? { borderColor: 'var(--color-primary)', color: 'var(--color-primary)' }
                : { borderColor: 'transparent', color: 'var(--color-on-surface-low)' }}>
              <t.icon size={14} /> {t.label}
            </button>
          )
        })}
      </div>

      {/* Studio owns its own 3-pane height; the tool tabs get a bounded scroll body. */}
      {tab === 'studio' ? (
        <MemoryStudio onChanged={reloadStats} />
      ) : (
        <ToolTabBody>
          {tab === 'recall' && <RecallTab />}
          {tab === 'health' && <HealthTab onChanged={reloadStats} />}
          {tab === 'audit' && <AuditTab />}
          {tab === 'inspect' && <InspectTab />}
          {tab === 'settings' && <SettingsTab stats={stats} onConsolidated={reloadStats} />}
        </ToolTabBody>
      )}
    </div>
  )
}

/** Bounded, self-measuring scroll body for the focused tool tabs (Recall/Health/
 *  Audit/Inspect/Settings) — the ONE scroll region, so the header/tab-bar stay pinned. */
function ToolTabBody({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [bodyH, setBodyH] = useState(420)
  useEffect(() => {
    const measure = () => {
      const el = ref.current
      if (!el) return
      setBodyH(Math.max(240, window.innerHeight - el.getBoundingClientRect().top - 24))
    }
    measure(); window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  return <div ref={ref} className="overflow-y-auto pr-1" style={{ height: bodyH }}>{children}</div>
}

function Stat({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="rounded-lg bg-surface-container px-3 py-2.5">
      <div className="text-on-surface text-[1.25rem] tabular-nums" style={{ fontVariationSettings: '"wght" 600' }}>{value}</div>
      <div className="text-on-surface-low text-[0.75rem]">{label}{sub ? ` · ${sub}` : ''}</div>
    </div>
  )
}

/** value_json is JSON-encoded (sometimes doubly) — unwrap to a readable string. */
function readValue(raw?: string): string {
  if (raw == null) return ''
  let v: unknown = raw
  for (let i = 0; i < 2; i++) {
    if (typeof v !== 'string') break
    try { v = JSON.parse(v) } catch { break }
  }
  return typeof v === 'string' ? v : JSON.stringify(v)
}

// ── Memory Studio ────────────────────────────────────────────────────────────
// A 3-pane "studio of memories" — the single home for EXPLORING + INSPECTING
// everything the system remembers. Replaces the old tabs-under-tabs (Semantic ·
// Episodic · Lessons) + the orphaned Graph tab + the awkward Editors tab, folding
// them into ONE surface where the list, the graph, and the inspector are all views
// onto the same objects (list-detail + Obsidian-style local-graph focus).
//
//   ┌ EXPLORER ┬──── GRAPH CANVAS ────┬ INSPECTOR ┐
//   │ facets   │   local-focus on the │ full      │
//   │ + search │   selected memory's  │ fields +  │
//   │ + list   │   N-hop neighbourhood│ edit/del  │
//   └──────────┴──────────────────────┴───────────┘
// Selecting in the list focuses the graph + opens the inspector; clicking a node
// selects it in the list. One fetch of the graph is shared across all three panes.

type StudioKind = 'fact' | 'episodic' | 'lesson' | 'doc'
interface StudioItem {
  uid: string            // unique within the studio (kind-scoped)
  kind: StudioKind
  title: string          // the list's primary line (key / rule / first line / doc name)
  preview: string        // secondary line
  ref: string | null     // the graph node ref (`sem:<key>`, `lesson:<rule[:80]>`), or null (episodic/doc = no single node)
  fact?: SemanticEntry
  episodic?: EpisodicEntry
  lesson?: Lesson
  doc?: { which: 'preferences' | 'projects' | 'history'; label: string }
}

const STUDIO_KIND_META: Record<StudioKind, { label: string; icon: LucideIcon }> = {
  fact: { label: 'Facts', icon: Database },
  episodic: { label: 'Episodes', icon: BookOpen },
  lesson: { label: 'Lessons', icon: GraduationCap },
  doc: { label: 'Documents', icon: FileEdit },
}
const STUDIO_DOCS: { which: 'preferences' | 'projects' | 'history'; label: string }[] = [
  { which: 'preferences', label: 'Preferences' },
  { which: 'projects', label: 'Projects' },
  { which: 'history', label: 'History' },
]

/** A lesson's graph ref mirrors the backend `_add("lesson", str(rule)[:80], …)` — so
 *  a selected lesson maps to its node without re-hashing (the `ref` seam handles the
 *  md5; we only need the same label key). */
const lessonRef = (rule: string) => `lesson:${rule.slice(0, 80)}`

function MemoryStudio({ onChanged }: { onChanged: () => void }) {
  const [kindFilter, setKindFilter] = useState<StudioKind | 'all'>('all')
  const [q, setQ] = useState('')
  const [selUid, setSelUid] = useState<string | null>(null)
  const [hopDepth, setHopDepth] = useState(1)
  const [addMode, setAddMode] = useState<'fact' | 'lesson' | null>(null)

  // ── data: facts + episodics + lessons + the graph (shared across panes) ──
  const { data: facts, refresh: refreshFacts } = useCachedData('settings:memory-semantic', () => api.memorySemantic().catch(() => [] as SemanticEntry[]))
  const { data: episodics, refresh: refreshEpi } = useCachedData('settings:memory-episodic:all', () => api.memoryEpisodic({ limit: 100 }).catch(() => [] as EpisodicEntry[]))
  const { data: lessons, refresh: refreshLessons } = useCachedData('settings:lessons', () => api.lessons().catch(() => [] as Lesson[]), { persist: false })
  const { data: graph, refresh: refreshGraph } = useCachedData('settings:memory-graph', () => api.memoryGraph().catch(() => ({ nodes: [], edges: [] })), { persist: false })
  const reloadAll = () => {
    invalidateCache('settings:memory-semantic'); invalidateCache('settings:memory-episodic', true)
    invalidateCache('settings:lessons'); invalidateCache('settings:memory-graph')
    refreshFacts(); refreshEpi(); refreshLessons(); refreshGraph(); onChanged()
  }

  // ── unified item list ──
  const items: StudioItem[] = useMemo(() => {
    const out: StudioItem[] = []
    for (const d of STUDIO_DOCS) out.push({ uid: `doc:${d.which}`, kind: 'doc', title: d.label, preview: 'Editable markdown memory', ref: null, doc: d })
    for (const f of facts ?? []) out.push({ uid: `fact:${f.key}`, kind: 'fact', title: f.key, preview: readValue(f.value_json), ref: `sem:${f.key}`, fact: f })
    for (const l of lessons ?? []) out.push({ uid: `lesson:${l.rule}`, kind: 'lesson', title: l.rule, preview: l.category || 'lesson', ref: lessonRef(l.rule), lesson: l })
    for (const e of episodics ?? []) out.push({ uid: `epi:${e.id}`, kind: 'episodic', title: e.text.slice(0, 80), preview: e.created_at ? fmtDate(e.created_at) : 'episodic', ref: null, episodic: e })
    return out
  }, [facts, episodics, lessons])

  const loading = facts === undefined || episodics === undefined || lessons === undefined
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length, fact: 0, episodic: 0, lesson: 0, doc: 0 }
    for (const it of items) c[it.kind]++
    return c
  }, [items])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return items.filter((it) => (kindFilter === 'all' || it.kind === kindFilter)
      && (!needle || it.title.toLowerCase().includes(needle) || it.preview.toLowerCase().includes(needle)))
  }, [items, kindFilter, q])

  const selected = useMemo(() => items.find((it) => it.uid === selUid) ?? null, [items, selUid])
  const focusRef = selected?.ref ?? null

  // ── pane height (mirror MemoryGraph's self-measure) ──
  const shellRef = useRef<HTMLDivElement | null>(null)
  const [paneH, setPaneH] = useState(460)
  useEffect(() => {
    const measure = () => {
      const el = shellRef.current
      if (!el) return
      setPaneH(Math.max(320, window.innerHeight - el.getBoundingClientRect().top - 24))
    }
    measure(); window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  // Clicking a graph node → select the matching item (by ref).
  const selectByRef = (ref: string) => {
    const it = items.find((x) => x.ref === ref)
    if (it) setSelUid(it.uid)
  }

  const removeSelected = async () => {
    if (!selected) return
    if (selected.kind === 'fact' && selected.fact) {
      if (!(await confirmDelete('memory', selected.fact.key))) return
      await api.deleteSemantic(selected.fact.key).catch(() => {})
    } else if (selected.kind === 'episodic' && selected.episodic) {
      if (!(await confirm({ title: 'Delete this episodic memory?', danger: true, confirmLabel: 'Delete' }))) return
      await api.deleteEpisodic(selected.episodic.id).catch(() => {})
    } else if (selected.kind === 'lesson' && selected.lesson) {
      if (!(await confirmDelete('lesson'))) return
      await api.deleteLesson(selected.lesson.rule).catch(() => {})
    } else return
    setSelUid(null); reloadAll()
  }

  return (
    <div ref={shellRef} className="flex gap-3 overflow-hidden" style={{ height: paneH }}>
      {/* ── EXPLORER ── */}
      <div className="flex w-[19rem] shrink-0 flex-col rounded-xl border border-outline-variant/40 bg-surface-container/40">
        <div className="flex flex-col gap-2 border-b border-outline-variant/30 p-2.5">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memories"
              className="h-8 w-full rounded-md bg-surface-high pl-8 pr-2 text-[0.8rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          </div>
          <div className="flex flex-wrap gap-1">
            {(['all', 'fact', 'episodic', 'lesson', 'doc'] as const).map((k) => {
              const on = kindFilter === k
              const meta = k === 'all' ? null : STUDIO_KIND_META[k]
              return (
                <button key={k} type="button" onClick={() => setKindFilter(k)}
                  className="inline-flex items-center gap-1 rounded-pill px-2 h-6 text-[0.72rem] transition-colors"
                  style={on ? { background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
                  {meta && <meta.icon size={11} />}{k === 'all' ? 'All' : meta!.label}<span className="tabular-nums opacity-60">{counts[k]}</span>
                </button>
              )
            })}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {loading ? <ListSkeleton rows={8} /> : shown.length === 0 ? (
            <p className="py-6 text-center text-on-surface-low text-[0.8rem]">{q ? 'No matches.' : 'No memories yet.'}</p>
          ) : shown.map((it) => {
            const on = it.uid === selUid
            const Icon = STUDIO_KIND_META[it.kind].icon
            return (
              <button key={it.uid} type="button" onClick={() => setSelUid(it.uid)}
                className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors"
                style={on ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined}>
                <Icon size={13} className="mt-0.5 shrink-0" style={{ color: on ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[0.76rem]" style={{ color: on ? 'var(--color-primary)' : 'var(--color-on-surface)' }}>{it.title}</span>
                  <span className="block truncate text-on-surface-low text-[0.7rem]">{it.preview}</span>
                </span>
              </button>
            )
          })}
        </div>
        <div className="flex gap-1.5 border-t border-outline-variant/30 p-2">
          <Button size="sm" variant="secondary" onClick={() => { setAddMode('fact'); setSelUid(null) }} className="flex-1"><Plus size={14} /> Fact</Button>
          <Button size="sm" variant="secondary" onClick={() => { setAddMode('lesson'); setSelUid(null) }} className="flex-1"><GraduationCap size={14} /> Lesson</Button>
        </div>
      </div>

      {/* ── GRAPH CANVAS ── */}
      <div className="relative min-w-0 flex-1 overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container/40">
        <MemoryGraph data={graph ?? null} focusRef={focusRef} hopDepth={hopDepth} onSelectRef={selectByRef} boxHeight={paneH} />
        {focusRef && (
          <div className="absolute right-3 top-3 flex items-center gap-2 rounded-pill bg-surface-high/90 px-2 py-1 text-[0.7rem] backdrop-blur">
            <span className="text-on-surface-low">Focus · hops</span>
            {[1, 2, 3].map((d) => (
              <button key={d} type="button" onClick={() => setHopDepth(d)}
                className="grid size-5 place-items-center rounded tabular-nums"
                style={hopDepth === d ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { color: 'var(--color-on-surface-low)' }}>{d}</button>
            ))}
            <button type="button" onClick={() => setSelUid(null)} className="ml-1 text-primary hover:underline">↺ show all</button>
          </div>
        )}
      </div>

      {/* ── INSPECTOR ── */}
      <div className="flex w-[21rem] shrink-0 flex-col overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container/40">
        {addMode ? (
          <div className="flex flex-col gap-2 p-3">
            <div className="flex items-center justify-between">
              <span className="text-on-surface text-[0.85rem] font-medium">{addMode === 'fact' ? 'New fact' : 'New lesson'}</span>
              <button type="button" onClick={() => setAddMode(null)} className="text-on-surface-low text-[0.75rem] hover:text-on-surface">Cancel</button>
            </div>
            {addMode === 'fact'
              ? <AddSemanticForm onDone={(created) => { setAddMode(null); if (created) reloadAll() }} />
              : <AddLessonForm onDone={(created) => { setAddMode(null); if (created) reloadAll() }} />}
          </div>
        ) : selected ? (
          <StudioInspector item={selected} onDelete={removeSelected} onSaved={reloadAll} />
        ) : (
          <div className="grid flex-1 place-items-center p-6 text-center">
            <div className="text-on-surface-low">
              <Eye size={22} className="mx-auto mb-2 opacity-50" />
              <p className="text-[0.82rem]">Select a memory to inspect it.</p>
              <p className="mt-1 text-[0.72rem]">Facts &amp; lessons light up their neighbourhood in the graph.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** The inspector pane — full fields + actions per kind. Fact/episodic/lesson show
 *  their record + a Delete; a Document opens the reused markdown editor inline. */
function StudioInspector({ item, onDelete, onSaved }: { item: StudioItem; onDelete: () => void; onSaved: () => void }) {
  const Icon = STUDIO_KIND_META[item.kind].icon
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-outline-variant/30 px-3 py-2.5">
        <Icon size={14} className="shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate font-mono text-on-surface text-[0.8rem]">{item.title}</span>
        {item.kind !== 'doc' && (
          <button type="button" onClick={onDelete} aria-label="Delete" title="Delete" className="grid size-7 shrink-0 place-items-center rounded-md text-on-surface-low hover:text-danger"><Trash2 size={13} /></button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {item.kind === 'fact' && item.fact && (
          <div className="flex flex-col gap-3 text-[0.8rem]">
            <div>
              <div className="mb-1 text-on-surface-low text-[0.68rem] uppercase tracking-wide">Value</div>
              <pre className="whitespace-pre-wrap rounded-lg bg-surface-high px-3 py-2 text-on-surface text-[0.78rem]">{readValue(item.fact.value_json)}</pre>
            </div>
            <StudioMeta pairs={[
              ['Scope', (item.fact.scope || 'global') + (item.fact.scope_ref ? ` · ${item.fact.scope_ref}` : '')],
              ['Source', item.fact.source || '—'], ['Tier', item.fact.tier || 'semantic'],
              ['Confidence', item.fact.confidence != null ? String(item.fact.confidence) : '—'],
              ['Recalled', `${item.fact.recall_count ?? 0}×`],
              ['Updated', item.fact.updated_at ? fmtDate(item.fact.updated_at) : '—'],
            ]} />
          </div>
        )}
        {item.kind === 'episodic' && item.episodic && (
          <div className="flex flex-col gap-3 text-[0.8rem]">
            <p className="leading-snug text-on-surface">{item.episodic.text}</p>
            <StudioMeta pairs={[
              ['When', item.episodic.created_at ? fmtDate(item.episodic.created_at) : '—'],
              ['Tags', parseTags(item.episodic.tags).map((t) => `#${t}`).join(' ') || '—'],
            ]} />
          </div>
        )}
        {item.kind === 'lesson' && item.lesson && (
          <div className="flex flex-col gap-3 text-[0.8rem]">
            <p className="leading-snug text-on-surface">{item.lesson.rule}</p>
            <StudioMeta pairs={[['Category', item.lesson.category || '—'], ['Learned', item.lesson.ts ? fmtDate(item.lesson.ts) : '—']]} />
          </div>
        )}
        {item.kind === 'doc' && item.doc && (
          <StudioDocEditor which={item.doc.which} onSaved={onSaved} />
        )}
      </div>
    </div>
  )
}

function StudioMeta({ pairs }: { pairs: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[0.74rem]">
      {pairs.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-on-surface-low">{k}</dt>
          <dd className="truncate text-on-surface-var">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Inline markdown editor for a memory doc, folded into the inspector (reuses the
 *  same GET/PUT the old Editors tab used). Save gated on dirty; transient Saved ✓. */
function StudioDocEditor({ which, onSaved }: { which: 'preferences' | 'projects' | 'history'; onSaved: () => void }) {
  const [content, setContent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  useEffect(() => { setContent(null); api.memoryDoc(which).then((c) => { setContent(c); setDraft(c) }).catch(() => { setContent(''); setDraft('') }) }, [which])
  const dirty = content !== null && draft !== content
  const save = async () => {
    setBusy(true)
    try { await api.saveMemoryDoc(which, draft); setContent(draft); setSaved(true); window.setTimeout(() => setSaved(false), 1800); onSaved() }
    catch { /* leave dirty */ }
    setBusy(false)
  }
  if (content === null) return <div className="flex items-center gap-2 text-on-surface-low text-[0.8rem]"><Loader2 size={14} className="animate-spin" /> Loading…</div>
  return (
    <div className="flex flex-col gap-2">
      <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={16} spellCheck={false}
        className="w-full resize-y rounded-lg bg-surface-high px-3 py-2 font-mono text-[0.74rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50"
        style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }} />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={!dirty || busy}><Save size={14} /> {busy ? 'Saving…' : 'Save'}</Button>
        {dirty && <span className="text-on-surface-low text-[0.74rem]">Unsaved changes</span>}
        {saved && <span className="text-ok text-[0.74rem]">Saved ✓</span>}
      </div>
    </div>
  )
}

/** Add a learned lesson from the Studio (POST /api/lessons) — the manual entry the
 *  old Lessons tab had; lessons are mostly auto-captured, but a user can add one. */
function AddLessonForm({ onDone }: { onDone: (created: boolean) => void }) {
  const [rule, setRule] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const submit = async () => {
    if (!rule.trim()) return
    setSaving(true); setErr('')
    try { await api.addLesson(rule.trim()); onDone(true) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); setSaving(false) }
  }
  return (
    <div className="flex flex-col gap-2">
      <textarea value={rule} onChange={(e) => setRule(e.target.value)} rows={4} autoFocus
        placeholder="e.g. Always run the test suite before saying a fix works."
        className="w-full resize-y rounded-lg bg-surface-high px-3 py-2 text-[0.8rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={submit} disabled={!rule.trim() || saving}>{saving ? 'Saving…' : 'Save lesson'}</Button>
        {err && <span className="text-danger text-[0.76rem]">{err}</span>}
      </div>
      <p className="text-on-surface-low text-[0.72rem]">Injected into future prompts. Prune anything wrong from the list.</p>
    </div>
  )
}

// ── Add-fact form (used by the Studio explorer) ──────────────────────────────
const KEY_PREFIXES = ['pref', 'project', 'user', 'lesson']
function AddSemanticForm({ onDone }: { onDone: (created: boolean) => void }) {
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const validKey = /^[a-z][a-z0-9_.]*[a-z0-9]$/.test(key) && KEY_PREFIXES.includes(key.split('.')[0])

  const submit = async () => {
    if (!validKey) { setErr(`Key must start with ${KEY_PREFIXES.map((p) => `${p}.`).join(' / ')} and be lowercase dotted.`); return }
    if (!value.trim()) { setErr('Value is required.'); return }
    setSaving(true); setErr('')
    try { await api.writeSemantic(key, value); onDone(true) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); setSaving(false) }
  }

  return (
    <div className="mb-3 rounded-lg border border-outline-variant/40 bg-surface p-3">
      <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="key (e.g. pref.theme, user.timezone)"
        className="mb-2 h-9 w-full rounded-md bg-surface-high px-3 font-mono text-[0.8rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <textarea value={value} onChange={(e) => setValue(e.target.value)} placeholder="value" rows={2}
        className="mb-2 w-full rounded-md bg-surface-high px-3 py-2 text-[0.8rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={submit} disabled={saving || !key || !value.trim()}>{saving ? 'Saving…' : 'Save'}</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {err && <span className="text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      </div>
    </div>
  )
}

// ── Audit ────────────────────────────────────────────────────────────────────
function AuditTab() {
  const { data: events, refresh } = useCachedData(
    'settings:memory-events', () => api.memoryEvents({ limit: 100 }).catch(() => [] as MemoryEvent[]),
  )
  const [filter, setFilter] = useState('')
  const reload = () => { invalidateCache('settings:memory-events'); refresh() }

  if (!events) return <ListSkeleton rows={8} />
  const q = filter.trim().toLowerCase()
  const shown = q ? events.filter((e) => `${e.event_type} ${e.memory_type} ${e.memory_key ?? ''}`.toLowerCase().includes(q)) : events

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by type or key"
            className="h-9 w-full rounded-md bg-surface-high pl-8 pr-2 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </div>
        <Button variant="secondary" size="sm" onClick={reload}><RefreshCw size={14} /></Button>
      </div>
      {shown.length === 0 ? (
        <p className="py-6 text-center text-on-surface-low text-[0.82rem]">No matching events.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {shown.map((e) => <AuditRow key={e.id} ev={e} onUndone={reload} />)}
        </div>
      )}
    </div>
  )
}

const EVENT_TONE: Record<string, string> = {
  create: 'var(--color-success)', update: 'var(--color-primary)',
  delete: 'var(--color-danger)', import: 'var(--color-primary)', consolidate: 'var(--color-warning)',
}
// Semantic event types whose effect the reversible WAL can undo.
const UNDOABLE = new Set(['create', 'update', 'delete', 'supersede', 'promotion'])
function AuditRow({ ev, onUndone }: { ev: MemoryEvent; onUndone: () => void }) {
  const [busy, setBusy] = useState(false)
  const canUndo = ev.memory_type === 'semantic' && UNDOABLE.has(ev.event_type) && !ev.undone_at
  const undo = async () => {
    setBusy(true)
    try { await api.undoMemoryEvent(ev.id); onUndone() } finally { setBusy(false) }
  }
  return (
    <div className="flex items-center gap-2 rounded-md bg-surface-container px-3 py-1.5 text-[0.78rem]">
      <span className="w-16 shrink-0 font-mono text-[0.7rem]" style={{ color: EVENT_TONE[ev.event_type] ?? 'var(--color-on-surface-low)' }}>{ev.event_type}</span>
      <span className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low text-[0.66rem]">{ev.memory_type}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-on-surface text-[0.74rem]">{ev.memory_key || '—'}</span>
      {ev.undone_at && <span className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low text-[0.62rem]">undone</span>}
      {ev.created_at && <span className="shrink-0 text-on-surface-low text-[0.68rem]">{fmtDate(ev.created_at)}</span>}
      {canUndo && (
        <button onClick={undo} disabled={busy} title="Undo this memory change"
          className="shrink-0 rounded px-1.5 py-0.5 text-[0.66rem] text-on-surface-low hover:text-primary disabled:opacity-50">
          {busy ? '…' : 'undo'}
        </button>
      )}
    </div>
  )
}

// ── Inspect (context preview) ────────────────────────────────────────────────
function InspectTab() {
  const [q, setQ] = useState('')
  const [result, setResult] = useState<{ semantic: string; episodic: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try { const p = await api.memoryContextPreview(q); setResult({ semantic: p.semantic_context, episodic: p.episodic_context }) }
    catch { setResult({ semantic: '', episodic: '' }) }
    setBusy(false)
  }
  return (
    <div>
      <p className="mb-3 text-on-surface-low text-[0.82rem]">Preview the memory context that would be injected into a prompt for a given query.</p>
      <div className="mb-3 flex items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') run() }}
          placeholder="A query, e.g. what's my timezone" className="h-9 flex-1 rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <Button size="sm" onClick={run} disabled={busy}>{busy ? <Loader2 size={15} className="animate-spin" /> : 'Preview'}</Button>
      </div>
      {result && (
        <div className="flex flex-col gap-3">
          <InspectBlock title="Semantic context" body={result.semantic} />
          <InspectBlock title="Episodic context" body={result.episodic} />
        </div>
      )}
    </div>
  )
}
function InspectBlock({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <div className="mb-1 text-on-surface-low text-[0.7rem] uppercase tracking-wide">{title}</div>
      {body ? (
        <pre className="overflow-x-auto rounded-lg bg-surface-container px-3 py-2 text-on-surface text-[0.74rem] whitespace-pre-wrap">{body}</pre>
      ) : (
        <p className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low text-[0.78rem] italic">Nothing would be injected.</p>
      )}
    </div>
  )
}

// ── Recall (deep query-scored recall) ────────────────────────────────────────
/** "Ask my memory" — a query-scored deep recall over the whole store. Unlike
 *  Inspect (which previews the turn-injection context), this runs the ranked
 *  recall the memory_recall tool uses and records the recall signal. */
function RecallTab() {
  const [q, setQ] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (!q.trim()) return
    setBusy(true)
    try { const r = await api.memoryRecall(q.trim()); setResult(r.result) }
    catch { setResult('') }
    setBusy(false)
  }
  return (
    <div>
      <p className="mb-3 text-on-surface-low text-[0.82rem]">Ask your memory a question — a ranked deep recall across every stored fact, lesson, and episode (records the recall signal).</p>
      <div className="mb-3 flex items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') run() }}
          placeholder="e.g. what did I decide about the TicTacToe deploy?" className="h-9 flex-1 rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <Button size="sm" onClick={run} disabled={busy || !q.trim()}>{busy ? <Loader2 size={15} className="animate-spin" /> : 'Recall'}</Button>
      </div>
      {result !== null && (result
        ? <pre className="overflow-x-auto rounded-lg bg-surface-container px-3 py-2 text-on-surface text-[0.74rem] whitespace-pre-wrap">{result}</pre>
        : <p className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low text-[0.78rem] italic">Nothing recalled for that query.</p>)}
    </div>
  )
}

// ── Health (lint + observability + promote) ──────────────────────────────────
/** Memory health: the lint report card (near-dups / stale / contradictions, with
 *  what auto-purged), the observability dashboard (injection-rejection reasons +
 *  injected-context byte budget), and a manual episodic→durable promote trigger. */
function HealthTab({ onChanged }: { onChanged: () => void }) {
  const { data: lint, refresh: refreshLint } = useCachedData<MemoryLint | null>('settings:memory-lint', () => api.memoryLint().catch(() => null), { persist: false })
  const { data: obs, refresh: refreshObs } = useCachedData<MemoryObservability | null>('settings:memory-obs', () => api.memoryObservability().catch(() => null), { persist: false })
  const [promoting, setPromoting] = useState(false)
  const [dreamResult, setDreamResult] = useState<string | null>(null)
  const promote = async () => {
    setPromoting(true); setDreamResult(null)
    try {
      const r = await api.memoryPromote()
      const n = r?.promoted ?? 0
      setDreamResult(n > 0 ? `Consolidated ${n} fact${n > 1 ? 's' : ''}.` : 'Nothing new to consolidate.')
      window.setTimeout(() => setDreamResult(null), 4000)
    } catch { /* surfaced by no change */ }
    setPromoting(false)
    invalidateCache('settings:memory-lint'); invalidateCache('settings:memory-obs'); refreshLint(); refreshObs(); onChanged()
  }
  const reload = () => { invalidateCache('settings:memory-lint'); invalidateCache('settings:memory-obs'); refreshLint(); refreshObs() }
  if (lint === undefined || obs === undefined) return <ListSkeleton rows={5} />
  const autoFixed = lint ? Object.entries(lint.auto_fixed).filter(([, n]) => n > 0) : []
  return (
    <div className="flex flex-col gap-l">
      {/* Dreaming — episodic→semantic consolidation. Runs automatically in the
          background (after-turn promote_episodic_patterns), but a manual trigger lets
          the user dream on demand — e.g. right after a dense session. The scoring is
          vector_memory.dream_score (frequency × diversity × recency × richness). */}
      <Section title="Dreaming"
        hint="Like sleep consolidates memories, PersonalClaw reviews its episodic memories (raw conversation fragments) and promotes the recurring, cross-context ones into durable semantic facts — scored on frequency, diversity, recency, and richness. It runs automatically in the background; trigger a pass now to consolidate a recent burst of activity.">
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={promote} disabled={promoting}>{promoting ? <><Loader2 size={14} className="animate-spin" /> Dreaming…</> : <><Moon size={14} /> Dream now</>}</Button>
          {dreamResult
            ? <span className="text-ok text-[0.76rem]">{dreamResult}</span>
            : <span className="text-on-surface-low text-[0.76rem]">Consolidate episodic memories → semantic facts</span>}
        </div>
      </Section>

      {/* health report card */}
      <Section title="Health check" hint="Duplicate, stale, and contradictory facts. Superseded facts auto-purge on each sweep.">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={reload}><RefreshCw size={14} /> Re-scan</Button>
        </div>
        {autoFixed.length > 0 && (
          <p className="mt-2 text-ok text-[0.78rem]">Auto-purged: {autoFixed.map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(', ')}.</p>
        )}
        <div className="mt-3 flex flex-col gap-1.5">
          {!lint || lint.flags.length === 0 ? (
            <p className="text-on-surface-low text-[0.82rem] italic">No issues flagged — memory is clean.</p>
          ) : lint.flags.map((f, i) => (
            <div key={i} className="flex items-start gap-2 rounded-lg bg-surface-container px-3 py-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
              <div className="min-w-0">
                <div className="text-on-surface text-[0.8rem]"><span className="rounded bg-surface-high px-1.5 py-0.5 text-[0.68rem] uppercase tracking-wide text-on-surface-low">{f.check.replace(/_/g, ' ')}</span> <span className="font-mono">{f.key}</span></div>
                <div className="text-on-surface-low text-[0.76rem]">{f.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* observability */}
      {obs && (
        <Section title="Observability" hint="What the memory system is doing under the hood.">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Object.entries(obs.stats).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-surface-container px-3 py-2">
                <div className="text-on-surface text-[1.05rem] tabular-nums" style={{ fontVariationSettings: '"wght" 600' }}>{v}</div>
                <div className="text-on-surface-low text-[0.7rem]">{k.replace(/_/g, ' ')}</div>
              </div>
            ))}
          </div>
          {Object.keys(obs.rejections).length > 0 && (
            <div className="mt-3">
              <div className="mb-1 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Write rejections</div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(obs.rejections).map(([reason, n]) => (
                  <span key={reason} className="rounded-pill bg-surface-high px-2.5 py-1 text-[0.75rem] text-on-surface-var">{reason.replace(/_/g, ' ')}: <strong>{n}</strong></span>
                ))}
              </div>
            </div>
          )}
          <div className="mt-3 text-on-surface-low text-[0.76rem]">
            Injected-context budget: <strong className="text-on-surface-var">{obs.context_preview.total_chars.toLocaleString()} chars</strong>
            {' '}(semantic {obs.context_preview.semantic_chars.toLocaleString()} · episodic {obs.context_preview.episodic_chars.toLocaleString()} · lessons {obs.context_preview.lessons_chars.toLocaleString()})
          </div>
        </Section>
      )}
    </div>
  )
}

// ── Maintenance (migrate legacy memory / import an export) — used by SettingsTab ──
function MemoryMaintenance({ stats, onChanged }: { stats: MemoryStats | null | undefined; onChanged: () => void }) {
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const migrate = async () => {
    setBusy('migrate'); setMsg('')
    try { const c = await api.memoryMigrate(); setMsg(`Migrated ${c.semantic ?? 0} semantic + ${c.episodic ?? 0} episodic.`); onChanged() }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Migration failed') }
    setBusy('')
  }
  const importJson = async () => {
    const input = document.createElement('input')
    input.type = 'file'; input.accept = 'application/json,.json'
    input.onchange = async () => {
      const f = input.files?.[0]; if (!f) return
      setBusy('import'); setMsg('')
      try {
        const data = JSON.parse(await f.text())
        const c = await api.memoryImport(data)
        setMsg(`Imported ${Object.entries(c).map(([k, v]) => `${v} ${k}`).join(', ') || 'nothing'}.`); onChanged()
      } catch (e) { setMsg(e instanceof Error ? e.message : 'Import failed — is it a valid export JSON?') }
      setBusy('')
    }
    input.click()
  }
  return (
    <Section title="Maintenance" hint="Migrate legacy memory or restore from an export.">
      <div className="flex flex-wrap items-center gap-2">
        {stats?.has_legacy_memory && (
          <Button size="sm" variant="ghost" onClick={migrate} disabled={!!busy}>
            {busy === 'migrate' ? <Loader2 size={14} className="animate-spin" /> : <ArrowRightLeft size={14} />} Migrate legacy → vector store
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={importJson} disabled={!!busy}>
          {busy === 'import' ? <Loader2 size={14} className="animate-spin" /> : <UploadCloud size={14} />} Import from JSON
        </Button>
      </div>
      {stats && !stats.has_legacy_memory && <p className="mt-1.5 text-on-surface-low text-[0.75rem]">No legacy markdown memory to migrate.</p>}
      {msg && <p className="mt-2 text-on-surface-var text-[0.8rem]">{msg}</p>}
    </Section>
  )
}

// ── Settings (retention + consolidate) ───────────────────────────────────────
function SettingsTab({ stats, onConsolidated }: { stats: MemoryStats | null | undefined; onConsolidated: () => void }) {
  const { data } = useCachedData(
    'settings:memory-settings', () => api.memorySettings().catch(() => null), { persist: true },
  )
  const [s, setS] = useState<MemorySettings | null>(null)
  const [saved, setSaved] = useState(false)
  const [consolidating, setConsolidating] = useState(false)
  const [consolidateMsg, setConsolidateMsg] = useState('')
  useEffect(() => { if (data) setS(data) }, [data])

  const patch = (p: Partial<MemorySettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    api.saveMemorySettings(p).then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) }).catch(() => {})
  }
  const consolidate = async () => {
    setConsolidating(true); setConsolidateMsg('')
    try {
      // Consolidation runs per chat session (the rollup of a conversation into
      // memory) — fire it for every current session, like the legacy panel.
      const sessions = await api.chatSessions().catch(() => [])
      const keys = sessions.map((s) => s.key).filter(Boolean)
      if (keys.length === 0) { setConsolidateMsg('No active sessions to consolidate.'); setConsolidating(false); return }
      const results = await Promise.allSettled(keys.map((k) => api.consolidateMemory(k)))
      const ok = results.filter((r) => r.status === 'fulfilled' && !(r.value as { error?: string }).error).length
      setConsolidateMsg(`Consolidation started for ${ok}/${keys.length} session${keys.length === 1 ? '' : 's'}.`)
    } catch (e) { setConsolidateMsg(e instanceof Error ? e.message : 'Failed') }
    setConsolidating(false); onConsolidated()
  }

  if (!s) return <FormSkeleton sections={2} />
  return (
    <div>
      <Section title="Retention" hint="When idle conversations roll up into memory and how long history is kept.">
        <Field label="Idle before history rollup (hours)" hint="A conversation idle this long gets consolidated into memory.">
          <NumInput value={s.history_idle_hours} onChange={(v) => patch({ history_idle_hours: v })} step={0.5} min={0.5} />
        </Field>
        <Field label="Max history age (days)" hint="History older than this is pruned.">
          <NumInput value={s.history_max_days} onChange={(v) => patch({ history_max_days: v })} step={1} min={1} />
        </Field>
        <div className="mt-2"><SavedToast show={saved} /></div>
      </Section>

      <Section title="Injection & behavior" hint="How memory is surfaced to the agent each turn, and whether it acts proactively.">
        <Row label="L1 manifest injection" hint="Inject a small always-on manifest of your most-recalled facts; the agent pulls deeper memory on demand. Off = inject full semantic + episodic every turn (legacy).">
          <Toggle on={s.l1_manifest !== false} onChange={(v) => patch({ l1_manifest: v })} label="L1 manifest injection" />
        </Row>
        <Row label="Active recall" hint="On an interactive turn, surface query-relevant memory just before the reply — bounded by a timeout + circuit breaker.">
          <Toggle on={s.active_recall !== false} onChange={(v) => patch({ active_recall: v })} label="Active recall" />
        </Row>
        <Row label="Proactive check-ins" hint="Experimental: let the agent infer future check-ins from conversation and deliver one natural reminder per window via the heartbeat. Off by default; high-confidence only, capped per day, one-tap dismiss.">
          <Toggle on={Boolean(s.proactive_commitments)} onChange={(v) => patch({ proactive_commitments: v })} label="Proactive check-ins" />
        </Row>
        <div className="mt-2"><SavedToast show={saved} /></div>
      </Section>

      <Section title="Consolidation" hint="Force an immediate consolidation pass instead of waiting for idle rollup.">
        <div className="flex items-center gap-3">
          <Button variant="secondary" size="sm" onClick={consolidate} disabled={consolidating}>
            {consolidating ? <><Loader2 size={15} className="animate-spin" /> Consolidating…</> : 'Consolidate now'}
          </Button>
          {consolidateMsg && <span className="text-on-surface-low text-[0.8rem]">{consolidateMsg}</span>}
        </div>
      </Section>

      <VaultSection settings={s} onToggle={(v) => patch({ vault_enabled: v })} saved={saved} />

      <DailyDigestSection />

      {/* Maintenance (migrate legacy memory / import an export) — moved here from the
          retired Editors tab; retention/consolidation/maintenance now live together. */}
      <MemoryMaintenance stats={stats} onChanged={onConsolidated} />
    </div>
  )
}

/** Daily-digest nodes (mem-tree) — the per-day "what happened on day D" rollups the
 *  maintenance cadence builds from episodic activity. Read view + a Build-now action
 *  (forces a synchronous rebuild for days not yet digested). */
function DailyDigestSection() {
  const [digests, setDigests] = useState<DailyDigest[] | null>(null)
  const [busy, setBusy] = useState(false)
  const load = (rebuild = false) => {
    setBusy(true)
    api.dailyDigests(rebuild).then(setDigests).catch(() => setDigests([])).finally(() => setBusy(false))
  }
  useEffect(() => load(false), [])

  return (
    <Section title="Daily digests" hint="Per-day rollups of memory activity — 'what happened on day D'. Built automatically on the maintenance cadence; browsable in the Obsidian vault too.">
      <div className="mb-3 flex items-center gap-3">
        <Button variant="secondary" size="sm" onClick={() => load(true)} disabled={busy}>
          {busy ? <><Loader2 size={15} className="animate-spin" /> Building…</> : 'Build / refresh'}
        </Button>
        {digests && <span className="text-on-surface-low text-[0.8rem]">{digests.length} digest{digests.length === 1 ? '' : 's'}</span>}
      </div>
      {!digests ? <ListSkeleton rows={3} /> : digests.length === 0 ? (
        <p className="py-4 text-center text-on-surface-low text-[0.82rem]">No daily digests yet — they build from past days with memory activity.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {digests.map((d) => <DigestRow key={d.day} digest={d} />)}
        </div>
      )}
    </Section>
  )
}

function DigestRow({ digest }: { digest: DailyDigest }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg bg-surface-container px-3 py-2">
      <button type="button" onClick={() => setOpen((o) => !o)} className="w-full text-left">
        <div className="flex items-center gap-2">
          <span className="font-mono text-on-surface text-[0.8rem]">{digest.day}</span>
          <span className="text-on-surface-low text-[0.7rem]">daily digest</span>
        </div>
        <div className={`mt-0.5 text-on-surface-low text-[0.78rem] ${open ? 'whitespace-pre-wrap' : 'truncate'}`}>{digest.text}</div>
      </button>
    </div>
  )
}

/** Memory vault mirror — enable the Obsidian-compatible markdown export + a
 *  sync-now / open-folder affordance. The vault is a read-only projection of
 *  memory; it re-syncs automatically after each session seal, but "Sync now"
 *  lets a user generate/refresh it on demand (works even while the toggle is off,
 *  as a one-shot export). */
function VaultSection({ settings, onToggle, saved }: {
  settings: MemorySettings; onToggle: (v: boolean) => void; saved: boolean
}) {
  const enabled = Boolean(settings.vault_enabled)
  const [status, setStatus] = useState<MemoryVaultStatus | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [msg, setMsg] = useState('')
  const loadStatus = () => { api.memoryVaultStatus().then(setStatus).catch(() => setStatus(null)) }
  useEffect(loadStatus, [enabled])

  const sync = async () => {
    setSyncing(true); setMsg('')
    try {
      const r = await api.syncMemoryVault()
      setMsg(`Synced ${r.records} record${r.records === 1 ? '' : 's'} → ${r.files} file${r.files === 1 ? '' : 's'}` +
        (r.written ? ` (${r.written} updated${r.pruned ? `, ${r.pruned} pruned` : ''})` : ' (no changes)'))
      loadStatus()
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Sync failed') }
    setSyncing(false)
  }

  return (
    <Section title="Memory vault (Obsidian mirror)" hint="Mirror memory to a browsable markdown vault — YAML frontmatter + [[wikilinks]] + graph view. Read-only; regenerated from the store, never hand-edited.">
      <Row label="Enable vault mirror" hint="When on, the vault re-syncs automatically after each session is sealed.">
        <Toggle on={enabled} onChange={onToggle} label="Enable vault mirror" />
      </Row>
      {status?.path && (
        <p className="mt-1 mb-2 font-mono text-on-surface-low text-[0.72rem] break-all">
          {status.path}{status.exists ? ` · ${status.files} file${status.files === 1 ? '' : 's'}` : ' · not yet generated'}
        </p>
      )}
      <div className="mt-1 flex items-center gap-3">
        <Button variant="secondary" size="sm" onClick={sync} disabled={syncing}>
          {syncing ? <><Loader2 size={15} className="animate-spin" /> Syncing…</> : 'Sync now'}
        </Button>
        {msg && <span className="text-on-surface-low text-[0.8rem]">{msg}</span>}
      </div>
      <div className="mt-2"><SavedToast show={saved} /></div>
    </Section>
  )
}

function NumInput({ value, onChange, step, min }: { value: number; onChange: (v: number) => void; step: number; min: number }) {
  return (
    <input type="number" value={value} step={step} min={min} onChange={(e) => onChange(Number(e.target.value))}
      className="h-9 w-28 rounded-md bg-surface-container px-2.5 text-on-surface text-[0.875rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]" />
  )
}

// ── helpers ──────────────────────────────────────────────────────────────────
function parseTags(raw?: string): string[] {
  if (!raw) return []
  if (Array.isArray(raw)) return raw as string[]
  try { const v = JSON.parse(raw); return Array.isArray(v) ? v : [] } catch { return [] }
}
function fmtDate(iso: string): string {
  // avoid Date.now()-class APIs; just trim the ISO string to date + HH:MM.
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : iso.slice(0, 16)
}
