import { useState } from 'react'
import { Check, Globe, Newspaper, LineChart, FileText, Zap, type LucideIcon } from 'lucide-react'
import { api, type SearchProviderInfo, type ToolItem } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { reportingWrite } from '../../app/reportingWrite'
import { PanelHeader, Section } from './settingsUI'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { DisclosureCard } from '../../ui/DisclosureCard'
import { StatusPill } from '../../ui/StatusPill'
import { TextLink } from '../../ui/TextLink'
import { hasSearchTool, SEARCH_TOOL, SEARCH_TOOL_APP } from './searchTool'
import { SearchFailure, SearchStatePill, SearchTestButton } from './searchProviderState'
import { InlineError } from '../../ui/InlineError'

// Canonical search use-cases (matches the backend SEARCH_USE_CASES). Single-select:
// one provider per use-case; an unbound one falls back to the general binding.
const USE_CASE_META: Record<string, { label: string; description: string; icon: LucideIcon }> = {
  'search-general': { label: 'General search', description: 'Default web search for any chat turn or loop.', icon: Globe },
  'search-news': { label: 'News search', description: 'Recency-biased search — prefers a provider with a freshness filter.', icon: Newspaper },
  'search-financial': { label: 'Financial search', description: 'Domain/source-biased search for financial queries.', icon: LineChart },
  'fetch-article': { label: 'Article fetch', description: 'Single-URL content extraction — prefers a provider that returns page content; else the native fetch pipeline handles it.', icon: FileText },
}
const USE_CASE_ORDER = ['search-general', 'search-news', 'search-financial', 'fetch-article']

/** The Store card of the app that ships the search tool (see `searchTool.ts` for why a stale name
 *  cannot strand anyone)… */
const SEARCH_TOOL_APP_HREF = `#/apps?view=store&open=${SEARCH_TOOL_APP.name}`
/** …and the seven search-provider apps, deep-linked by the `search` tag every one of them
 *  declares — the bare Store is 38 cards. */
const STORE_SEARCH_APPS_HREF = '#/apps?view=store&stag=search'
/** The panel's dashed advisory shell, declared once because it now carries TWO notes — and because
 *  two shrink-only ratchets meet on this string. `spacingTokenRamp` wants an exact-rung value spelled
 *  as a token; `rowGroupPadding` caps the token-spelled roomy slabs that compete with `RowGroup`. A
 *  second literal copy trips one of them whichever way it is spelled, so: one declaration, two uses,
 *  and the census counts these three utilities once. (Spelling either offending pair out in this
 *  comment also trips the second rail — it matches the raw file, comments included.) */
const NOTE_SHELL = 'mb-3 rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-3 text-on-surface-low'

/** Search → bind a configured search provider to each use-case. Reads
 *  /api/search/providers (registered providers + capabilities) + /api/search/active
 *  (current bindings) + /api/tools (does a `web_search` tool exist at all); writes via
 *  PUT /api/search/active/{use_case}. Single-select — configure providers (endpoint /
 *  API key) over in Providers. */
