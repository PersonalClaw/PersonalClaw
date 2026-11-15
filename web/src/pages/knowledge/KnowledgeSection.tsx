import { KnowledgeListPage } from './KnowledgeListPage'
import { KnowledgeCreatePage } from './KnowledgeCreatePage'
import { KnowledgeDetailPage } from './KnowledgeDetailPage'
import { MODELS_PATH } from '../chat/NoModelSetupState'
import type { RouteProps } from '../../app/useQueryState'
import { useItemBack } from '../../app/itemBack'

/** The knowledge store's own pages: `#/knowledge/new` (type grid, then per-type authoring) and
 *  `#/knowledge/item/<id>` (the full-screen item page, whose back control returns to the library
 *  with it selected, or to the project `?back=project:<id>` names). The list, the Sources and the
 *  Reports pages are the library's now (#6101): their addresses are redirect rows onto it, so the
 *  router hands this section only a deeper path no row retires. */
export function KnowledgeSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const parts = (sub || '').split('/')
  const itemId = parts[0] === 'item' ? parts[1] ?? '' : ''
  const back = useItemBack(`knowledge:${itemId}`, query.back)
  if (parts[0] === 'new')
    return <KnowledgeCreatePage onBack={() => navigate('library')} onCreated={() => navigate('library')} />
  if (itemId)
    return <KnowledgeDetailPage key={itemId} id={itemId} backLabel={back.label} onBack={() => navigate(back.path)} onOpenItem={(id) => navigate(`knowledge/item/${id}`)} query={query} setQuery={setQuery} />
  // `onOpenReader` is the same destination with the reading mode already on (`?read=1`, the param
  // KnowledgeDetailPage reads) — the library home's continue-reading shelf resumes INTO the reader,
  // not into the metadata view, because the position it saved belongs to the reader's scroller.
  return <KnowledgeListPage key={navEpoch} onCreate={() => navigate('knowledge/new')} onOpenItem={(id) => navigate(`knowledge/item/${id}`)} onOpenReader={(id) => navigate(`knowledge/item/${id}?read=1`)} onOpenSources={() => navigate('knowledge/sources')} onOpenReports={() => navigate('knowledge/reports')} onOpenChat={() => navigate('chat')} onOpenModels={() => navigate(MODELS_PATH)} query={query} setQuery={setQuery} />
}
