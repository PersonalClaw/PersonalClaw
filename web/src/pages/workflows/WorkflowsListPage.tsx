import { useMemo } from 'react'
import { Plus, Workflow as WorkflowIcon, ListOrdered, Eye, Pencil } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { ListControls } from '../../ui/ListControls'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { SidePanel } from '../../ui/SidePanel'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type WorkflowItem } from '../../lib/api'
import { scopeMeta } from './workflowMeta'
import { WorkflowDetail } from './WorkflowDetail'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/useQueryState'

export function WorkflowsListPage({ onCreate, query, setQuery }: { onCreate: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: items, refresh } = useCachedData<WorkflowItem[]>('workflows', () => api.workflows().catch(() => []), { persist: true })
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null
  const [editing, setEditing] = useEditFlag(query, setQuery)

  const load = () => { invalidateCache('workflows'); refresh() }

  const filtered = useMemo(() => {
    if (!items) return null
    const n = q.trim().toLowerCase()
    return n ? items.filter((w) => `${w.name} ${w.description} ${(w.tags ?? []).join(' ')}`.toLowerCase().includes(n)) : items
  }, [items, q])
  const open = items?.find((w) => w.id === openId) ?? null

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<span data-type="title-l" className="text-on-surface">Workflows</span>}
          right={<HeaderActions><HeaderControl icon={Plus} label="New workflow" variant="primary" priority="primary" onClick={onCreate} /></HeaderActions>}
        />
      }
      controls={(items === undefined || items.length > 0)
        ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search workflows', label: 'Search workflows' }} />
        : undefined}
      panel={open && (
        <SidePanel key={open.id} fillHeight storeKey="workflow-panel-w" icon={(() => { const sm = scopeMeta(open.scope); return <WorkflowIcon size={18} style={{ color: sm.tone }} /> })()} title={open.name} onClose={() => setQuery({ open: null, edit: null })}>
          <WorkflowDetail workflow={open} editing={editing} onEditingChange={setEditing} allWorkflows={items ?? []} onSaved={() => load()} onDeleted={() => { setOpenId(""); load() }} />
        </SidePanel>
      )}
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {filtered === null ? <ListSkeleton rows={6} /> : filtered.length === 0 ? (
          <EmptyState icon={WorkflowIcon} title={q ? 'No matching workflows' : 'No workflows'} hint={q ? 'Try a different term.' : 'Workflows are reusable step-by-step SOPs. The agent auto-follows one when a turn matches its intent.'} action={!q ? { label: 'New workflow', onClick: onCreate, icon: Plus } : undefined} />
        ) : (
          <div className="flex flex-col gap-s">
            {filtered.map((w, i) => {
              const sm = scopeMeta(w.scope)
              // Right-click / long-press → scoped actions, via the shared ContextMenu
              // primitive. Both reuse the SAME setQuery the row's click / panel edit
              // toggle already drive (open the detail panel; open it straight into edit).
              const menuItems: ContextMenuItem[] = [
                { icon: <Eye size={15} />, label: 'Open', onSelect: () => setQuery({ open: w.id, edit: null }) },
                { icon: <Pencil size={15} />, label: 'Edit', onSelect: () => setQuery({ open: w.id, edit: '1' }) },
              ]
              return (
                <ContextMenu key={w.id} items={menuItems}>
                <ListRow index={i} accent={w.enabled === false ? undefined : sm.tone} onClick={() => setQuery({ open: w.id, edit: null })}>
                  <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${sm.tone} 16%, transparent)` }}><WorkflowIcon size={19} style={{ color: sm.tone }} /></span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-s">
                      <span className={`truncate text-[0.9375rem] ${w.enabled === false ? 'text-on-surface-var' : 'text-on-surface'}`} style={{ fontVariationSettings: '"wght" 500' }}>{w.name}</span>
                      {w.enabled === false && <span className="shrink-0 text-on-surface-low text-[0.7rem]">· disabled</span>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
                      <span style={{ color: sm.tone }}>{sm.label}</span>
                      <span className="inline-flex items-center gap-1"><ListOrdered size={11} /> {w.steps.length}</span>
                      {w.description && <span className="truncate">· {w.description}</span>}
                    </div>
                  </div>
                  {(w.tags?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-1">{w.tags!.slice(0, 2).map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.7rem]">{t}</span>)}</div>}
                </ListRow>
                </ContextMenu>
              )
            })}
          </div>
        )}
      </div>
    </WorkbenchLayout>
  )
}
