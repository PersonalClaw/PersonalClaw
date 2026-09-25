import type { ToolItem } from '../../lib/api'

/** The tool that CALLS whatever search provider is bound. A search provider is inert without it:
 *  binding one registers a provider, not a tool the agent can invoke (#278 — a user installed
 *  DuckDuckGo, bound it, saw `available: true`, and chat still answered "no web-search tool in the
 *  catalog"). Predicated on the TOOL NAME, not on the app that ships it, so any app providing
 *  `web_search` satisfies the check and no bundle name gates it.
 *
 *  Its own module because two surfaces state this fact — the Search panel's note and the hub's
 *  Search tile — and must not state it two ways. */
export const SEARCH_TOOL = 'web_search'

/** The app that ships `web_search` today — named in COPY and in a Store deep link only (the user
 *  has to find it there), never in the predicate. `open=<name>` opens that Store card's detail
 *  panel; an unknown name degrades to the plain Store grid rather than erroring, so a link built
 *  from this cannot strand a user if the bundle is ever renamed. */
export const SEARCH_TOOL_APP = { name: 'web-tools', label: 'Native Tools (Web)' }

/** Whether a tool list carries the tool that calls a search provider. */
export const hasSearchTool = (tools: ToolItem[]) => tools.some((t) => t.name === SEARCH_TOOL)
