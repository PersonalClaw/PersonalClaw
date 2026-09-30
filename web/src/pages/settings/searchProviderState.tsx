import { useState } from 'react'
import { Beaker } from 'lucide-react'
import { api, type SearchProviderInfo } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { fullStamp } from '../../lib/epoch'
import { reportingWrite } from '../../app/reportingWrite'
import { Button } from '../../ui/Button'
import { StatusPill } from '../../ui/StatusPill'

/** What a search provider's state IS, as its pill says it — from what was MEASURED, never from its
 *  settings. `available` says only that it has what it needs to try (a key, an endpoint); a key
 *  that is present is not a key that is accepted, so "ready" is a word no state here uses. `check`
 *  is its last search (its Test, or a real one), and a provider nothing has measured says so. */
export function searchState(p: SearchProviderInfo): { word: string; tone: 'ok' | 'danger' | null; title: string } {
  const when = p.check ? fullStamp(p.check.checked_at) : ''
  if (!p.available) {
    return { word: 'not configured', tone: null, title: 'It is missing what it needs to search, an API key or an endpoint. Add it in Settings → Providers.' }
  }
  if (p.check?.state === 'ok') {
    return { word: 'working', tone: 'ok', title: `Its last search worked${when ? ` (${when})` : ''}.` }
  }
  if (p.check?.state === 'failed') {
    return { word: 'failed', tone: 'danger', title: `Its last search failed${when ? ` (${when})` : ''}: ${p.check.detail}` }
  }
  if (p.capabilities.keyless) {
    return { word: 'no key needed', tone: null, title: 'It needs no API key. Test runs one search through it.' }
  }
  return { word: 'not checked', tone: null, title: 'Its key is saved, but no search has tried it yet. Test runs one search with it.' }
}

/** The state as a pill. The two toned states ride `ui/StatusPill`; the neutral ones keep the solid
 *  surface ground the panel's `not configured` always had, which is a ground and not a tone tint. */
export function SearchStatePill({ provider }: { provider: SearchProviderInfo }) {
  const s = searchState(provider)
  if (s.tone) return <StatusPill tone={s.tone} className="py-0.5" title={s.title}>{s.word}</StatusPill>
  return (
    <span data-type="caption" title={s.title} className="shrink-0 rounded-pill px-1.5 py-0.5"
      style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>{s.word}</span>
  )
}

/** The provider's Test: one search through it now. What it found comes back as the provider's row
 *  (`onTested`), and every search surface re-reads, so the panel and the app's card agree. */
export function SearchTestButton({ provider, onTested }: {
  provider: SearchProviderInfo
  onTested: (tested: SearchProviderInfo) => void
}) {
  const [testing, setTesting] = useState(false)
  const test = async () => {
    setTesting(true)
    try {
      let tested: SearchProviderInfo | undefined
      const done = await reportingWrite(`test ${provider.display_name}`, async () => { tested = await api.testSearchProvider(provider.name) })
      if (done && tested) {
        onTested(tested)
        invalidateKeys('settings:search', true)
      }
    } finally { setTesting(false) }
  }
  return (
    <Button size="xs" variant="secondary" className="shrink-0" loading={testing} loadingLabel="Testing…"
      ariaLabel={`Test: ${provider.display_name}`} title="Runs one search through it, now" onClick={test}>
      <Beaker size={11} aria-hidden /> Test
    </Button>
  )
}

/** A failed search's own words, under the provider it failed for. */
export function SearchFailure({ provider }: { provider: SearchProviderInfo }) {
  if (provider.check?.state !== 'failed') return null
  return <p data-type="caption" className="break-words text-on-surface-low">{provider.check.detail}</p>
}

/** A search app's card in Settings → Providers: the state its provider was last measured in, and
 *  its Test — the same two things a model provider's card and a channel's card carry. */
export function SearchProviderCheck({ provider }: { provider: SearchProviderInfo }) {
  const [shown, setShown] = useState(provider)
  const [base, setBase] = useState(provider)
  if (provider !== base) { setBase(provider); setShown(provider) }
  return (
    <div className="mt-s flex flex-col gap-xs border-t border-outline-variant/30 pt-s">
      <div className="flex items-center gap-s">
        <span data-type="caption" className="text-on-surface-var">Search</span>
        <SearchStatePill provider={shown} />
        <div className="ml-auto"><SearchTestButton provider={shown} onTested={setShown} /></div>
      </div>
      <SearchFailure provider={shown} />
    </div>
  )
}
