import { useEffect, useMemo, useState } from 'react'
import {
  Clock, RotateCcw, Loader2, Trash2, FileSymlink, History, Tag, Download, ChevronUp, FileWarning,
} from 'lucide-react'
import { api, type Artifact, type ArtifactEvent } from '../../../lib/api'
import { notify } from '../../../app/appSdk'
import { confirmDelete } from '../../../ui/dialog'
import { downloadText, safeFilename } from '../../../lib/download'
import { artifactKindMeta, relTime } from '../fileMeta'
import { ContentSurface } from '../../../ui/content/ContentSurface'
import { resolveContentType } from '../../../ui/content/contentTypes'
import type { CommentTarget } from '../../../ui/content/commentTarget'

interface ViewerProps {
  slug: string
  onChanged: () => void
  onDeleted: () => void
  onOpenSourceFile: (path: string) => void
  // Where selection-comments route. On the Files/Artifacts page the host passes a
  // newSessionTarget (a fresh chat session per comment); a host inside an active
  // chat would pass a sameSessionTarget. When omitted, the comment layer is off.
  commentTarget?: CommentTarget
}

export function ArtifactViewer({ slug, onChanged, onDeleted, onOpenSourceFile, commentTarget }: ViewerProps) {
  const [art, setArt] = useState<Artifact | null>(null)
  const [versions, setVersions] = useState<number[]>([])
  const [events, setEvents] = useState<ArtifactEvent[]>([])
  const [selVersion, setSelVersion] = useState<number | null>(null)  // null = current
  const [viewContent, setViewContent] = useState('')
  const [metaOpen, setMetaOpen] = useState(false)  // sticky bottom metadata panel
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  // The artifact couldn't be loaded (e.g. deleted in another session / stale deep-link).
  // Without this, a 404 on the main fetch would reject the await and strand the viewer
  // on an infinite loading spinner.
  const [loadError, setLoadError] = useState('')

  const reload = async () => {
    setLoading(true); setLoadError('')
    try {
      const [a, v, e] = await Promise.all([
        api.artifact(slug),
        api.artifactVersions(slug).catch(() => ({ slug, versions: [] })),
        api.artifactEvents(slug).catch(() => ({ slug, events: [] })),
      ])
      setArt(a); setVersions(v.versions); setEvents(e.events)
      setSelVersion(null); setViewContent(a.content ?? '')
    } catch (err) {
      setLoadError(String((err as Error)?.message || err))
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { reload() }, [slug])

  // Load a historical version's immutable content when one is picked.
  useEffect(() => {
    if (selVersion === null) { setViewContent(art?.content ?? ''); return }
    let alive = true
    api.artifactVersion(slug, selVersion).then((a) => { if (alive) setViewContent(a.content ?? '') }).catch(() => {})
    return () => { alive = false }
  }, [selVersion, slug, art])

  const km = useMemo(() => art ? artifactKindMeta(art.kind) : null, [art])
  const isCurrent = selVersion === null
  // The registry resolves how this artifact renders/edits/sanitizes — one source
  // of truth (was the ArtifactBody if/else + EDITABLE_KINDS/IFRAME_KINDS Sets).
  const ctype = useMemo(() => art ? resolveContentType({ kind: art.kind }) : null, [art])

  // ContentSurface owns the draft + edit toggle. A plain Save records an 'edited'
  // event; the separate "Snapshot" action (below, passed as a ContentAction) cuts
  // a new immutable version.
  const onSave = async (draft: string) => {
    if (!art) return
    // Re-throw after notifying: ContentSurface's save keeps the draft dirty on a throw,
    // so the user doesn't lose their edit + sees why it failed (was silently swallowed).
    try {
      await api.updateArtifact(slug, { content: draft, snapshot: false, event_type: 'edited' })
      await reload(); onChanged()
    } catch (e) { notify(`Could not save artifact: ${(e as Error).message}`, 'error'); throw e }
  }
  // "Snapshot" — persist the draft AND cut a new immutable version (event 'iterated').
  const snapshot = async (draft: string) => {
    if (!art) return
    try {
      await api.updateArtifact(slug, { content: draft, snapshot: true, event_type: 'iterated' })
      await reload(); onChanged()
    } catch (e) { notify(`Could not snapshot artifact: ${(e as Error).message}`, 'error'); throw e }
  }
  const revert = async () => {
    if (!art || selVersion === null) return
    setBusy(true)
    try {
      // Revert is server-side: it restores version N's body (text or binary) as a
      // new current version. We send NO content — for a binary artifact the client
      // only holds a raw-URL ref, not the bytes, so the server must source them.
      await api.updateArtifact(slug, { event_type: 'reverted', from_version: selVersion })
      await reload(); onChanged()
    } catch (e) { notify(`Could not revert: ${(e as Error).message}`, 'error') }
    finally { setBusy(false) }
  }
  const del = async () => {
    if (!art) return
    // Binary kinds (image): the bytes are only here, and any chat message that
    // embedded this image references it by URL — deleting breaks those messages
    // (they degrade to a "no longer available" placeholder). Warn specifically so
    // it isn't a surprise, since (unlike a file-backed artifact) there's no source
    // file to fall back on. Suggest Download first.
    const body = ctype?.binary
      ? 'The image bytes live only here. Any chat message that shows this image will display a "no longer available" placeholder after deletion. Download it first if you want to keep a copy. This cannot be undone.'
      : 'The underlying source file/widget is not touched — only the saved artifact and its version history are removed.'
    if (!(await confirmDelete('artifact', art.name, { body }))) return
    try { await api.deleteArtifact(slug); onDeleted() }
    catch (e) { notify(`Could not delete artifact: ${(e as Error).message}`, 'error') }
  }
  // Download the currently-shown content (current or a historical version) with
  // an extension that matches the artifact kind.
  const ext = ({ markdown: 'md', html: 'html', react: 'jsx', svg: 'svg', json: 'json', text: 'txt', widget: 'html', document: 'html', infographic: 'txt' } as Record<string, string>)
  const download = () => {
    if (!art) return
    const suffix = selVersion === null ? '' : `-v${selVersion}`
    // Binary kinds (image): the body is bytes on the server, and viewContent is
    // only a raw-URL ref — so pull the real bytes from /raw (pinned to the shown
    // version) rather than saving the ref string as text. The endpoint sets the
    // Content-Type; the browser keeps the right extension.
    if (ctype?.binary) {
      const q = selVersion === null ? '' : `?version=${selVersion}`
      const a = document.createElement('a')
      a.href = `/api/artifacts/${encodeURIComponent(slug)}/raw${q}`
      a.download = `${safeFilename(art.name, art.slug)}${suffix}`
      document.body.appendChild(a); a.click(); a.remove()
      return
    }
    downloadText(`${safeFilename(art.name, art.slug)}${suffix}.${ext[art.kind] || 'txt'}`, viewContent)
  }

  // Load failed (deleted in another session / stale deep-link) — a clean placeholder
  // instead of an endless spinner, with a retry for transient errors.
  if (loadError && !loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.875rem]">Couldn't open this artifact.</p>
          <p className="text-[0.75rem] text-on-surface-low/80">It may have been deleted. {loadError}</p>
          <button type="button" onClick={() => reload()}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[0.75rem] text-primary hover:bg-surface-high">
            <RotateCcw size={13} /> Try again
          </button>
        </div>
      </div>
    )
  }
  if (loading || !art || !km) return <div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
  const Icon = km.icon

  return (
    <div className="flex h-full flex-col">
      {/* main — min-h-0 so it stays within the flex track and the content area's
          overflow-auto actually scrolls (a flex child defaults to min-height:auto,
          which would let it grow to the full content height instead). */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-s border-b border-outline/40 px-m py-2">
          <Icon size={15} style={{ color: km.tone }} className="shrink-0" />
          <span className="truncate text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>{art.name}</span>
          <span className="truncate text-on-surface-low text-[0.7rem] font-mono">{art.slug} · {km.label}</span>
          {art.live_dirty && <span className="shrink-0 rounded px-1.5 py-0.5 text-[0.65rem]" style={{ background: 'color-mix(in srgb, var(--color-warning) 18%, transparent)', color: 'var(--color-warning)' }}>source changed</span>}
          <div className="ml-auto flex items-center gap-1">
            {art.source_path && (
              <button onClick={() => onOpenSourceFile(art.source_path)} type="button"
                className="inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface" title={`Open source file: ${art.source_path}`}>
                <FileSymlink size={13} /> Source file
              </button>
            )}
            <button onClick={download} type="button" title="Download this artifact"
              className="inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface">
              <Download size={13} /> Download
            </button>
            <button onClick={del} type="button" aria-label="Delete artifact" title="Delete artifact" className="inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem] hover:bg-surface-high" style={{ color: 'var(--color-error)' }}><Trash2 size={13} /></button>
          </div>
        </div>

        {!isCurrent && (
          <div className="flex items-center gap-2 border-b border-outline/40 px-m py-1.5 text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-warning) 10%, transparent)' }}>
            <Clock size={12} style={{ color: 'var(--color-warning)' }} />
            <span className="text-on-surface-low">Viewing historical v{selVersion} (read-only)</span>
            <button onClick={revert} disabled={busy} type="button" className="ml-auto inline-flex items-center gap-1 rounded-md px-2 h-6 text-[0.7rem]" style={{ color: 'var(--color-warning)', border: '1px solid color-mix(in srgb, var(--color-warning) 35%, transparent)' }}>
              <RotateCcw size={11} /> Revert to v{selVersion}
            </button>
          </div>
        )}

        {/* body — the ONE registry-driven render/edit surface (was the kind
            if/else + EDITABLE_KINDS/IFRAME_KINDS Sets + inline Monaco + manual
            CommentLayer). The artifact keeps only its chrome above/below. */}
        <div className="min-h-0 flex-1">
          {ctype && (
            <ContentSurface
              key={`${art.slug}:${selVersion ?? 'cur'}`}
              type={ctype}
              content={viewContent}
              title={art.name}
              docId={art.slug}
              path={art.source_path || undefined}
              readOnly={!isCurrent}
              onSave={isCurrent ? onSave : undefined}
              commentTarget={commentTarget}
              actions={isCurrent ? [{ icon: History, label: 'Snapshot', title: 'Save as a new version snapshot', primary: true, run: snapshot }] : undefined}
            />
          )}
        </div>
      </div>

      {/* metadata — a collapsed, expandable sticky bottom panel (was a right
          rail): versions + tags + timeline. Header always visible; body toggles. */}
      <div className="shrink-0 border-t border-outline/40 bg-surface-container/40">
        <button type="button" onClick={() => setMetaOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-m py-2 text-on-surface-low hover:text-on-surface transition-colors">
          <ChevronUp size={14} className={`transition-transform ${metaOpen ? '' : 'rotate-180'}`} />
          <span className="text-[0.7rem] uppercase tracking-wide">Details</span>
          <span className="text-on-surface-low text-[0.7rem]">· v{art.version}{art.tags.length ? ` · ${art.tags.length} tag${art.tags.length === 1 ? '' : 's'}` : ''} · {events.length} event{events.length === 1 ? '' : 's'}</span>
        </button>
        {metaOpen && (
          <div className="grid max-h-[40vh] grid-cols-1 gap-l overflow-y-auto px-m pb-m sm:grid-cols-3">
            <div>
              <Label icon={History}>Versions</Label>
              <select value={selVersion ?? 'current'} onChange={(e) => setSelVersion(e.target.value === 'current' ? null : Number(e.target.value))}
                className="mt-1.5 h-8 w-full rounded-md bg-surface-high px-2 text-[0.8125rem] text-on-surface outline-none [color-scheme:dark]">
                <option value="current">Current · v{art.version}</option>
                {versions.slice().reverse().filter((v) => v !== art.version).map((v) => <option key={v} value={v}>v{v}</option>)}
              </select>
            </div>

            <div>
              <Label icon={Tag}>Tags</Label>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {art.tags.length ? art.tags.map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.7rem]">{t}</span>)
                  : <span className="text-on-surface-low text-[0.75rem]">None</span>}
              </div>
            </div>

            <div>
              <Label icon={Clock}>Timeline</Label>
              <div className="mt-2 flex flex-col gap-2.5">
                {events.length === 0 && <span className="text-on-surface-low text-[0.75rem]">No events.</span>}
                {events.slice().reverse().map((e, i) => (
                  <div key={i} className="flex items-start gap-2 text-[0.7rem]">
                    <span className="mt-1 size-1.5 shrink-0 rounded-full" style={{ background: eventTone(e.type) }} />
                    <div className="min-w-0">
                      <div className="text-on-surface">
                        {e.type}{e.type === 'reverted' && e.from_version ? ` v${e.from_version}→v${e.version}` : e.version ? ` (v${e.version})` : ''}
                      </div>
                      <div className="text-on-surface-low">{e.by || 'system'} · {relTime(e.ts)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Label({ icon: Icon, children }: { icon: typeof Clock; children: React.ReactNode }) {
  return <div className="flex items-center gap-1.5 text-on-surface-low text-[0.7rem] uppercase tracking-wide"><Icon size={11} /> {children}</div>
}

function eventTone(type: string): string {
  if (type === 'created') return 'var(--color-success)'
  if (type === 'reverted') return 'var(--color-warning)'
  if (type === 'referenced') return 'var(--color-on-surface-low)'
  return 'var(--color-primary)'
}
