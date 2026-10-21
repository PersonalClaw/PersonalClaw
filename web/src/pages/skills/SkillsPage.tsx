import { useEffect, useMemo, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { Sparkles, Search, Zap, Store, Download, Loader2, Plus, ShieldCheck, ShieldAlert, Lightbulb, GraduationCap, FilePen, RefreshCw } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { ListControls } from '../../ui/ListControls'
import { Segmented } from '../../ui/Segmented'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { useIsMobile } from '../../app/useIsMobile'
import { Modal } from '../../ui/Modal'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { ListBesideDetail } from '../capabilities/ListBesideDetail'
import { compareSkills } from '../capabilities/capabilityOrder'
import { TextInput, TextArea, FieldError } from '../../ui/forms'
import { useQuery, invalidateKeys } from '../../lib/data'
import { api, type SkillItem, type SkillSearchResult, type SkillMarketplace, type SkillSourceUnreachable } from '../../lib/api'
import { SOURCE_TONE, sourceLabel, fmtInstalls, provenanceMeta, withFrontmatterName } from './skillMeta'
import { toneChipSkin } from '../../design/accent'
import { SkillInspector } from './SkillInspector'
import { MarketplaceDetail } from './MarketplaceDetail'
import { SkillProposals } from './SkillProposals'
import { LearningSummaryBlock } from './LearningSummaryBlock'
import { PageTitle } from '../../ui/PageTitle'

/** The New-skill dialog's starting SKILL.md. `name:` is deliberately empty: it is filled from the
 *  Name field, the skill's one identity, by `withFrontmatterName` as the user types. */
const SKILL_TEMPLATE = `---
name:
description: One line on when this skill should load.
---

# My skill

Instructions the agent follows when this skill is active.
`

type Mode = 'installed' | 'browse' | 'proposals'

export function SkillsPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [modeRaw] = useQueryParam(query, setQuery, 'mode', 'installed')
  const mode: Mode = modeRaw === 'browse' || modeRaw === 'proposals' ? modeRaw : 'installed'
  // The proposals waiting for review, counted on the view switch wherever the tab is. Its catch
  // stays: it only feeds a count, and a missing count is not a lie.
  const { data: proposals } = useQuery('skill-proposals-count', () => api.skillProposals().then((d) => d.proposals).catch(() => []))
  const toggle = <ModeToggle mode={mode} proposals={proposals?.length ?? 0}
    onChange={(m) => setQuery({ mode: m === 'installed' ? null : m, open: null, q: null })} />
  if (mode === 'browse') return <Browse toggle={toggle} query={query} setQuery={setQuery} />
  if (mode === 'proposals') return <ProposalsView toggle={toggle} />
  return <Installed toggle={toggle} onBrowse={() => setQuery({ mode: 'browse', open: null, q: null })} query={query} setQuery={setQuery} />
}

// ── Proposals ─────────────────────────────────────────────────────────────────
function ProposalsView({ toggle }: { toggle: React.ReactNode }) {
  return (
    <ListBesideDetail noun="skill proposals" detail={null}
      header={
        <TopBar
          keepCornerPadding
          left={<div className="flex min-w-0 items-center gap-m"><PageTitle className="shrink-0">Skill proposals</PageTitle>{toggle}</div>}
        />
      }
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        <SkillProposals />
      </div>
    </ListBesideDetail>
  )
}

/** The Skills tab's three views: what is installed, the marketplace, and the proposals waiting for
 *  review, with how many wait. Each is the address it always was (`?mode=`), and the header keeps
 *  only New skill, so Proposals is a view here rather than a button beside it. */
