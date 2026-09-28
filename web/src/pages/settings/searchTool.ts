import type { ToolItem } from '../../lib/api'

/** The tool that CALLS whatever search provider is bound. A search provider is inert without it:
 *  binding one registers a provider, not a tool the agent can invoke (#278 — a user installed
 *  DuckDuckGo, bound it, saw `available: true`, and chat still answered "no web-search tool in the
 *  catalog"). Predicated on the TOOL NAME, not on the app that ships it, so any app providing
 *  `web_search` satisfies the check and no bundle name gates it.
 *
 *  Its own module because three surfaces state this fact — the Search panel's note, the hub's
 *  Search tile and first run's Web search lane — and must not state it three ways. */
export const SEARCH_TOOL = 'web_search'

/** The app that ships `web_search` today — named in COPY, in a Store deep link, and to find the
 *  catalog card first run offers beside a search provider (the user has to get it from somewhere),
 *  never in the predicate. `open=<name>` opens that Store card's detail panel; an unknown name
 *  degrades to the plain Store grid rather than erroring, so a link built from this cannot strand a
 *  user if the bundle is ever renamed.
 *
 *  `label` is the app's own display name, for a surface with no catalog entry to read it from. It
 *  was "Native Tools (Web)", and "native" is the word for an app that ships WITH PersonalClaw, which
 *  this one does not: it is installed from the Store, so a note naming it told a user to install
 *  something the name said they already had. */
export const SEARCH_TOOL_APP = { name: 'web-tools', label: 'Web Tools' }

/** Whether a tool list carries the tool that calls a search provider. */
export const hasSearchTool = (tools: ToolItem[]) => tools.some((t) => t.name === SEARCH_TOOL)
