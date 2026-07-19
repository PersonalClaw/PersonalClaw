import { PromptsListPage } from './PromptsListPage'
import { PromptCreatePage } from './PromptCreatePage'
import { PromptViewPage } from './PromptViewPage'
import type { PromptKind } from '../../lib/api'
import type { RouteProps } from '../../app/useQueryState'

type ViewKind = PromptKind | 'snippets'

/** Prompts navigation — URL-addressable: `#/prompts` (list with System/User/
 *  Snippets tabs via ?tab; search/sort/filter via ?q/?sort/?src; quick-open via
 *  ?open), `#/prompts/new` (create; ?kind=user|system|snippets), and
 *  `#/prompts/view?kind=&name=` (dedicated full-page view/edit). */
export function PromptsSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const head = (sub || '').split('/')[0]
  if (head === 'new') {
    const mode = (query['kind'] || 'user') as ViewKind
    return <PromptCreatePage mode={mode} onBack={() => navigate('prompts')} onCreated={() => navigate('prompts')} />
  }
  if (head === 'view') {
    const kind = (query['kind'] || 'user') as ViewKind
    const name = query['name'] || ''
    if (!name) { navigate('prompts'); return null }
    return <PromptViewPage kind={kind} name={name} navigate={navigate} query={query} setQuery={setQuery}
      onBack={() => navigate(`prompts?tab=${kind}`)} />
  }
  return <PromptsListPage key={navEpoch}
    onCreate={(tab) => navigate(`prompts/new?kind=${tab}`)}
    onOpen={(tab, name, opts) => navigate(`prompts/view?kind=${tab}&name=${encodeURIComponent(name)}${opts?.edit ? '&edit=1' : ''}`)}
    navigate={navigate} query={query} setQuery={setQuery} />
}