function ModeToggle({ mode, proposals, onChange }: { mode: Mode; proposals: number; onChange: (m: Mode) => void }) {
  // Left-slot nav Segmented: every option carries an icon, so it collapses to icon-only
  // on mobile (matching the resolved header doctrine) rather than clipping "Installed"
  // into the action cluster on a narrow header. The active segment stays highlighted.
  const isMobile = useIsMobile()
  // 🔑 `collapse="menu"` because WITHOUT IT THE STRIP IS CRUSHED, and `Segmented`'s own comment already
  // said so: "Tabs are `shrink-0`. `size-8` / `px-m` set a tab's size but NOT its floor, so in a
  // constrained slot the flex parent squeezed them … Overflow is the job of `collapse` ('scroll' /
  // 'menu'), not of silently crushing every target: a strip that cannot fit should scroll or fold."
  // This call site never passed the prop, so it kept crushing.
  //
  // Measured on this header (seeded `demo-home`), strip box vs its two 32px tabs:
  //
  //     1440px   strip 197×40   both tabs reachable          ← fits
  //      390px   strip  41×40   "Browse" reachable 16×16     ← already under the 24px floor
  //      320px   strip   8×40   "Browse" reachable  1×1      ← effectively gone
  //
  // The tabs are `shrink-0` and the strip's wrapper is `min-w-0`, so the strip absorbs the whole
  // squeeze while its children keep their size and overflow it. `menu` is the designed last rung of
  // the ladder (labelled → icon-only → folded pill) and `CollapsedSegmented`'s own docstring is
  // written for exactly this case: a phone header's ~42px control rail, where the labelled pill's
  // ~119px "still overflowed … and rendered as a clipped '☰ Li…'" but the icon-only pill fits.
  return (
    <Segmented ariaLabel="Skills view" value={mode} onChange={(m) => onChange(m as Mode)} iconOnly={isMobile}
      collapse="menu"
      options={[
        { key: 'installed', label: 'Installed', icon: Sparkles },
        { key: 'browse', label: 'Browse', icon: Store },
        // The count shows only while proposals wait: a counter that reads 0 is hidden.
        { key: 'proposals', label: proposals > 0 ? `Proposals (${proposals})` : 'Proposals', icon: Lightbulb },
      ]} />
  )
}