export function SearchPanel() {
  const { data, error: loadErr, refresh } = useQuery('settings:search', async () => {
    const [providers, active, tools] = await Promise.all([
      // 🔴 NO FALLBACK, and this is the read that may not have one (#532). It backs a positive
      // claim about the server's state — "No search providers configured. Install a search
      // provider app from the Store" — so a 500 on /api/search/providers told a user with three
      // registered providers to go install their first one, and pointed them at the Store to do
      // it. The rejection has to reach the hook for the panel to be able to say otherwise.
      api.searchProviders(),
      // `null`, NOT `{}`, on failure — the same reasoning as the tools probe below, for a louder
      // claim: `{}` made every use case read "none — falls back to General" and offered its
      // providers to pick, as if nothing were bound. Unread bindings are said as unread.
      api.searchActive().catch(() => null),
      // `null`, NOT `[]`, on failure: "no tools came back" and "the tool list says there is
      // no web_search" are different claims, and only the second one may accuse the user of a
      // missing app. An unreachable /api/tools renders no note rather than a false one.
      api.tools().catch(() => null as ToolItem[] | null),
    ])
    return { providers, active, tools }
  }, { persist: true })
  const providers = data?.providers
  /** `null` = the bindings could not be read (the rows below are replaced, not guessed). */
  const active = data?.active ?? null
  const tools = data?.tools ?? null
  // Registered-but-no-tool is the whole gap. Registered (not "bound") is the right left-hand
  // side because the registry's implicit fallback searches over ANY registered provider when a
  // use-case is unbound (search_providers/registry.py "2. Implicit fallback"), so a provider is
  // enough to make the missing tool the only thing standing between the user and a web search.
  const missingSearchTool = providers !== undefined && providers.length > 0
    && tools !== null && !hasSearchTool(tools)

  // PREFIX mode, so a binding change also reaches the hub tile's own `settings:search-card` — a key
  // of its own because its reads are bare where this panel's are not (see `useSearchEntity`).
  const reloadActive = () => { invalidateKeys('settings:search', true); refresh() }

  // Error BEFORE the skeleton, or it is unreachable — `providers` is undefined for the loading AND
  // the failed case, so the skeleton would run forever on a 500. Same one-line shape `PacksPanel`
  // ships for the same reason. The skeleton borrows the same noun (`ui/loadingNounPairing`): the
  // failure and the wait describe one fetch, so they say one word — and the two lines stay adjacent,
  // because that rail reads a gate's two branches as a PAIR and anything wedged between them reads
  // as a different fetch.
  if (!providers && loadErr) return <LoadError what="search providers" error={loadErr} onRetry={refresh} />
  if (!providers) return <ListSkeleton rows={4} what="search providers" />

  return (
    <div>
      <PanelHeader title="Search" hint="Bind a search provider to each use case. Configure providers (endpoint / API key) in Providers, then assign them here. An unbound use case falls back to General search." />
      <Section>
        {providers.length === 0 && (
          <div data-type="body-s" className={`${NOTE_SHELL} text-center`}>
            No search providers configured. Install a search provider app from the <TextLink href={STORE_SEARCH_APPS_HREF} ink="emphasis" className="underline">Store</TextLink> (some need no API key at all), then add its endpoint / API key in <span className="text-on-surface">Providers</span> if it asks for one.
          </div>
        )}
        {missingSearchTool && (
          <div data-type="body-s" className={NOTE_SHELL}>
            Binding a provider here is not enough on its own: the agent has no <code className="text-on-surface">{SEARCH_TOOL}</code> tool yet, so a chat turn cannot search. It ships in the <span className="text-on-surface">{SEARCH_TOOL_APP.label}</span> app — <TextLink href={SEARCH_TOOL_APP_HREF} ink="emphasis" className="underline">install it from the Store</TextLink>.
          </div>
        )}
        {active === null
          ? <InlineError icon onRetry={refresh}>Couldn't read which provider each use case is bound to, so none can be changed until a retry succeeds.</InlineError>
          : USE_CASE_ORDER.map((uc) => (
            <UseCaseRow key={uc} useCase={uc} activeProviders={active[uc] ?? []} providers={providers} onChanged={reloadActive} />
          ))}
      </Section>
    </div>
  )
}

/** A row in a use-case's provider picker: a registered provider, or a binding whose
 *  provider is NO LONGER REGISTERED (its app was uninstalled or deactivated).
 *
 *  Both variants carry `name`, so the row's selection test stays the plain
 *  `activeProviders.includes(row.name)` — the spelling `exclusiveChoiceNamed`'s membership
 *  matcher sweeps for. Hoisting the name out into a local instead would leave that detector
 *  matching nothing here, which reads exactly like a clean tree. */
export type PickableProvider =
  | { kind: 'registered'; name: string; provider: SearchProviderInfo }
  | { kind: 'stale'; name: string }

/** Which providers a use-case may bind, PLUS every provider it is ALREADY bound to.
 *
 *  The second half is the part that was missing. `eligible` alone is what the registry
 *  currently offers, and a binding outlives the thing it points at: uninstalling (or
 *  deactivating) a bound provider app deregisters the provider and leaves
 *  `active_search_providers.json` untouched — deliberately, so reinstalling restores the
 *  choice. The write path already refuses to CREATE that state
 *  (`search_registry.api_search_active_set`: "silently stranding the use-case on a dead
 *  provider name"), and the resolver already stops using it
 *  (`search_providers/registry.py`: "a bound name whose provider isn't registered
 *  (disabled/removed) → fall through to the implicit fallback"). Only the picker still
 *  believed it: the row's subtitle printed the stored name as the active binding while its
 *  body said "No search providers configured", and — because the chip list was built from
 *  `eligible` alone — offered no control to clear it. A binding the user cannot see the
 *  state of and cannot remove is the phantom binding `ModelsPanel.capableModels` adds its
 *  synthetic row for; this is the same rule for the Search entity.
 *
 *  One rule covers both reasons a binding can be absent from `eligible`: the provider is
 *  gone (`stale`), or it is registered but not eligible for THIS use-case (a `fetch-article`
 *  binding to a provider without `supports_fetch`). The second still resolves at runtime —
 *  step 1 checks registration, not capability — so it is shown as itself, not as stale.
 *  Pure + exported for unit testing. */
export function pickableProviders(
  useCase: string, providers: SearchProviderInfo[], activeProviders: string[],
): PickableProvider[] {
  // For fetch-article, only a provider that can extract content is a sensible bind;
  // every other use-case can bind any provider.
  const eligible = useCase === 'fetch-article'
    ? providers.filter((p) => p.capabilities.supports_fetch)
    : providers
  const out: PickableProvider[] = eligible.map((p) => ({ kind: 'registered', name: p.name, provider: p }))
  for (const name of activeProviders) {
    if (eligible.some((p) => p.name === name)) continue
    const registered = providers.find((p) => p.name === name)
    out.push(registered
      ? { kind: 'registered', name, provider: registered }
      : { kind: 'stale', name })
  }
  return out
}

