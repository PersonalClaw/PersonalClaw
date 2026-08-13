import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { api, type Artifact } from '../../lib/api'
import { artifactKindMeta, relTime } from '../files/fileMeta'
import { buildSrcdoc, buildReactSrcdoc, readThemeVars } from '../../ui/widget/widgetSrcdoc'
import { resolveContentType, isSandboxed } from '../../ui/content/contentTypes'
import { TileButton } from '../../ui/TileButton'
import { useMode } from '../../app/theme'

/** Live-preview card for the artifacts library grid (ARTIFACTS S2).
 *
 *  Soul guardrail: previews are sandboxed EXACTLY like chat widgets — the same
 *  `sandbox="allow-scripts"` srcdoc contract (`widgetSrcdoc`), theme tokens
 *  injected, scaled down (not re-implemented). Three cost controls keep a big
 *  library smooth:
 *    • LAZY — the preview mounts only once the card is near the viewport
 *      (IntersectionObserver); off-screen cards hold a flat placeholder.
 *    • LRU CAP — at most `IFRAME_CAP` live iframes at once, coordinated through a
 *      module-level registry; older previews demote back to placeholders.
 *    • INERT — the preview is pointer-events-none (the card is one click target),
 *      so a script-bearing widget can't capture grid scroll/clicks.
 *  Text kinds render a cheap excerpt (no iframe at all); images use the raw URL. */

// At most this many LIVE preview iframes across the grid (LRU-evicted).
export const IFRAME_CAP = 12

// ── Module-level LRU of live iframe cards ───────────────────────────────────
// Each visible iframe card registers itself; when the pool exceeds the cap, the
// least-recently-activated card is told to demote to a placeholder.
const _live = new Map<number, () => void>() // id → demote()
let _nextId = 1

function acquireLiveSlot(demote: () => void): number {
  const id = _nextId++
  _live.set(id, demote)
  if (_live.size > IFRAME_CAP) {
    const oldest = _live.keys().next().value
    if (oldest !== undefined) {
      const fn = _live.get(oldest)
      _live.delete(oldest)
      fn?.()
    }
  }
  return id
}

function releaseLiveSlot(id: number) {
  _live.delete(id)
}

/** The scale factor for the mini preview: render the srcdoc at ~2.5x the card size
 *  and scale down, so widget layouts built for full width don't collapse. */
const PREVIEW_SCALE = 0.4

/** Whether this artifact's mini preview renders as a live sandboxed iframe.
 *  Derived from the content-type REGISTRY (no hand-rolled kind Sets): every
 *  registry-sandboxed type (widget/html/react/infographic) previews live, plus
 *  document + svg — which the full-page registry renders in-DOM sanitized (so the
 *  comment layer attaches), but the MINI card sandboxes through the same themed
 *  srcdoc instead: at card scale there is no comment layer, and one isolation
 *  path is cheaper + safer than a scaled in-DOM sanitizer render. */
function previewsAsIframe(ctype: ReturnType<typeof resolveContentType>): boolean {
  return isSandboxed(ctype) || ctype.id === 'document' || ctype.id === 'svg'
}

function ExcerptPreview({ content }: { content: string }) {
  return (
    <pre className="h-full w-full overflow-hidden whitespace-pre-wrap break-words px-3 py-2 font-mono text-[0.625rem] leading-relaxed text-on-surface-low">
      {content.slice(0, 600)}
    </pre>
  )
}

function Placeholder({ tone, label }: { tone: string; label: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <span className="rounded-pill px-2.5 py-1 text-[0.6875rem]" style={{ background: `color-mix(in srgb, ${tone} 14%, transparent)`, color: tone }}>{label}</span>
    </div>
  )
}

/** The live iframe preview — srcdoc per kind, sandboxed + inert + scaled. */
function LivePreview({ art, content, mode }: { art: Artifact; content: string; mode: 'dark' | 'light' }) {
  const srcdoc = useMemo(() => {
    const themeVars = readThemeVars()
    if (art.kind === 'react') return buildReactSrcdoc({ jsx: content, themeVars, mode })
    // widget/html/infographic/document/svg all render through the standard themed
    // srcdoc (document/svg get readable theme colors; scripts stay sandboxed).
    const html = art.kind === 'svg' ? `<div style="display:grid;place-items:center;height:100vh">${content}</div>` : content
    return buildSrcdoc({ html, themeVars, mode })
  }, [art.kind, content, mode])
  return (
    <div className="pointer-events-none h-full w-full overflow-hidden" aria-hidden>
      <iframe
        srcDoc={srcdoc}
        sandbox="allow-scripts"
        tabIndex={-1}
        title={`Preview of ${art.name}`}
        className="origin-top-left border-none bg-surface"
        style={{ width: `${100 / PREVIEW_SCALE}%`, height: `${100 / PREVIEW_SCALE}%`, transform: `scale(${PREVIEW_SCALE})` }}
      />
    </div>
  )
}

