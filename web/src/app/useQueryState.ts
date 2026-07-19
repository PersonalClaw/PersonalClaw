/** Per-page view-state props threaded from the hash router. Every top-level page
 *  receives these so its tabs / filters / search / open-panel id can live in the
 *  URL (deep-linkable, refresh- + back/forward-safe). */
export interface RouteProps {
  sub: string
  navigate: (path: string, opts?: { replace?: boolean }) => void
  navEpoch: number
  query: Record<string, string>
  setQuery: (patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => void
}

/** Read a single query param with a default. */
export function qget(query: Record<string, string>, key: string, dflt = ''): string {
  return query[key] ?? dflt
}

/** A `useState`-shaped binding to a single URL query param. Returns the current
 *  value (or `dflt`) and a setter that writes it to the URL (pushing a history
 *  entry by default). Setting the value back to `dflt` drops the param to keep
 *  the URL clean. Lets a page deep-link a tab/filter/search/open-id with a
 *  one-line change from `useState`. */
export function useQueryParam(
  query: Record<string, string>,
  setQuery: RouteProps['setQuery'],
  key: string,
  dflt = '',
  opts?: { replace?: boolean },
): [string, (v: string) => void] {
  const value = query[key] ?? dflt
  const set = (v: string) => setQuery({ [key]: v === dflt ? null : v }, opts)
  return [value, set]
}

/** A boolean URL-flag: present-as `'1'` ↔ absent. Convenience over `useQueryParam`
 *  so pages stop repeating the `raw === '1'` read + `v ? '1' : ''` write boilerplate
 *  for on/off panels & toggles (Inbox `?settings`, Tools `?add`, etc.). Defaults to
 *  PUSH (opening a panel is a Back-undoable destination); pass `{replace:true}` for a
 *  lightweight in-place toggle that shouldn't accumulate history. */
export function useQueryFlag(
  query: Record<string, string>,
  setQuery: RouteProps['setQuery'],
  key: string,
  opts?: { replace?: boolean },
): [boolean, (on: boolean) => void] {
  const on = query[key] === '1'
  const set = (v: boolean) => setQuery({ [key]: v ? '1' : null }, opts)
  return [on, set]
}

/** The `?edit=1` view↔edit toggle for an open record's detail panel — the canonical
 *  model's "edit-vs-view of an open item" row. Entering edit PUSHES (so Back leaves
 *  edit mode); leaving via Cancel/Save REPLACES (collapses the edit entry so Back
 *  doesn't drop the user back INTO edit). The record identity itself lives in a
 *  separate `?open`/record param — this owns only the mode. Detail components take
 *  `editing` + `onEditingChange` from here and are fully controlled, so clicking
 *  Edit/Cancel in the panel keeps the URL and the view in lockstep. */
export function useEditFlag(
  query: Record<string, string>,
  setQuery: RouteProps['setQuery'],
): [boolean, (on: boolean) => void] {
  const on = query['edit'] === '1'
  const set = (v: boolean) => setQuery({ edit: v ? '1' : null }, { replace: !v })
  return [on, set]
}
