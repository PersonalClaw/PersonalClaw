import { useEffect, useMemo, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import {
  Clock, RotateCcw, Loader2, Trash2, FileSymlink, History, Tag, Download, ChevronUp, FileWarning,
  GitCompare, Lock,
} from 'lucide-react'
import { api, type Artifact, type ArtifactEvent } from '../../lib/api'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { isArtifactUpdateFor } from './artifactUpdateSignal'
import { notify } from '../../app/appSdk'
import { confirmDelete } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { QuietButton } from '../../ui/QuietButton'
import { downloadText, safeFilename } from '../../lib/download'
import { artifactKindMeta, relTime } from '../files/fileMeta'
import { ContentSurface } from '../../ui/content/ContentSurface'
import { resolveContentType } from '../../ui/content/contentTypes'
import { ArtifactCompare } from './ArtifactCompare'
import { ArtifactDeploy } from './ArtifactDeploy'
import type { CommentTarget } from '../../ui/content/commentTarget'
import { invalidateCache } from '../../lib/useCachedData'

interface ViewerProps {
  slug: string
  onChanged: () => void
  onDeleted: () => void
  onOpenSourceFile: (path: string) => void
  // Where selection-comments route. On the Files/Artifacts page the host passes a
  // newSessionTarget (a fresh chat session per comment); a host inside an active
  // chat would pass a sameSessionTarget. When omitted, the comment layer is off.
  commentTarget?: CommentTarget
  // Library detail polish (ARTIFACTS S2): open pinned to a historical version
  // (the ?v=N deep-link) and report version picks so the host can write the URL.
  initialVersion?: number
  onVersionChange?: (v: number | null) => void
  // Open with the details rail (versions + tags + timeline) expanded.
  defaultDetailsOpen?: boolean
}

export function ArtifactViewer({ slug, onChanged, onDeleted, onOpenSourceFile, commentTarget, initialVersion, onVersionChange, defaultDetailsOpen = false }: ViewerProps) {
  const [art, setArt] = useState<Artifact | null>(null)
  const [versions, setVersions] = useState<number[]>([])
  const [events, setEvents] = useState<ArtifactEvent[]>([])
  const [selVersion, setSelVersionRaw] = useState<number | null>(initialVersion ?? null)  // null = current
  const setSelVersion = (v: number | null) => { setSelVersionRaw(v); onVersionChange?.(v) }
  const [viewContent, setViewContent] = useState('')
  const [metaOpen, setMetaOpen] = useState(defaultDetailsOpen)  // sticky bottom metadata panel
  // Compare mode (S3 T3.3): replaces the body with a two-version diff. Reset on slug
  // change so navigating to another artifact never opens mid-comparison.
  const [comparing, setComparing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  // The artifact couldn't be loaded (e.g. deleted in another session / stale deep-link).
  // Without this, a 404 on the main fetch would reject the await and strand the viewer
  // on an infinite loading spinner.
  const [loadError, setLoadError] = useState('')
  // The version rail and the timeline are SEPARATE fetches from the artifact itself,
  // and both used to be `.catch(() => [])` — so an unreachable rail rendered as an
  // artifact with no history, and an unreachable timeline as one with no events. A
  // swallowed error that reads as an empty state is worse than either: it tells the
  // user a fact about their data that isn't true. Each side-fetch now keeps its own
  // failure so the panel can say which of the two happened.
  const [versionsError, setVersionsError] = useState('')
  const [eventsError, setEventsError] = useState('')

  // `quiet`: refresh in place without swapping the mounted body for the spinner.
  // A live refresh (the AE-10 socket trigger below) must not tear the render surface
  // down — that would flash empty, drop scroll position and discard an in-progress
  // draft, which is the opposite of "updates without a reload".
  const reload = async (opts?: { keepVersion?: boolean; quiet?: boolean }) => {
    if (!opts?.quiet) setLoading(true)
    setLoadError('')
    try {
      const [a, v, e] = await Promise.all([
        api.artifact(slug),
        api.artifactVersions(slug).then(
          (r) => { setVersionsError(''); return r.versions },
          (err) => { setVersionsError(String((err as Error)?.message || err)); return null },
        ),
        api.artifactEvents(slug).then(
          (r) => { setEventsError(''); return r.events },
          (err) => { setEventsError(String((err as Error)?.message || err)); return null },
        ),
      ])
      setArt(a); setVersions(v ?? []); setEvents(e ?? [])
      // keepVersion: the first load honors a ?v=N deep-link pin; every LATER
      // reload (save/snapshot/revert) returns to the current version as before.
      if (!opts?.keepVersion) setSelVersion(null)
      // Don't overwrite a pinned historical body with the current one — the pin
      // effect below refetches it when `art` changes, and writing current content
      // here would flash the wrong version in between.
      if (!(opts?.keepVersion && selVersion !== null)) setViewContent(a.content ?? '')
    } catch (err) {
      setLoadError(String((err as Error)?.message || err))
    } finally {
      if (!opts?.quiet) setLoading(false)
    }
  }
  useEffect(() => { setComparing(false); reload({ keepVersion: initialVersion != null }) }, [slug])  // eslint-disable-line react-hooks/exhaustive-deps

  // AE-10 — the live-refresh trigger behind the split-view iterate panel. The panel
  // is a `ChatEmbed` (a sandboxed iframe, a separate document with no bridge back
  // here), so a version the agent writes from inside it would otherwise sit
  // invisible behind a stale preview until the user reloaded the page. This filters
  // the `tool_call` frame the chat runner ALREADY broadcasts — see
  // `artifactUpdateSignal`; no WS event is added.
  //
  // `keepVersion` so a live write never yanks a pinned `?v=N` snapshot out from
  // under whoever is reading it: the rail gains the new version, the pinned body
  // stays put. `onChanged` keeps the library grid's card in step.
  useChatSocket((m: WsMessage) => {
    if (!isArtifactUpdateFor(m, slug)) return
    reload({ keepVersion: true, quiet: true }).then(() => onChanged()).catch(() => {})
  })

  // Pull-on-view (WF2AUT-6 / R10): opening an artifact is the render that drives any `view` trigger
  // bound to it. The surface id is `artifact.<slug>` — stable per artifact, and what an author binds
  // a `view` trigger to. Fire-and-forget: a background refresh must never block the open or surface
  // an error toast, and within its TTL the backend just serves cache and costs nothing.
  useEffect(() => { api.viewRender(`artifact.${slug}`).catch(() => {}) }, [slug])

  // Load a historical version's immutable content when one is picked.
  useEffect(() => {
    if (selVersion === null) { setViewContent(art?.content ?? ''); return }
    let alive = true
    api.artifactVersion(slug, selVersion).then((a) => { if (alive) setViewContent(a.content ?? '') }).catch(() => {})
    return () => { alive = false }
  }, [selVersion, slug, art])

  const km = useMemo(() => art ? artifactKindMeta(art.kind) : null, [art])
  const isCurrent = selVersion === null
  // A frozen record (SM-9: today, a shared chat transcript). The server refuses every
  // content mutation on it, so the UI must stop OFFERING one — an editor whose save
  // always 400s is worse than no editor. Folded into the SAME condition that already
  // governs editing a historical version, so there is one "can this be edited?" answer
  // rather than two that can drift apart.
  const frozen = !!art?.readonly
  const editable = isCurrent && !frozen
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
    try {
      await api.deleteArtifact(slug)
      // 🔴 `onDeleted()` refreshes THIS surface. The chat composer's attach picker reads the same
      // collection under its own cache key, so a deleted artifact stayed on offer there — and
      // attaching it fails. Prefix mode busts every key in the `artifacts:` namespace at once,
      // which is why the picker's key was moved into it.
      invalidateCache('artifacts:', true)
      onDeleted()
    }
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
          <p className="text-[0.8125rem]">Couldn't open this artifact.</p>
          <p className="text-[0.75rem] text-on-surface-low/80">It may have been deleted. {loadError}</p>
          <Button variant="ghost-accent" size="xs" onClick={() => reload()} className="mt-1"><RotateCcw size={13} /> Try again</Button>
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
          <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{art.name}</span>
          <span className="truncate text-on-surface-low text-[0.75rem] font-mono">{art.slug} · {km.label}</span>
          {art.live_dirty && <span className="shrink-0 rounded px-1.5 py-0.5 text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-warning) 18%, transparent)', color: 'var(--color-warning)' }}>source changed</span>}
          <div className="ml-auto flex items-center gap-1">
            {art.source_path && (
              <QuietButton onClick={() => onOpenSourceFile(art.source_path)} title={`Open source file: ${art.source_path}`}>
                <FileSymlink size={13} /> Source file
              </QuietButton>
            )}
            <QuietButton onClick={download} title="Download this artifact">
              <Download size={13} /> Download
            </QuietButton>
            <button onClick={del} type="button" aria-label="Delete artifact" title="Delete artifact" className="inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem] hover:bg-surface-high" style={{ color: 'var(--color-error)' }}><Trash2 size={13} /></button>
          </div>
        </div>

        {/* Deploy / Open / Tear down (PEP-8) — only for the kinds that can be served as a
            page, and only on the CURRENT version: a deploy serves the live body, so
            offering it while a historical snapshot is on screen would publish something
            other than what the user is looking at. */}
        {isCurrent && <ArtifactDeploy slug={slug} kind={art.kind} />}

        {/* Why this artifact can't be edited. Without it a read-only artifact just looks
            like one whose editor is broken — the same reason the historical-version bar
            below says which version you're on. Also names the redaction, because a
            transcript that reads as verbatim but isn't would mislead whoever it's shown to. */}
        {frozen && isCurrent && (
          <div className="flex items-center gap-2 border-b border-outline/40 px-m py-1.5 text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
            <Lock size={12} className="text-primary" />
            <span className="text-on-surface-low">Read-only record — a shared chat transcript with credentials redacted. It can be downloaded or deleted, never edited.</span>
          </div>
        )}

        {!isCurrent && (
          <div className="flex items-center gap-2 border-b border-outline/40 px-m py-1.5 text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-warning) 10%, transparent)' }}>
            <Clock size={12} style={{ color: 'var(--color-warning)' }} />
            <span className="text-on-surface-low">Viewing historical v{selVersion} (read-only)</span>
            {/* No revert on a frozen artifact: the server refuses it, so offering the
                button would only produce an error the user can do nothing about. */}
            {!frozen && (
              <button onClick={revert} disabled={busy} type="button" className="ml-auto inline-flex items-center gap-1 rounded-md px-2 h-6 text-[0.75rem]" style={{ color: 'var(--color-warning)', border: '1px solid color-mix(in srgb, var(--color-warning) 35%, transparent)' }}>
                <RotateCcw size={11} /> Revert to v{selVersion}
              </button>
            )}
          </div>
        )}

        {/* body — the ONE registry-driven render/edit surface (was the kind
            if/else + EDITABLE_KINDS/IFRAME_KINDS Sets + inline Monaco + manual
            CommentLayer). The artifact keeps only its chrome above/below.
            Compare REPLACES the body rather than sitting beside it: the question
            "what changed between these two?" wants the whole width, and stacking a
            diff under a live preview leaves neither readable. */}
        <div className="min-h-0 flex-1">
          {comparing
            ? <ArtifactCompare art={art} versions={versions} />
            : ctype && (
            <ContentSurface
              key={`${art.slug}:${selVersion ?? 'cur'}`}
              type={ctype}
              content={viewContent}
              title={art.name}
              docId={art.slug}
              path={art.source_path || undefined}
              readOnly={!editable}
              onSave={editable ? onSave : undefined}
              // Renderer-driven iteration (AS-3): an EDITMODE tweak saves through the
              // SAME snapshot path the Snapshot action uses, so the new version and
              // its restore are inherited machinery rather than a second write path.
              // A historical/frozen version keeps annotate (a correction is a request,
              // not a mutation) but offers no persist.
              iterate={{ slug: art.slug, persistVersion: editable ? snapshot : undefined }}
              commentTarget={commentTarget}
              actions={editable ? [{ icon: History, label: 'Snapshot', title: 'Save as a new version snapshot', primary: true, run: snapshot }] : undefined}
            />
          )}
        </div>
      </div>

      {/* metadata — a collapsed, expandable sticky bottom panel (was a right
          rail): versions + tags + timeline. Header always visible; body toggles. */}
      <div className="shrink-0 border-t border-outline/40 bg-surface-container/40">
        <button type="button" onClick={() => setMetaOpen((v) => !v)} aria-expanded={metaOpen}
          className="flex w-full items-center gap-2 px-m py-2 text-on-surface-low hover:text-on-surface transition-colors">
          <ChevronUp size={14} className={`transition-transform ${metaOpen ? '' : 'rotate-180'}`} />
          <span className="text-[0.75rem] uppercase tracking-wide">Details</span>
          <span className="text-on-surface-low text-[0.75rem]">· v{art.version}{art.tags.length ? ` · ${art.tags.length} tag${art.tags.length === 1 ? '' : 's'}` : ''} · {events.length} event{events.length === 1 ? '' : 's'}</span>
        </button>
        {metaOpen && (
          <div className="grid max-h-[40vh] grid-cols-1 gap-l overflow-y-auto px-m pb-m sm:grid-cols-3">
            <div>
              <Label icon={History}>Versions</Label>
              {/* An unreachable rail is NOT a rail with nothing in it. Offering the
                  picker here would show a lone "Current" and read as a healthy
                  single-version artifact, hiding the fetch that failed — so the
                  failure gets its own state, named and retryable. */}
              {versionsError ? (
                <div className="mt-1.5 flex flex-col items-start gap-1">
                  <span className="flex items-center gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-error)' }}>
                    <FileWarning size={12} /> Couldn't load version history.
                  </span>
                  <span className="text-on-surface-low text-[0.75rem]">{versionsError}</span>
                  <QuietButton onClick={() => reload({ keepVersion: true })} title="Retry loading the version history">
                    <RotateCcw size={13} /> Try again
                  </QuietButton>
                </div>
              ) : versions.length === 0 ? (
                <span className="mt-1.5 block text-on-surface-low text-[0.75rem]">No version history.</span>
              ) : (
              <select value={selVersion ?? 'current'} onChange={(e) => setSelVersion(e.target.value === 'current' ? null : Number(e.target.value))}
                aria-label="Version"
                className="mt-1.5 h-8 w-full rounded-md bg-surface-high px-2 text-[0.8125rem] text-on-surface outline-none">
                <option value="current">Current · v{art.version}</option>
                {versions.slice().reverse().filter((v) => v !== art.version).map((v) => <option key={v} value={v}>v{v}</option>)}
              </select>
              )}
              {/* Compare is offered only once there are two versions to compare —
                  a disabled control on a one-version artifact would just raise the
                  question of why it's disabled. */}
              {versions.length > 1 && (
                <QuietButton onClick={() => setComparing((v) => !v)} ariaExpanded={comparing}
                  title={comparing ? 'Close the version comparison' : 'Compare two versions of this artifact'}
                  className="mt-1.5">
                  <GitCompare size={13} /> {comparing ? 'Close compare' : 'Compare versions'}
                </QuietButton>
              )}
            </div>

            <div>
              <Label icon={Tag}>Tags</Label>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {art.tags.length ? art.tags.map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.75rem]">{t}</span>)
                  : <span className="text-on-surface-low text-[0.75rem]">None</span>}
              </div>
            </div>

            <div>
              <Label icon={Clock}>Timeline</Label>
              <div className="mt-2 flex flex-col gap-2.5">
                {/* Same distinction as the rail: "we couldn't read the timeline" is a
                    different fact from "nothing has happened to this artifact". */}
                {eventsError && (
                  <span className="flex items-center gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-error)' }}>
                    <FileWarning size={12} /> Couldn't load the timeline.
                  </span>
                )}
                {!eventsError && events.length === 0 && <span className="text-on-surface-low text-[0.75rem]">No events.</span>}
                {events.slice().reverse().map((e, i) => (
                  <div key={i} className="flex items-start gap-2 text-[0.75rem]">
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
  return <div className="flex items-center gap-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide"><Icon size={11} /> {children}</div>
}

function eventTone(type: string): string {
  if (type === 'created') return 'var(--color-success)'
  if (type === 'reverted') return 'var(--color-warning)'
  if (type === 'referenced') return 'var(--color-on-surface-low)'
  return 'var(--color-primary)'
}
