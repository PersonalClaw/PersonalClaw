import { useEffect } from 'react'
import { KnowledgeCreatePage } from './KnowledgeCreatePage'
import { KnowledgeDetailPage } from './KnowledgeDetailPage'
import type { RouteProps } from '../../app/useQueryState'
import { useItemBack } from '../../app/itemBack'

/** The knowledge store's own pages: `#/knowledge/new` (type grid, then per-type authoring) and
 *  `#/knowledge/item/<id>` (the full-screen item page, whose back control returns to the library
 *  with it selected, or to the project `?back=project:<id>` names). The list, the Sources and the
 *  Reports pages are the library's now (#6101): their addresses are redirect rows onto it, so the
 *  router hands this section only a deeper path no row retires, which opens the library in its place. */
export function KnowledgeSection({ sub, navigate, query, setQuery }: RouteProps) {
  const parts = (sub || '').split('/')
  const itemId = parts[0] === 'item' ? parts[1] ?? '' : ''
  const back = useItemBack(`knowledge:${itemId}`, query.back)
  if (parts[0] === 'new')
    return <KnowledgeCreatePage onBack={() => navigate('library')} onCreated={() => navigate('library')} />
  if (itemId)
    return <KnowledgeDetailPage key={itemId} id={itemId} backLabel={back.label} onBack={() => navigate(back.path)} onOpenItem={(id) => navigate(`knowledge/item/${id}`)} query={query} setQuery={setQuery} />
  return <ToLibrary navigate={navigate} />
}

/** An address under `#/knowledge` that names no page: the library, in its place in history. */
function ToLibrary({ navigate }: Pick<RouteProps, 'navigate'>) {
  useEffect(() => { navigate('library', { replace: true }) }, [navigate])
  return null
}
