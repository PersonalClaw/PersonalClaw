import { useEffect, useMemo, useState } from 'react'
import { Sparkles, Search, Zap, Store, Download, Loader2, Plus, ShieldCheck, ShieldAlert, Lightbulb } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { Button } from '../../ui/Button'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { ListControls } from '../../ui/ListControls'
import { Segmented } from '../../ui/Segmented'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { useIsMobile } from '../../app/useIsMobile'
import { Modal } from '../../ui/Modal'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { SidePanel } from '../../ui/SidePanel'
import { TextInput, TextArea } from '../tasks/formControls'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type SkillItem, type SkillSearchResult, type SkillMarketplace } from '../../lib/api'
import { SOURCE_TONE, sourceLabel, fmtInstalls } from './skillMeta'
import { SkillInspector } from './SkillInspector'
import { MarketplaceDetail } from './MarketplaceDetail'
import { SkillProposals } from './SkillProposals'

const SKILL_TEMPLATE = `---
name: my-skill
description: One line on when this skill should load.
---

# My skill

Instructions the agent follows when this skill is active.
`

type Mode = 'installed' | 'browse'

export function SkillsPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [mode] = useQueryParam(query, setQuery, 'mode', 'installed')
  if (mode === 'browse') {
    return <Browse onInstalled={() => {}} onBack={() => setQuery({ mode: null, open: null, q: null })} query={query} setQuery={setQuery} />
  }
  if (mode === 'proposals') {
    return <ProposalsView onBack={() => setQuery({ mode: null, open: null, q: null })} />
  }
  return <Installed
    onBrowse={() => setQuery({ mode: 'browse', open: null, q: null })}
    onProposals={() => setQuery({ mode: 'proposals', open: null, q: null })}
    query={query} setQuery={setQuery} />
}

// ── Proposals (skill-evolution-proposal-only) ─────────────────────────────────
function ProposalsView({ onBack }: { onBack: () => void }) {
  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<div className="flex min-w-0 items-center gap-m"><span data-type="title-l" className="text-on-surface shrink-0">Skill proposals</span></div>}
          right={<HeaderActions><HeaderControl icon={Sparkles} label="Installed skills" variant="secondary" onClick={onBack} /></HeaderActions>}
        />
      }
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        <SkillProposals />
      </div>
    </WorkbenchLayout>
  )
}

function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  // Left-slot nav Segmented: both options carry an icon, so it collapses to icon-only
  // on mobile (matching the resolved header doctrine) rather than clipping "Installed"
  // into the action cluster on a narrow header. The active segment stays highlighted.
  const isMobile = useIsMobile()
  return (
    <Segmented ariaLabel="Skills view" value={mode} onChange={(m) => onChange(m as Mode)} iconOnly={isMobile}
      options={[{ key: 'installed', label: 'Installed', icon: Sparkles }, { key: 'browse', label: 'Browse', icon: Store }]} />
  )
}