function UseCaseRow({ useCase, activeProviders, providers, onChanged }: {
  useCase: string; activeProviders: string[]; providers: SearchProviderInfo[]; onChanged: () => void
}) {
  // No `open` flag here: `DisclosureCard` owns the disclosure state, which is the only thing this
  // component ever used it for.
  const [saving, setSaving] = useState(false)
  // Each provider's row as its latest Test returned it, shown over the row it was tested from until
  // the panel's own re-read brings a newer one.
  const [tested, setTested] = useState<Record<string, { over: SearchProviderInfo; row: SearchProviderInfo }>>({})
  const meta = USE_CASE_META[useCase] ?? { label: useCase, description: '', icon: Globe }
  const rows = pickableProviders(useCase, providers, activeProviders)
  // The count pill is how many this use-case can BIND, so it counts registered rows only —
  // a stale binding is not an option on offer.
  const bindable = rows.filter((r) => r.kind === 'registered').length
  const staleActive = activeProviders.filter((n) => !providers.some((p) => p.name === n))

  // Reported, and the re-read gated on the answer — the same no-catch shape as the provider cards'
  // switches: a refused change rejected unhandled and the row simply did not move.
  const setActive = async (names: string[]) => {
    setSaving(true)
    try {
      if (await reportingWrite(`set the ${meta.label} provider`, () => api.setActiveSearchProvider(useCase, names))) onChanged()
    } finally { setSaving(false) }
  }
  // Single-select: clicking the active provider clears it; clicking another swaps.
  const toggle = (name: string) => setActive(activeProviders.includes(name) ? [] : [name])

  return (
    <DisclosureCard icon={meta.icon} label={meta.label} active={activeProviders.length > 0} count={bindable}
      subtitle={activeProviders.length > 0
        ? (staleActive.includes(activeProviders[0])
          // Not "duckduckgo" on its own: the provider is gone, so the name alone claims an
          // active binding that nothing can serve. Say what it resolves to instead.
          ? <span>{activeProviders[0]} <span className="italic text-on-surface-low">— not installed; falls back to any available provider</span></span>
          : activeProviders[0])
        : <span className="italic">none — falls back to General</span>}>
      <p data-type="body-s" className="text-on-surface-low">{meta.description}</p>
      {rows.length === 0 ? (
        <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low italic">
          {useCase === 'fetch-article'
            ? 'No configured provider can extract page content. Bind one with fetch support (e.g. Tavily), or leave this unset to use the native fetch pipeline.'
            : 'No search providers configured. Add one in Providers first.'}
        </div>
      ) : (
        // 🔴 One-of-N, and the bind was announced by COLOUR alone: the filled circle and the
        //    tinted row are the only thing that said "this provider is bound". The group is named
        //    with its use-case so the four sibling groups on this panel are distinguishable —
        //    "General search provider" and "News search provider", not four identical lists.
        <div role="group" aria-label={`${meta.label} provider`}
          className="-m-1 flex flex-col gap-0.5 p-1" style={{ opacity: saving ? 0.6 : 1 }}>
          {rows.map((row) => {
            const on = activeProviders.includes(row.name)
            // What the Test found shows at once, before the panel's re-read lands.
            const t = tested[row.name]
            const provider = row.kind === 'registered' ? (t && t.over === row.provider ? t.row : row.provider) : null
            return (
              <div key={row.name} className="flex flex-col gap-0.5">
                <div className="flex items-center gap-1.5">
                  <button type="button" onClick={() => toggle(row.name)} disabled={saving}
                    aria-pressed={on}
                    className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-3 py-2 text-left transition-colors hover:bg-surface-high"
                    style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' } : undefined}>
                    <span className="grid size-4 shrink-0 place-items-center rounded-full border"
                      style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>
                      {on && <Check size={10} strokeWidth={3} className="text-on-primary" />}
                    </span>
                    <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">
                      {provider ? provider.display_name : row.name}
                    </span>
                    {provider && <CapChips caps={provider.capabilities} />}
                    {/* Each provider's state is what its last search MEASURED (`searchProviderState`):
                        `working`, `failed`, `not checked`, `no key needed`, or `not configured` when it
                        lacks a key. `ready` used to stand for a key merely being present. `not
                        installed` is about a provider that is gone, and it is the only one whose
                        remedy is the Store rather than a key. Clicking it clears the binding — the
                        one action a provider that is gone can still offer. */}
                    {provider
                      ? <SearchStatePill provider={provider} />
                      : <StatusPill tone="warn" className="py-0.5">not installed</StatusPill>}
                  </button>
                  {/* Beside the choice, not in it: testing a provider is not choosing it. */}
                  {provider && row.kind === 'registered' && (
                    <SearchTestButton provider={provider}
                      onTested={(found) => setTested((cur) => ({ ...cur, [found.name]: { over: row.provider, row: found } }))} />
                  )}
                </div>
                {provider && <div className="px-m"><SearchFailure provider={provider} /></div>}
              </div>
            )
          })}
        </div>
      )}
    </DisclosureCard>
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
        <span key={c.label} title={c.title} data-type="caption" className="inline-flex items-center gap-0.5 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low uppercase tracking-wide">
          <Zap size={8} className="text-primary" />{c.label}
        </span>
      ))}
    </span>
  )
}
