import { KnowledgeListPage } from './KnowledgeListPage'
import { KnowledgeCreatePage } from './KnowledgeCreatePage'
import { KnowledgeDetailPage } from './KnowledgeDetailPage'
import { SourcesPage } from './SourcesPage'
import { SourceCreatePage } from './SourceCreatePage'
import type { RouteProps } from '../../app/useQueryState'

/** Knowledge navigation — URL-addressable: `#/knowledge` (list; view toggle
 *  Library/Graph/Intents + type/provider filter + search via ?query),
 *  `#/knowledge/new` (type-grid → per-type authoring), `#/knowledge/item/<id>`
 *  (the dedicated full-screen item detail page), and `#/knowledge/sources`
 *  (+ `/sources/new`) — the watched sources that fill the library on their own
 *  (WATCHED-SOURCES §2.4). */
export function KnowledgeSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const parts = (sub || '').split('/')
  if (parts[0] === 'new')
    return <KnowledgeCreatePage onBack={() => navigate('knowledge')} onCreated={() => navigate('knowledge')} />
  if (parts[0] === 'sources' && parts[1] === 'new')
    return <SourceCreatePage onBack={() => navigate('knowledge/sources')} onCreated={() => navigate('knowledge/sources')} />
  if (parts[0] === 'sources')
    return <SourcesPage key={navEpoch} onBack={() => navigate('knowledge')} onCreate={() => navigate('knowledge/sources/new')} />
  if (parts[0] === 'item' && parts[1])
    return <KnowledgeDetailPage key={parts[1]} id={parts[1]} onBack={() => navigate('knowledge')} onOpenItem={(id) => navigate(`knowledge/item/${id}`)} query={query} setQuery={setQuery} />
  return <KnowledgeListPage key={navEpoch} onCreate={() => navigate('knowledge/new')} onOpenItem={(id) => navigate(`knowledge/item/${id}`)} onOpenSources={() => navigate('knowledge/sources')} query={query} setQuery={setQuery} />
}
