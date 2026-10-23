import { useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'

import { viewTransition } from '../design/motion'
import { redirectFor } from './routeRedirects'

/** Minimal hash router — keeps the active page + view-state in `location.hash`
 *  so every page, sub-page, tab, filter, and open panel is URL-addressable:
 *  refresh and back/forward land exactly where the user was. Zero deps, no server
 *  rewrite (works behind the static serve + dev proxy).
 *
 *  Grammar: `#/<route>/<sub...>?<query>`
 *   - `route` — first path segment (drives the nav switch).
 *   - `sub`   — the rest of the path (structural: sub-page / create / detail id),
 *               e.g. `history`, `new`, `<id>`, `tab/sub`.
 *   - `query` — `?key=val&…` for EPHEMERAL view-state (active tab, view mode,
 *               filter, search, which detail panel is open). Collision-free with
 *               path ids and freely combinable.
 *
 *  `navigate(path, { replace })` sets the full `#/<path>` (path may include its
 *  own `?query`). `setQuery(patch, { replace })` merges query params onto the
 *  CURRENT route+sub without touching the path, and only while that route+sub is
 *  still the page the hook rendered: a page's late write never lands on another's. Default is push (a history
 *  entry); pass `replace: true` to overwrite the current entry instead. Pass
 *  `remount: true` when the update is a navigation for the page: it bumps
 *  `navEpoch` as `navigate` does, so a page keyed on it mounts afresh.
 *
 *  A retired address (`routeRedirects.ts`) never reaches a page: it is rewritten to where it opens
 *  now when the hook first reads the bar, on every `hashchange` (Back, Forward, a link the desktop
 *  shell or a notification opens) and on every `navigate`/`setQuery`, in place of the history entry
 *  it would have had. */
interface HashRoute {
  route: string
  sub: string
  /** parsed `?query` params for the current URL (ephemeral view-state). */
  query: Record<string, string>
  /** bumps on every navigate()/setQuery() — fold into a component key to force a
   *  fresh remount even when route/sub are unchanged (New Chat fix). */
  navEpoch: number
  navigate: (path: string, opts?: { replace?: boolean }) => void
  /** merge query params onto the current route+sub; null/'' value removes a key. */
  setQuery: (patch: Record<string, string | null | undefined>, opts?: { replace?: boolean; remount?: boolean }) => void
}

function rawHash(): string {
  return (typeof location !== 'undefined' ? location.hash : '').replace(/^#\/?/, '')
}

/** Rewrite a retired address in the bar to where it opens now (`routeRedirects.ts`), replacing
 *  its history entry, so Back never returns to it. The resolver reads the bar as written and drops
 *  its one `#/`, as `rawHash` does: handed `rawHash()`, it would drop a second one the router reads
 *  as a route. */
function settleHash(): void {
  if (typeof location === 'undefined') return
  const moved = redirectFor(location.hash)
  if (moved !== null) history.replaceState(null, '', `#/${moved}`)
}

function parseHash(fallback: string): { route: string; sub: string; query: Record<string, string> } {
  const h = rawHash()
  const qIdx = h.indexOf('?')
  const path = qIdx >= 0 ? h.slice(0, qIdx) : h
  const qs = qIdx >= 0 ? h.slice(qIdx + 1) : ''
  // Decode each path segment — the URL stores them percent-encoded (a session
  // key / id may contain spaces or other reserved chars), but the app works with
  // the decoded value. Decode per-segment (not the whole path) so an encoded "/"
  // inside a segment never gets mistaken for a separator. Query params are
  // already decoded by URLSearchParams.
  const decodeSeg = (s: string) => { try { return decodeURIComponent(s) } catch { return s } }
  const segs = path.split('/').filter(Boolean).map(decodeSeg)
  const query: Record<string, string> = {}
  if (qs) for (const [k, v] of new URLSearchParams(qs)) query[k] = v
  return { route: segs[0] || fallback, sub: segs.slice(1).join('/'), query }
}

/** Build the `path?query` string from a base path + a query object (drops empty). */
function buildHash(path: string, query: Record<string, string>): string {
  const clean = path.replace(/^#?\/?/, '').split('?')[0]
  const entries = Object.entries(query).filter(([, v]) => v !== '' && v != null)
  if (!entries.length) return clean
  const usp = new URLSearchParams()
  for (const [k, v] of entries) usp.set(k, v)
  return `${clean}?${usp.toString()}`
}

export function useHashRoute(fallback: string): HashRoute {
  // Settled before the first read, so the first render already has the new address: a page that
  // drew for the old one would run its effects, its reads among them, for an address that is gone.
  const [state, setState] = useState(() => { settleHash(); return parseHash(fallback) })
  const [navEpoch, setNavEpoch] = useState(0)
  /** The route last applied to `state` — a mirror, never a source of truth. It exists
   *  because the `hashchange` listener below is registered once with `[]` deps, so its
   *  closure would hold the FIRST render's `state.route` forever; and it decides only
   *  whether to crossfade, never what to render. */
  const applied = useRef(state.route)

  /** Pull the live hash into React state. The one place `state` is written. */
  const applyHash = () => {
    const next = parseHash(fallback)
    applied.current = next.route
    setState(next)
  }

  useEffect(() => {
    // The ONE animated seam. Every navigation the
    // user performs arrives here as a `hashchange` — a nav click (apply's push branch
    // below) and browser back/forward alike — so wrapping this single listener
    // crossfades all of them with no page opting in.
    //
    // COSMETIC ONLY, three ways:
    //  • The URL is written by `apply` BEFORE this runs and outside any transition, so
    //    the address bar and history can never be gated on an animation.
    //  • `viewTransition` runs its callback on every path (no API, a throwing API) and
    //    never awaits the animation, so the state change can't be lost either.
    //  • Nothing about focus or scroll is touched: the transition animates snapshots
    //    of the old and new frames, and the real DOM swap is the same one React
    //    already did — a route change moves focus exactly as far as it did before.
    //
    // Only a `route` (first path segment) change animates. Sub-page, tab, filter and
    // search updates land here too, and crossfading the whole page on every keystroke
    // of a search box would fight the task instead of decorating it.
    //
    // `flushSync` is load-bearing: the browser captures the "after" snapshot as soon as
    // the callback returns, while React 18 commits a plain `setState` later on the
    // scheduler — without the flush BOTH snapshots would be the old DOM and the
    // crossfade would animate nothing. It is safe precisely here because a native
    // listener is never inside a React render or commit.
    const onHash = () => {
      settleHash()
      if (parseHash(fallback).route === applied.current) { applyHash(); return }
      viewTransition(() => { flushSync(applyHash) })
    }
    window.addEventListener('hashchange', onHash)
    if (!location.hash) location.replace(`#/${fallback}`)
    return () => window.removeEventListener('hashchange', onHash)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // bumpEpoch: only path navigation bumps navEpoch (which pages fold into a `key`
  // to force a fresh mount — e.g. re-clicking the active nav item resets the page).
  // A same-page query update (setQuery: open an item, change a filter/tab) must NOT
  // bump it, or the whole page remounts + refetches before the panel opens.
  const apply = (written: string, replace: boolean, bumpEpoch: boolean) => {
    // A retired address is never written: its new one is, as the one history entry. The resolver
    // reads the bar this would write, `#/` and all.
    const next = redirectFor(`#/${written}`) ?? written
    if (bumpEpoch) setNavEpoch((n) => n + 1)
    const live = rawHash()
    if (live === next) { applyHash(); return }
    if (replace) {
      // replaceState doesn't emit hashchange — re-sync state ourselves. A replace is a
      // URL CORRECTION (a redirect, an in-place refinement), not a navigation the user
      // performed, so it deliberately does not crossfade.
      history.replaceState(null, '', `#/${next}`)
      applyHash()
    } else {
      location.hash = `#/${next}`   // emits hashchange → onHash re-parses (+ crossfades)
    }
  }

  const navigate = (path: string, opts?: { replace?: boolean }) => {
    // path may carry its own ?query; preserve it verbatim.
    const clean = path.replace(/^#?\/?/, '')
    apply(clean, !!opts?.replace, true)
  }

  // A page's query belongs to that page's history entry. `setQuery` is made for the page this hook
  // last rendered (`state`), and a call that lands once another page is in the bar is that page's
  // late write, not a change to the one now showing: dropped, never merged onto it. The code
  // cockpit opened at its bare address writes its first change (`diff=`) when the workspace's
  // changes arrive; pressed Back before then, that write used to land on the page before (CI read
  // Settings with `?diff=…` in its address: compatibilityFixtures.spec.ts, #3905).
  const ownPage = { route: state.route, sub: state.sub }
  const setQuery = (patch: Record<string, string | null | undefined>, opts?: { replace?: boolean; remount?: boolean }) => {
    const cur = parseHash(fallback)
    if (cur.route !== ownPage.route || cur.sub !== ownPage.sub) return
    const merged = { ...cur.query }
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === '') delete merged[k]
      else merged[k] = v
    }
    const basePath = [cur.route, cur.sub].filter(Boolean).join('/')
    apply(buildHash(basePath, merged), !!opts?.replace, !!opts?.remount)
  }

  return { route: state.route, sub: state.sub, query: state.query, navEpoch, navigate, setQuery }
}