// ── Installed ───────────────────────────────────────────────────────────────
function Installed({ toggle, onBrowse, query, setQuery }: { toggle: React.ReactNode; onBrowse: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  // No `.catch(() => [])` on the installed read. Swallowing the rejection handed the hook an EMPTY
  // LIST, so a user whose library is intact was told "No skills installed" under a coral CTA to go
  // install some — measured against a 500 with native skills present on disk.
  const { data: items, error: itemsErr, refresh } = useQuery<SkillItem[]>('skills', () => api.skills(), { persist: true })
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [openKeyRaw, setOpenKey] = useQueryParam(query, setQuery, 'open', '')
  const openKey = openKeyRaw || null
  const [creatingRaw, setCreating2] = useQueryParam(query, setQuery, 'create', '')
  const creating = creatingRaw === '1'
  const setCreating = (v: boolean) => setCreating2(v ? '1' : '')

  const load = () => { invalidateKeys('skills'); refresh() }

  // In its named order: by name, case-insensitive, equal names by id. The route lists in the order a
  // copy wins, which is not an order to read by.
  const filtered = useMemo(() => {
    if (!items) return null
    const n = q.trim().toLowerCase()
    return (n ? items.filter((s) => `${s.name} ${s.description}`.toLowerCase().includes(n)) : items.slice()).sort(compareSkills)
  }, [items, q])
  const open = items?.find((s) => s.key === openKey) ?? null

  return (
    <>
      <ListBesideDetail noun="skills"
        controls={(items === undefined || items.length > 0)
          ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search skills', label: 'Search skills' }}
              results={{ count: (filtered ?? []).length, noun: 'skills', active: !!q.trim() }} />
          : undefined}
        header={
          <TopBar
            keepCornerPadding
            left={<div className="flex min-w-0 items-center gap-m"><PageTitle className="shrink-0">Skills</PageTitle>{toggle}</div>}
            right={
              <HeaderActions>
                {/* 🔑 `New skill` IS the primary here, and it is the canonical spelling. Every other list
                    destination writes its create action `variant="primary" priority="primary"` — `New
                    chat`, `New task`, `New trigger`, `New project` (×2), `New agent`, `New report`, `New
                    intent`, `Add knowledge`, `Install from URL`, `Start from template`. It is the one
                    action the header holds (#3817): Proposals is a view on the switch beside the title,
                    and Browse was always one. */}
                <HeaderControl icon={Plus} label="New skill" variant="primary" priority="primary" onClick={() => setCreating(true)} />
              </HeaderActions>
            }
          />
        }
        detail={open && {
          id: open.key, title: open.name, onClose: () => setOpenKey(''),
          icon: <Sparkles size={18} style={{ color: SOURCE_TONE[open.source] ?? 'var(--color-primary)' }} />,
          body: <SkillInspector skill={open} onDeleted={() => { setOpenKey(''); load() }} onSaved={load} />,
        }}
      >
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {/* The learning summary block. Above the list, INSIDE the content column so it
              shares the list's measure. Renders itself away when learning is off or nothing
              was learned in the window, so it costs a fresh install nothing. */}
          <LearningSummaryBlock />
          {items === undefined && itemsErr ? (
            <LoadError what="installed skills" error={itemsErr} onRetry={load} />
          ) : filtered === null ? <ListSkeleton rows={6} what="installed skills" /> : filtered.length === 0 ? (
            <EmptyState icon={Sparkles} title={q ? 'No matching skills' : 'No skills installed'} hint={q ? 'Try a different term.' : 'Skills extend what agents can do. Browse the marketplace to install some.'} action={!q ? { label: 'Browse skills', onClick: onBrowse, icon: Store } : undefined} />
          ) : (
            <div className="flex flex-col gap-s">
              {filtered.map((s) => {
                const tone = SOURCE_TONE[s.source] ?? 'var(--color-on-surface-low)'
                const prov = provenanceMeta(s.provenance)
                // Right-click / long-press → scoped actions. This surface only opens a
                // skill (delete/enable live inside the inspector panel, not here), so
                // the menu mirrors the row's open handler — still aids discoverability.
                const menuItems: ContextMenuItem[] = [
                  { icon: <Sparkles size={15} />, label: 'Open', onSelect: () => setOpenKey(s.key) },
                ]
                return (
                  <ContextMenu key={s.key} items={menuItems}>
                  <ListRow onClick={() => setOpenKey(s.key)} label={s.name} selected={s.key === openKey}>
                    <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)` }}><Sparkles size={19} style={{ color: tone }} /></span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-s">
                        <span className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{s.name}</span>
                        {s.always && <span className="shrink-0 inline-flex items-center gap-1 text-warn text-[0.75rem]" title="Always loaded"><Zap size={11} /> always</span>}
                        {s.integrity === 'intact' && <ShieldCheck size={12} className="shrink-0 text-ok" aria-label="Integrity verified" role="img" />}
                        {/* `edited` is the owner's change since the skill was installed, said plainly; only a
                            damaged install record is a warning, since then nothing can say what was installed. */}
                        {s.integrity === 'edited' && <span className="shrink-0 inline-flex items-center gap-xs text-on-surface-low text-[0.75rem]" title="Changed since it was installed"><FilePen size={11} /> edited</span>}
                        {s.integrity === 'tampered' && <span className="shrink-0 inline-flex items-center gap-xs text-danger text-[0.75rem]" title="Its install record is damaged, so PersonalClaw can’t tell what was installed"><ShieldAlert size={11} /> can’t verify</span>}
                        {s.bundled_update && <span className="shrink-0 inline-flex items-center gap-xs text-info text-[0.75rem]" title="A newer version comes with PersonalClaw. Yours was kept: open the skill to take it or keep yours."><RefreshCw size={11} /> new version</span>}
                        {/* Provenance rides with `always`/`edited` rather than beside the source
                            chip: it is a fact about THIS skill's origin, not the tier badge, and
                            these markers already render only when they apply — a hand-authored
                            skill adds nothing, which is most rows. Raw semantic ink, no tint, so
                            it inherits this group's settled contrast rather than the chip's. */}
                        {prov && <span data-type="caption" className={`shrink-0 inline-flex items-center gap-xs ${prov.tone}`} title={prov.title}><GraduationCap size={11} /> {prov.label}</span>}
                      </div>
                      {/* `title`: measured at 390px, these descriptions clip to **192px of up to
                          3217px** — 16.8x over, so a phone user sees roughly the first six words of a
                          sentence written to explain what the skill DOES. The row's other truncating
                          texts already carry one (`always`, `tampered`), and a `title` on a truncating
                          element is the app's settled recovery idiom (19 sites). Nothing re-layouts. */}
                      <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]" title={s.description}>{s.description}</p>
                    </div>
                    {s.source !== 'agent-local' && s.loaded_by_agents.length > 0 && <span className="shrink-0 text-on-surface-low text-[0.75rem]">{s.loaded_by_agents.length} agent{s.loaded_by_agents.length === 1 ? '' : 's'}</span>}
                    {/* `toneChipSkin`, not the raw tint: `SOURCE_TONE` maps `bundled` and `native` to
                        `--color-primary`, so this drew coral ink on a 14% coral tint — **3.97:1 in
                        light** against a 4.5 floor, measured on 14 chips here with axe agreeing
                        [serious]. Exactly the 14% row of the accent-chip family, whose helper returns
                        the opaque container pair for coral (13.1:1 light / 10.43:1 dark) and leaves
                        every other tone's tint alone. Dark was never affected — 5.9. */}
                    <span className="shrink-0 rounded-pill px-2 h-6 inline-flex items-center text-[0.75rem]" style={toneChipSkin(tone)}>{sourceLabel(s.source, s.agent)}</span>
                  </ListRow>
                  </ContextMenu>
                )
              })}
            </div>
          )}
        </div>
      </ListBesideDetail>

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
  // The Name field is the skill's identity; the SKILL.md's `name:` follows it on every keystroke,
  // so the default path — type a name, press Create — sends a body the server accepts.
  const onName = (v: string) => { setName(v); setContent((c) => withFrontmatterName(c, v)) }

  async function create() {
    const n = name.trim()
    // No leading or trailing dash, because the server's key sanitizer strips them: `my-skill-`
    // would be stored as `my-skill` and then refused, since the frontmatter still says `my-skill-`.
    if (!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(n)) { setErr('Name must be 1–64 lowercase letters, digits or dashes, starting and ending with a letter or digit.'); return }
    setBusy(true); setErr('')
    try { await api.createSkill(n, content); onCreated() }
    catch (e) { setErr((e as Error).message || 'Could not create skill'); setBusy(false) }
  }

  return (
    <Modal title="New skill" icon={<Sparkles size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 'min(680px, 80vw)' }}>
        <div style={{ maxWidth: 280 }}><TextInput value={name} onChange={onName} placeholder="skill-name" autoFocus ariaLabel="Skill name" /></div>
        <TextArea value={content} onChange={setContent} rows={14} mono ariaLabel="Skill definition (SKILL.md)" />
        {err && <FieldError>{err}</FieldError>}
        <div className="flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={create} loading={busy}><Plus size={14} /> Create skill</Button>
        </div>
      </div>
    </Modal>
  )
}

// ── Browse (marketplace) ──────────────────────────────────────────────────────
function Browse({ toggle, query, setQuery }: { toggle: React.ReactNode } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: marketplaces = [] } = useQuery<SkillMarketplace[]>(
    'skills:marketplaces',
    // 🔴 `!== 'native'` was here too, and `/api/skills/marketplaces` returns exactly
    // {installed, native} on a stock install — so this narrowed to `[]`, the `<select>` below
    // emitted zero `<option>`s, and the only scope left was the "All marketplaces" default. Paired
    // with a server-side fan-out that dropped every installed hit, Browse had no reachable scope
    // that returned anything and every query rendered "No results — try a different search term or
    // marketplace" over a dropdown offering no other marketplace (#301). `installed` stays out on
    // its own terms: it mirrors the user's skills dir, so scoping to it would search the Installed
    // tab from the Browse tab.
    () => api.skillMarketplaces().then((m) => m.filter((x) => x.name !== 'installed')).catch(() => []),
    { persist: true },
  )
  const [marketplace, setMarketplace] = useQueryParam(query, setQuery, 'mkt', '') // '' = all
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [results, setResults] = useState<SkillSearchResult[] | null>(null)
  // Catalogues the last search could install FROM — excluding the two `native` mirrors of what
  // is already here. `null` = not searched yet. Zero of these is a different empty state from
  // zero matches, and the two looked identical.
  const [installableSources, setInstallableSources] = useState<number | null>(null)
  // Per-source matched counts from the search (computed server-side BEFORE the global
  // cap), so the source filter shows how many of a large catalog matched — not just how
  // many of its rows survived into this page of results.
  const [counts, setCounts] = useState<Record<string, number>>({})
  // The catalogues the last search could not reach, each with why. Their skills are missing from
  // `results`, so "No results" is only true when this is empty.
  const [unreachable, setUnreachable] = useState<SkillSourceUnreachable[]>([])
  const [loading, setLoading] = useState(false)
  const [searchErr, setSearchErr] = useState<unknown>(null)
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set())
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null

  async function search() {
    const query = q.trim()
    if (!query) { setResults(null); setCounts({}); setInstallableSources(null); setUnreachable([]); setSearchErr(null); return }
    setLoading(true)
    try {
      const { results: rows, counts: bySource, installableSources: reached, unreachable: missed } = await api.searchSkillsCounted(query, marketplace || undefined)
      setSearchErr(null)
      setResults(rows)
      setInstallableSources(reached)
      setCounts(bySource)
      setUnreachable(missed)
    }
    // The error is kept, not folded into `[]`: a failed search rendered as "No results — try a
    // different search term", blaming the user's query for an outage.
    catch (e) { setSearchErr(e); setResults(null); setCounts({}); setInstallableSources(null); setUnreachable([]) }
    finally { setLoading(false) }
  }
  // Live-search as the user types (debounced) and when the marketplace scope changes.
  useEffect(() => {
    if (!q.trim()) { setResults(null); setSearchErr(null); return }
    const t = setTimeout(() => { search() }, 300)
    return () => clearTimeout(t)
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [q, marketplace])

  const open = results?.find((r) => r.id === openId) ?? null
  const totalMatches = Object.values(counts).reduce((a, b) => a + b, 0)

  return (
    <ListBesideDetail noun="results"
      controls={
        // 🪤 This list comes from a FETCH, not a client-side narrow, so `active` also waits for the
        // fetch to settle: announcing while `loading` would report the previous query's count, and
        // announcing on `results === null` (nothing searched yet) would say "No matching skills"
        // about a search the user has not run.
        <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search the marketplace', label: 'Search marketplace' }}
          results={{ count: results?.length ?? 0, noun: 'skills', active: !!q.trim() && !loading && results !== null }}>
          <select value={marketplace} onChange={(e) => setMarketplace(e.target.value)} aria-label="Marketplace" className="h-10 rounded-pill bg-surface-high px-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
            <option value="">{totalMatches ? `All marketplaces (${totalMatches})` : 'All marketplaces'}</option>
            {marketplaces.map((m) => <option key={m.name} value={m.name}>{counts[m.name] ? `${m.name} (${counts[m.name]})` : m.name}</option>)}
          </select>
        </ListControls>
      }
      header={
        <TopBar
          keepCornerPadding
          left={<div className="flex min-w-0 items-center gap-m"><PageTitle className="shrink-0">Skills</PageTitle>{toggle}</div>}
        />
      }
      detail={open && {
        id: open.id, title: open.name || open.id, onClose: () => setOpenId(''),
        icon: <Download size={18} className="text-warn" />,
        // Installed-ness comes from the SERVER's annotation, OR from what this session just
        // installed. Reading only the session set claimed "not installed" about every already-present
        // skill on first paint, which is most of them on a stock install.
        body: <MarketplaceDetail result={open} installed={installedIds.has(open.id) || !!open.installed} onInstalled={() => setInstalledIds((s) => new Set(s).add(open.id))} />,
      }}
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {loading ? <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]"><Loader2 size={15} className="animate-spin" /> Searching…</div>
          : searchErr ? <LoadError what="skill search results" error={searchErr} onRetry={search} />
          : results === null ? <EmptyState icon={Store} title="Browse skills" hint={`Search ${marketplace || 'all marketplaces'} for skills to install — the agent loads them when relevant.`} />
          : unreachable.length > 0 && results.length === 0 ? <UnreachableCatalogues sources={unreachable} onRetry={search} />
          : results.length === 0 && installableSources === 0
            // Nothing to install FROM. Saying "no results" here blames the query for the
            // absence of any catalogue to query — our own "a failed fetch renders as an empty
            // state" shape. `=== 0` and not `!installableSources`, so a FAILED request (back to
            // null) keeps the ordinary wording rather than asserting a configuration fact it
            // never learned.
            ? <EmptyState icon={Store} title="No skill catalogue configured" hint="The store installs skills from a catalogue, and none is set up yet — so there is nothing to search. Install a skill-source app to add one. Your own skills and the bundled ones are unaffected." />
          : results.length === 0 ? <EmptyState icon={Search} title="No results" hint="Try a different search term or marketplace." />
          : (
            <div className="flex flex-col gap-s">
              {unreachable.length > 0 && <UnreachableCatalogues sources={unreachable} onRetry={search} />}
              {results.map((r) => {
                const installed = installedIds.has(r.id) || !!r.installed
                // Right-click / long-press → open the marketplace result (install itself
                // lives inside the detail panel, not this row), mirroring the click handler.
                const menuItems: ContextMenuItem[] = [
                  { icon: <Download size={15} />, label: 'Open', onSelect: () => setOpenId(r.id) },
                ]
                return (
                  <ContextMenu key={r.id} items={menuItems}>
                  <ListRow onClick={() => setOpenId(r.id)} label={r.name || r.id} selected={r.id === openId}>
                    <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)' }}><Sparkles size={19} className="text-warn" /></span>
                    <div className="flex-1 min-w-0">
                      <span className="block truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{r.name || r.id}</span>
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
    </ListBesideDetail>
  )
}

/** The catalogues a search could not reach, said above whatever it did find: a search that none of
 *  them answered is not one that matched nothing. The reason is the gateway's own sentence. */
function UnreachableCatalogues({ sources, onRetry }: { sources: SkillSourceUnreachable[]; onRetry: () => void }) {
  const one = sources.length === 1
  return (
    <div data-type="body-s" role="status" className="rounded-lg bg-surface-container px-l py-m text-warn">
      <p>
        Couldn't search {one ? sources[0].source : `${sources.length} catalogues`}, so{' '}
        {one ? 'its skills are' : 'their skills are'} missing here.
      </p>
      <ul className="mt-xs flex flex-col gap-xs">
        {sources.map((u) => (
          <li key={u.source} data-type="caption"><span className="text-on-surface">{u.source}</span> — {u.reason}</li>
        ))}
      </ul>
      <Button size="xs" variant="ghost" className="mt-xs" onClick={onRetry}>Try again</Button>
    </div>
  )
}