export const ArtifactCard = memo(function ArtifactCard({ art, onOpen }: {
  art: Artifact
  onOpen: (a: Artifact) => void
}) {
  const { mode } = useMode()
  const km = artifactKindMeta(art.kind)
  const rootRef = useRef<HTMLDivElement>(null)
  // near: the card is close to the viewport (IntersectionObserver) → fetch + mount.
  const [near, setNear] = useState(false)
  // live: this card currently holds one of the capped iframe slots.
  const [live, setLive] = useState(false)
  const [content, setContent] = useState<string | null>(null)
  // The lazy detail fetch also carries live_dirty (computed by get(), not list) —
  // the drift badge on file-backed cards rides the same request as the preview.
  const [dirty, setDirty] = useState(false)
  const slotRef = useRef<number | null>(null)

  const ctype = useMemo(() => resolveContentType({ kind: art.kind }), [art.kind])
  const isIframeKind = previewsAsIframe(ctype)
  const isImage = !!ctype.binary // image et al — content is a raw-URL ref, not text
  const isExcerpt = !isIframeKind && !isImage // markdown/text/json → excerpt

  // Observe viewport proximity (one observer per card is fine at grid scale; the
  // 200-artifact perf proof measures this).
  useEffect(() => {
    const el = rootRef.current
    if (!el || typeof IntersectionObserver === 'undefined') { setNear(true); return }
    const obs = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { setNear(true); obs.disconnect() }
    }, { rootMargin: '200px' })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // Fetch content once near (list payloads are content-free by design — the card
  // fetches its own body lazily; images skip this and use the raw URL directly).
  useEffect(() => {
    if (!near || isImage || content !== null) return
    let alive = true
    api.artifact(art.slug)
      .then((a) => { if (alive) { setContent(a.content ?? ''); setDirty(!!a.live_dirty) } })
      .catch(() => { if (alive) setContent('') })
    return () => { alive = false }
  }, [near, isImage, content, art.slug])

  // Acquire an LRU slot for iframe kinds once content is in; demote when evicted.
  useEffect(() => {
    if (!near || !isIframeKind || content === null) return
    setLive(true)
    const id = acquireLiveSlot(() => setLive(false))
    slotRef.current = id
    return () => { releaseLiveSlot(id); slotRef.current = null }
  }, [near, isIframeKind, content])

  // Theme switch: srcdoc rebuilds via `mode` in LivePreview's memo — nothing to do here.

  const preview = (() => {
    if (!near) return <Placeholder tone={km.tone} label={km.label} />
    if (isImage) {
      return <img src={`/api/artifacts/${encodeURIComponent(art.slug)}/raw`} alt={art.name} loading="lazy" className="h-full w-full object-cover" />
    }
    if (content === null) return <Placeholder tone={km.tone} label={km.label} />
    if (isExcerpt) return <ExcerptPreview content={content} />
    if (live) return <LivePreview art={art} content={content} mode={mode} />
    return <Placeholder tone={km.tone} label={km.label} />
  })()

  const Icon = km.icon
  return (
    <div ref={rootRef}>
    <TileButton onClick={() => onOpen(art)} title={art.name} ariaLabel={art.name}
      className="h-full w-full">
      <div className="h-36 w-full shrink-0 overflow-hidden border-b border-outline-variant/30 bg-surface">
        {preview}
      </div>
      <div className="flex min-w-0 flex-col gap-0.5 px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Icon size={13} style={{ color: km.tone }} className="shrink-0" />
          <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{art.name}</span>
        </div>
        <div className="flex items-center gap-1.5 text-on-surface-low text-[0.6875rem]">
          <span>{km.label}</span>
          <span>· v{art.version}</span>
          {art.collection && <span className="truncate rounded-pill bg-surface-high px-1.5">{art.collection}</span>}
          {dirty && <span className="shrink-0 rounded-pill px-1.5" title="The source file changed since the last snapshot" style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}>source changed</span>}
          <span className="ml-auto shrink-0">{relTime(art.updated_at || art.created_at)}</span>
        </div>
      </div>
    </TileButton>
    </div>
  )
})
