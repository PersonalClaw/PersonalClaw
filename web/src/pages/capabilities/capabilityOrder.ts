/** The named orders of Capabilities' lists (#3817, the `named-order` rule).
 *
 *  Catalogues are looked up by name, so each tab lists by name, A–Z, case-insensitively, and breaks
 *  a tie between equal names by the item's id, so two items named alike keep one order on every
 *  read and every machine. Every order here is the page's own: no route orders these rows for the
 *  page to show (the skills route lists in the order a copy wins, which is not an order to read by).
 *  `everyCapabilitiesTabShowsItsNamedOrder.test.tsx` drives each with shuffled rows.
 *
 *  A tab with a sort control (Prompts, Apps, the Store) starts on name order and names the order it
 *  shows on the control (`orderSaid`). */

/** Names as a reader looks them up: a letter is the same letter in either case, while an accent
 *  still tells two letters apart. */
const NAMES = new Intl.Collator(undefined, { sensitivity: 'accent' })

/** Two names, A–Z, case-insensitive. */
export function byName(a: string, b: string): number {
  return NAMES.compare(a, b)
}

/** Two ids in code-point order: the tie-breaker, the same whatever the reader's language. */
export function byId(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/** Agents: by display name, A–Z, case-insensitive; equal names by the agent's id. A native agent's
 *  name is its id. */
export function compareAgents(a: { name: string; id?: string }, b: { name: string; id?: string }): number {
  return byName(a.name, b.name) || byId(a.id ?? a.name, b.id ?? b.name)
}

/** Skills: by name, A–Z, case-insensitive; equal names (an agent's own copy beside the library's)
 *  by the skill's id. */
export function compareSkills(a: { name: string; key: string }, b: { name: string; key: string }): number {
  return byName(a.name, b.name) || byId(a.key, b.key)
}

/** A provider's tools: by name, A–Z, case-insensitive. The name is the tool's full name and its id,
 *  so two names equal A–Z differ only in case, and the tie goes by the same name in code-point
 *  order, capitals first, whatever order the tools were read in. */
export function compareTools(a: { name: string }, b: { name: string }): number {
  return byName(a.name, b.name) || byId(a.name, b.name)
}

/** Tool providers: the built-in ones by label, then the connected servers by name, each A–Z,
 *  case-insensitive, equal labels by the provider's key. A server's place no longer follows the order
 *  it was configured in, so adding one leaves the others where they are. */
export function compareToolProviders(
  a: { kind: 'native' | 'mcp'; label: string; key: string },
  b: { kind: 'native' | 'mcp'; label: string; key: string },
): number {
  if (a.kind !== b.kind) return a.kind === 'native' ? -1 : 1
  return byName(a.label, b.label) || byId(a.key, b.key)
}

/** Prompts and snippets: by title, or name where there is none, A–Z, case-insensitive; equal titles
 *  by name. */
export function comparePrompts(a: { name: string; title?: string }, b: { name: string; title?: string }): number {
  return byName(a.title || a.name, b.title || b.name) || byId(a.name, b.name)
}

/** Apps and Store listings: by display name, A–Z, case-insensitive; equal names by the app's name,
 *  which is a listing's id. The Store shows them within each source group, the groups in the order
 *  its Sources rail lists them. */
export function compareApps(a: { name: string; displayName: string }, b: { name: string; displayName: string }): number {
  return byName(a.displayName, b.displayName) || byId(a.name, b.name)
}

/** What a sort control says about the order it shows, for each order a Capabilities tab offers. */
const SAID: Record<string, string> = {
  name: 'Sorted by name',
  updated: 'Recently updated first',
  installed: 'Recently installed first',
  status: 'Enabled first',
  type: 'Sorted by type',
  source: 'Sorted by source',
  vars: 'Most variables first',
  author: 'Sorted by author',
}

/** The words a sort control shows for the order `sort`. */
export function orderSaid(sort: string): string {
  return SAID[sort] ?? SAID.name
}
