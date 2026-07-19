import { useState } from 'react'
import { ChevronRight, Check, Globe, Newspaper, LineChart, FileText, Zap, type LucideIcon } from 'lucide-react'
import { api, type SearchProviderInfo } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { PanelHeader, Section } from './settingsUI'
import { ListSkeleton } from '../../ui/ListScaffold'

// Canonical search use-cases (matches the backend SEARCH_USE_CASES). Single-select:
// one provider per use-case; an unbound one falls back to the general binding.
const USE_CASE_META: Record<string, { label: string; description: string; icon: LucideIcon }> = {
  'search-general': { label: 'General search', description: 'Default web search for any chat turn or loop.', icon: Globe },
  'search-news': { label: 'News search', description: 'Recency-biased search — prefers a provider with a freshness filter.', icon: Newspaper },
  'search-financial': { label: 'Financial search', description: 'Domain/source-biased search for financial queries.', icon: LineChart },
  'fetch-article': { label: 'Article fetch', description: 'Single-URL content extraction — prefers a provider that returns page content; else the native fetch pipeline handles it.', icon: FileText },
}
const USE_CASE_ORDER = ['search-general', 'search-news', 'search-financial', 'fetch-article']

/** Search → bind a configured search provider to each use-case. Reads
 *  /api/search/providers (registered providers + capabilities) + /api/search/active
 *  (current bindings); writes via PUT /api/search/active/{use_case}. Single-select —
 *  configure providers (endpoint / API key) over in Providers. */
export function SearchPanel() {
  const { data, refresh } = useCachedData('settings:search', async () => {
    const [providers, active] = await Promise.all([
      api.searchProviders().catch(() => [] as SearchProviderInfo[]),
      api.searchActive().catch(() => ({} as Record<string, string[]>)),
    ])
    return { providers, active }
  }, { persist: true })
  const providers = data?.providers
  const active = data?.active ?? {}

  const reloadActive = () => { invalidateCache('settings:search'); refresh() }

  if (!providers) return <ListSkeleton rows={4} />

  return (
    <div>
      <PanelHeader title="Search" hint="Bind a search provider to each use case. Configure providers (endpoint / API key) in Providers, then assign them here. An unbound use case falls back to General search." />
      <Section>
        {providers.length === 0 && (
          <div className="mb-3 rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.82rem]">
            No search providers configured. Enable <span className="text-on-surface">SearXNG</span> or <span className="text-on-surface">Tavily</span> in <span className="text-on-surface">Providers</span> and add their endpoint / API key.
          </div>
        )}
        {USE_CASE_ORDER.map((uc) => (
          <UseCaseRow key={uc} useCase={uc} activeProviders={active[uc] ?? []} providers={providers} onChanged={reloadActive} />
        ))}
      </Section>
    </div>
  )
}

function UseCaseRow({ useCase, activeProviders, providers, onChanged }: {
  useCase: string; activeProviders: string[]; providers: SearchProviderInfo[]; onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const meta = USE_CASE_META[useCase] ?? { label: useCase, description: '', icon: Globe }
  // For fetch-article, only a provider that can extract content is a sensible bind;
  // every other use-case can bind any provider.
  const eligible = useCase === 'fetch-article'
    ? providers.filter((p) => p.capabilities.supports_fetch)
    : providers

  const setActive = async (names: string[]) => {
    setSaving(true)
    try { await api.setActiveSearchProvider(useCase, names); onChanged() }
    finally { setSaving(false) }
  }
  // Single-select: clicking the active provider clears it; clicking another swaps.
  const toggle = (name: string) => setActive(activeProviders.includes(name) ? [] : [name])

  return (
    <div className="mb-2 overflow-hidden rounded-lg bg-surface-container">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-high">
        <ChevronRight size={14} className="shrink-0 text-on-surface-low transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'none', color: open ? 'var(--color-primary)' : undefined }} />
        <span className="grid size-7 shrink-0 place-items-center rounded-md"
          style={activeProviders.length > 0
            ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' }
            : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
          <meta.icon size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>{meta.label}</div>
          <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
            {activeProviders.length > 0 ? activeProviders[0] : <span className="italic">none — falls back to General</span>}
          </div>
        </div>
        {eligible.length > 0 && <span className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.68rem] tabular-nums">{eligible.length} available</span>}
      </button>

      {open && (
        <div className="flex flex-col gap-3 border-t border-outline-variant/30 px-4 pb-4 pt-3">
          <p className="text-on-surface-low text-[0.8rem]">{meta.description}</p>
          {eligible.length === 0 ? (
            <div className="rounded-lg border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low text-[0.8rem] italic">
              {useCase === 'fetch-article'
                ? 'No configured provider can extract page content. Bind one with fetch support (e.g. Tavily), or leave this unset to use the native fetch pipeline.'
                : 'No search providers configured. Add one in Providers first.'}
            </div>
          ) : (
            <div className="-m-1 flex flex-col gap-0.5 p-1" style={{ opacity: saving ? 0.6 : 1 }}>
              {eligible.map((p) => {
                const on = activeProviders.includes(p.name)
                return (
                  <button key={p.name} type="button" onClick={() => toggle(p.name)} disabled={saving}
                    className="flex items-center gap-2.5 rounded-md px-3 py-2 text-left transition-colors hover:bg-surface-high"
                    style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' } : undefined}>
                    <span className="grid size-4 shrink-0 place-items-center rounded-full border"
                      style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>
                      {on && <Check size={10} strokeWidth={3} className="text-on-primary" />}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8rem]">{p.display_name}</span>
                    <CapChips caps={p.capabilities} />
                    <span className="shrink-0 rounded-pill px-1.5 py-0.5 text-[0.65rem]"
                      style={p.available
                        ? { background: 'color-mix(in srgb, var(--color-ok) 16%, transparent)', color: 'var(--color-ok)' }
                        : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
                      {p.available ? 'ready' : 'not configured'}
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Compact capability chips for a provider (answer / content / fetch / recency). */
function CapChips({ caps }: { caps: SearchProviderInfo['capabilities'] }) {
  const chips: { label: string; on: boolean; title: string }[] = [
    { label: 'answer', on: caps.returns_answer, title: 'Returns a synthesized answer' },
    { label: 'content', on: caps.returns_content, title: 'Returns extracted page content' },
    { label: 'fetch', on: caps.supports_fetch, title: 'Can extract a single URL' },
    { label: 'recency', on: caps.supports_recency, title: 'Supports a recency filter' },
  ]
  const active = chips.filter((c) => c.on)
  if (active.length === 0) return null
  return (
    <span className="hidden shrink-0 items-center gap-1 sm:inline-flex">
      {active.map((c) => (
        <span key={c.label} title={c.title} className="inline-flex items-center gap-0.5 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.6rem] uppercase tracking-wide">
          <Zap size={8} className="text-primary" />{c.label}
        </span>
      ))}
    </span>
  )
}