// ── Installed ───────────────────────────────────────────────────────────────
function Installed({ onBrowse, onProposals, query, setQuery }: { onBrowse: () => void; onProposals: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: items, refresh } = useCachedData<SkillItem[]>('skills', () => api.skills().catch(() => []), { persist: true })
  const { data: proposals } = useCachedData('skill-proposals-count', () => api.skillProposals().catch(() => []))
  const proposalCount = proposals?.length ?? 0
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [openKeyRaw, setOpenKey] = useQueryParam(query, setQuery, 'open', '')
  const openKey = openKeyRaw || null
  const [creatingRaw, setCreating2] = useQueryParam(query, setQuery, 'create', '')
  const creating = creatingRaw === '1'
  const setCreating = (v: boolean) => setCreating2(v ? '1' : '')

  const load = () => { invalidateCache('skills'); refresh() }

  const filtered = useMemo(() => {
    if (!items) return null
    const n = q.trim().toLowerCase()
    return n ? items.filter((s) => `${s.name} ${s.description}`.toLowerCase().includes(n)) : items
  }, [items, q])
  const open = items?.find((s) => s.key === openKey) ?? null

  return (
    <>
      <WorkbenchLayout
        controls={(items === undefined || items.length > 0)
          ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search skills', label: 'Search skills' }} />
          : undefined}
        topBar={
          <TopBar
            keepCornerPadding
            left={<div className="flex min-w-0 items-center gap-m"><span data-type="title-l" className="text-on-surface shrink-0">Skills</span><ModeToggle mode="installed" onChange={(m) => m === 'browse' && onBrowse()} /></div>}
            right={
              <HeaderActions>
                <HeaderControl icon={Lightbulb} label={proposalCount > 0 ? `Proposals (${proposalCount})` : 'Proposals'} variant="secondary" onClick={onProposals} />
                <HeaderControl icon={Plus} label="New skill" variant="secondary" priority="primary" onClick={() => setCreating(true)} />
                <HeaderControl icon={Store} label="Browse" variant="primary" onClick={onBrowse} />
              </HeaderActions>
            }
          />
        }
        panel={open && (
          <SidePanel key={open.key} fillHeight storeKey="skill-panel-w" icon={<Sparkles size={18} style={{ color: SOURCE_TONE[open.source] ?? 'var(--color-primary)' }} />} title={open.name} onClose={() => setOpenKey("")}>
            <SkillInspector skill={open} onDeleted={() => { setOpenKey(""); load() }} onSaved={load} />
          </SidePanel>
        )}
      >
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {filtered === null ? <ListSkeleton rows={6} /> : filtered.length === 0 ? (
            <EmptyState icon={Sparkles} title={q ? 'No matching skills' : 'No skills installed'} hint={q ? 'Try a different term.' : 'Skills extend what agents can do. Browse the marketplace to install some.'} action={!q ? { label: 'Browse skills', onClick: onBrowse, icon: Store } : undefined} />
          ) : (
            <div className="flex flex-col gap-s">
              {filtered.map((s, i) => {
                const tone = SOURCE_TONE[s.source] ?? 'var(--color-on-surface-low)'
                // Right-click / long-press → scoped actions. This surface only opens a
                // skill (delete/enable live inside the inspector panel, not here), so
                // the menu mirrors the row's open handler — still aids discoverability.
                const menuItems: ContextMenuItem[] = [
                  { icon: <Sparkles size={15} />, label: 'Open', onSelect: () => setOpenKey(s.key) },
                ]
                return (
                  <ContextMenu key={s.key} items={menuItems}>
                  <ListRow index={i} onClick={() => setOpenKey(s.key)}>
                    <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)` }}><Sparkles size={19} style={{ color: tone }} /></span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-s">
                        <span className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{s.name}</span>
                        {s.always && <span className="shrink-0 inline-flex items-center gap-1 text-warn text-[0.7rem]" title="Always loaded"><Zap size={11} /> always</span>}
                        {s.integrity === 'intact' && <ShieldCheck size={12} className="shrink-0 text-ok" aria-label="Integrity verified" role="img" />}
                        {s.integrity === 'tampered' && <span className="shrink-0 inline-flex items-center gap-1 text-danger text-[0.7rem]" title="Integrity check failed — files changed since install"><ShieldAlert size={11} /> tampered</span>}
                      </div>
                      <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{s.description}</p>
                    </div>
                    {s.source !== 'agent-local' && s.loaded_by_agents.length > 0 && <span className="shrink-0 text-on-surface-low text-[0.7rem]">{s.loaded_by_agents.length} agent{s.loaded_by_agents.length === 1 ? '' : 's'}</span>}
                    <span className="shrink-0 rounded-pill px-2 h-6 inline-flex items-center text-[0.7rem]" style={{ background: `color-mix(in srgb, ${tone} 14%, transparent)`, color: tone }}>{sourceLabel(s.source, s.agent)}</span>
                  </ListRow>
                  </ContextMenu>
                )
              })}
            </div>
          )}
        </div>
      </WorkbenchLayout>

      {creating && <SkillCreateModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); load() }} />}
    </>
  )
}

/** Author a new local skill → POST /api/skills {name, content}. */
function SkillCreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [content, setContent] = useState(SKILL_TEMPLATE)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function create() {
    const n = name.trim()
    if (!/^[a-z0-9-]{1,64}$/.test(n)) { setErr('Name must be lowercase letters, digits, dashes (1–64 chars).'); return }
    setBusy(true); setErr('')
    try { await api.createSkill(n, content); onCreated() }
    catch (e) { setErr((e as Error).message || 'Could not create skill'); setBusy(false) }
  }

  return (
    <Modal title="New skill" icon={<Sparkles size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 'min(680px, 80vw)' }}>
        <div style={{ maxWidth: 280 }}><TextInput value={name} onChange={setName} placeholder="skill-name" autoFocus ariaLabel="Skill name" /></div>
        <TextArea value={content} onChange={setContent} rows={14} mono ariaLabel="Skill definition (SKILL.md)" />
        {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        <div className="flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={create} disabled={busy}>{busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Create skill</Button>
        </div>
      </div>
    </Modal>
  )
}

// ── Browse (marketplace) ──────────────────────────────────────────────────────
function Browse({ onBack, query, setQuery }: { onInstalled: () => void; onBack: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: marketplaces = [] } = useCachedData<SkillMarketplace[]>(
    'skills:marketplaces',
    () => api.skillMarketplaces().then((m) => m.filter((x) => x.name !== 'installed' && x.name !== 'native')).catch(() => []),
    { persist: true },
  )
  const [marketplace, setMarketplace] = useQueryParam(query, setQuery, 'mkt', '') // '' = all
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [results, setResults] = useState<SkillSearchResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set())
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null

  async function search() {
    const query = q.trim()
    if (!query) { setResults(null); return }
    setLoading(true)
    try { setResults(await api.searchSkills(query, marketplace || undefined)) }
    catch { setResults([]) }
    finally { setLoading(false) }
  }
  // Live-search as the user types (debounced) and when the marketplace scope changes.
  useEffect(() => {
    if (!q.trim()) { setResults(null); return }
    const t = setTimeout(() => { search() }, 300)
    return () => clearTimeout(t)
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [q, marketplace])

  const open = results?.find((r) => r.id === openId) ?? null

  return (
    <WorkbenchLayout
      controls={
        <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search the marketplace', label: 'Search marketplace' }}>
          <select value={marketplace} onChange={(e) => setMarketplace(e.target.value)} className="h-10 rounded-pill bg-surface-high px-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
            <option value="">All marketplaces</option>
            {marketplaces.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
          </select>
        </ListControls>
      }
      topBar={
        <TopBar
          keepCornerPadding
          left={<div className="flex min-w-0 items-center gap-m"><span data-type="title-l" className="text-on-surface shrink-0">Skills</span><ModeToggle mode="browse" onChange={(m) => m === 'installed' && onBack()} /></div>}
        />
      }
      panel={open && (
        <SidePanel key={open.id} fillHeight storeKey="skill-panel-w" icon={<Download size={18} className="text-warn" />} title={open.name || open.id} onClose={() => setOpenId("")}>
          <MarketplaceDetail result={open} installed={installedIds.has(open.id)} onInstalled={() => setInstalledIds((s) => new Set(s).add(open.id))} />
        </SidePanel>
      )}
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {loading ? <div className="flex items-center gap-2 text-on-surface-low text-[0.875rem]"><Loader2 size={15} className="animate-spin" /> Searching…</div>
          : results === null ? <EmptyState icon={Store} title="Browse skills" hint={`Search ${marketplace || 'all marketplaces'} for skills to install — the agent loads them when relevant.`} />
          : results.length === 0 ? <EmptyState icon={Search} title="No results" hint="Try a different search term or marketplace." />
          : (
            <div className="flex flex-col gap-s">
              {results.map((r, i) => {
                const installed = installedIds.has(r.id)
                // Right-click / long-press → open the marketplace result (install itself
                // lives inside the detail panel, not this row), mirroring the click handler.
                const menuItems: ContextMenuItem[] = [
                  { icon: <Download size={15} />, label: 'Open', onSelect: () => setOpenId(r.id) },
                ]
                return (
                  <ContextMenu key={r.id} items={menuItems}>
                  <ListRow index={i} onClick={() => setOpenId(r.id)}>
                    <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)' }}><Sparkles size={19} className="text-warn" /></span>
                    <div className="flex-1 min-w-0">
                      <span className="block truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{r.name || r.id}</span>
                      <div className="mt-0.5 flex flex-wrap items-center gap-x-m text-on-surface-low text-[0.8125rem]">
                        <span>{r.source}</span>
                        {r.installs ? <span>· {fmtInstalls(r.installs)}</span> : null}
                      </div>
                    </div>
                    {installed && <span className="shrink-0 text-ok text-[0.75rem]">installed</span>}
                  </ListRow>
                  </ContextMenu>
                )
              })}
            </div>
          )}
      </div>
    </WorkbenchLayout>
  )
}
