import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import type { Artifact } from '../../../lib/api'
import { ARTIFACT_KINDS, artifactKindMeta, relTime } from '../fileMeta'

/** The artifacts rail — filterable list standing in for the file tree when the
 *  scope switch is on "Artifacts". */
export function ArtifactList({ artifacts, activeSlug, onSelect }: {
  artifacts: Artifact[]
  activeSlug: string | null
  onSelect: (a: Artifact) => void
}) {
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<string>('')

  const kindsPresent = useMemo(() => {
    const s = new Set(artifacts.map((a) => a.kind))
    return ARTIFACT_KINDS.filter((k) => s.has(k.key))
  }, [artifacts])

  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase()
    return artifacts.filter((a) =>
      (!kind || a.kind === kind) &&
      (!n || `${a.name} ${a.slug} ${a.description} ${a.tags.join(' ')}`.toLowerCase().includes(n)))
  }, [artifacts, q, kind])

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-outline/40 p-m">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter artifacts…" aria-label="Filter artifacts"
            className="h-8 w-full rounded-md bg-surface-high pl-8 pr-3 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </div>
        {kindsPresent.length > 1 && (
          <div className="mt-2 flex flex-wrap gap-1">
            <FilterChip label="All" on={kind === ''} onClick={() => setKind('')} />
            {kindsPresent.map((k) => <FilterChip key={k.key} label={k.label} tone={k.tone} on={kind === k.key} onClick={() => setKind(k.key)} />)}
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {filtered.length === 0 && <div className="px-m py-s text-on-surface-low text-[0.8125rem]">No artifacts.</div>}
        {filtered.map((a) => {
          const km = artifactKindMeta(a.kind)
          const Icon = km.icon
          const active = a.slug === activeSlug
          return (
            <button key={a.slug} onClick={() => onSelect(a)} type="button"
              className="flex w-full items-start gap-2 rounded-md px-m py-2 text-left transition-colors hover:bg-surface-high"
              style={{ background: active ? 'color-mix(in srgb, var(--color-primary) 14%, transparent)' : undefined }}>
              <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-md" style={{ background: `color-mix(in srgb, ${km.tone} 16%, transparent)` }}>
                <Icon size={15} style={{ color: km.tone }} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }}>{a.name}</span>
                  {a.live_dirty && <span className="size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-warning)' }} title="Source file changed since last snapshot" />}
                </div>
                <div className="mt-0.5 flex items-center gap-x-2 text-on-surface-low text-[0.7rem]">
                  <span style={{ color: km.tone }}>{km.label}</span>
                  <span>v{a.version}</span>
                  {a.source_path && <span className="truncate" title={a.source_path}>· file-backed</span>}
                  <span className="ml-auto shrink-0">{relTime(a.updated_at || a.created_at)}</span>
                </div>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function FilterChip({ label, tone, on, onClick }: { label: string; tone?: string; on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} type="button"
      className="rounded-pill px-2.5 h-6 text-[0.7rem] transition-colors"
      style={on
        ? { background: tone ? `color-mix(in srgb, ${tone} 22%, transparent)` : 'var(--color-surface-highest)', color: tone ?? 'var(--color-on-surface)' }
        : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
      {label}
    </button>
  )
}
