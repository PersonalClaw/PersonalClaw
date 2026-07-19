import { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Minimize2, ExternalLink, Download, Bookmark } from 'lucide-react'
import { useMode } from '../../app/theme'
import { api } from '../../lib/api'
import { buildSrcdoc, readThemeVars } from './widgetSrcdoc'
import { effectiveWidgetSlug } from './widgetSlug'
import { BlueprintSkeleton } from './BlueprintSkeleton'
import { spring } from '../../design/motion'

const MIN_HEIGHT = 80
// The iframe body's own padding (16px each side) — added to the reported
// natural content width to get the iframe width that fits it exactly.
const BODY_PAD = 32
// Floating only makes sense when the text column keeps a readable measure.
const MIN_TEXT_COL = 300

// Height/width caches are theme-independent (theme vars are colors, not sizes).
const heightCache = new Map<string, number>()
const widthCache = new Map<string, number>()
function contentHash(html: string): string {
  let h = 0
  for (let i = 0; i < html.length; i++) h = ((h << 5) - h + html.charCodeAt(i)) | 0
  return String(h)
}

interface Props {
  html: string
  title?: string
  /** explicit `<widget slug="...">`, if supplied. */
  slug?: string
  /** message ts + widget ordinal → stable derived slug when none is explicit. */
  messageTs?: string
  widgetIndex?: number
  /** still streaming — render the partial HTML, defer the host script. */
  streaming?: boolean
}

/** Dynamic layout for the inline widget: float when the visual is narrow enough
 *  that the text column beside it stays readable; otherwise block (full-width). */
function computeWidgetLayout(naturalW: number | null, hostW: number | null): React.CSSProperties {
  if (!naturalW || !hostW || hostW < 500) return { width: '100%' }
  // Widget fills ≥ 90% of the column → full-width block (no float, no gap).
  if (naturalW >= hostW * 0.9) return { width: '100%' }
  // Widget is narrow enough: float left, clamp to natural width (with a ceiling).
  const w = Math.min(naturalW, hostW * 0.7)
  const textRemaining = hostW - w - 24 // gap
  if (textRemaining < MIN_TEXT_COL) return { width: '100%' }
  return { float: 'left', clear: 'left', width: w, maxWidth: '100%', marginRight: 24, marginBottom: 12 }
}

/** Renders an agent-emitted `<widget>` as a sandboxed, theme-aware blob-iframe.
 *  Full feature set: live-theme injection, auto height-sync, action→chat bridge,
 *  expand, download, open-in-tab, and save-as-artifact. The iframe is
 *  sandbox="allow-scripts" off a blob (null) origin, so widget content cannot
 *  reach the parent app — see widgetSrcdoc.ts for the security model. */
export function WidgetFrame({ html, title = 'Widget', slug, messageTs, widgetIndex = 0, streaming }: Props) {
  const { mode } = useMode()
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)
  const key = useMemo(() => contentHash(html), [html])
  const [height, setHeight] = useState(() => heightCache.get(key) ?? 200)
  // The widget's NATURAL content width (reported by the child) + the host
  // column's width (measured) → drives the dynamic layout decision below.
  const [naturalW, setNaturalW] = useState<number | null>(() => widthCache.get(key) ?? null)
  const [hostW, setHostW] = useState<number | null>(null)
  useEffect(() => {
    const el = wrapRef.current?.parentElement
    if (!el) return
    const ro = new ResizeObserver(() => setHostW(el.clientWidth))
    ro.observe(el)
    setHostW(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  // Re-read theme vars when the resolved mode flips; rebuild srcdoc on html/theme.
  // Inline chat renders FRAMELESS (transparent iframe body, straight against the
  // app canvas); download/open-in-tab build a solid-bg standalone doc instead.
  const themeVars = useMemo(() => readThemeVars(), [mode])
  const srcdoc = useMemo(() => buildSrcdoc({ html, themeVars, mode, includeHost: !streaming, transparentBody: true }), [html, themeVars, mode, streaming])
  const standaloneSrcdoc = useCallback(() => buildSrcdoc({ html, themeVars, mode, includeHost: false }), [html, themeVars, mode])

  // blob: URL (own opaque origin) instead of srcdoc — srcdoc inherits the parent
  // CSP (script-src 'self' would block the widget's inline scripts).
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  useEffect(() => {
    const url = URL.createObjectURL(new Blob([srcdoc], { type: 'text/html;charset=utf-8' }))
    setBlobUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [srcdoc])

  // ── save-as-artifact (bookmark) — stable slug reconciles across refresh ──
  const effSlug = useMemo(() => effectiveWidgetSlug({ explicitSlug: slug, messageTs, widgetIndex }), [slug, messageTs, widgetIndex])
  const [saved, setSaved] = useState(false)
  // Live ref of (saved, slug) so the message bridge (bound once on `key`) reads the
  // latest without re-binding — used to name the artifact for the living-view refresh.
  const liveSlugRef = useRef<{ saved: boolean; slug: string }>({ saved: false, slug: effSlug })
  liveSlugRef.current = { saved, slug: effSlug }

  // height-sync + action→chat bridge from the (trusted) child frame only.
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (!iframeRef.current || e.source !== iframeRef.current.contentWindow) return
      if (e.data?.type === 'widget-height' && typeof e.data.height === 'number') {
        // No max cap — the frameless inline widget grows to fit its content; the
        // page (chat scroll pane) is the scroll container, not the widget.
        const h = Math.max(e.data.height, MIN_HEIGHT)
        setHeight(h); heightCache.set(key, h)
        if (typeof e.data.width === 'number' && e.data.width > 0) { setNaturalW(e.data.width + BODY_PAD); widthCache.set(key, e.data.width + BODY_PAD) }
      } else if (e.data?.type === 'widget-action') {
        const { action, payload } = e.data
        const base = payload && Object.keys(payload).length > 0 ? `[UI] ${action}: ${JSON.stringify(payload)}` : `[UI] ${action}`
        // Living-view (C32): name the source artifact slug so the agent can refresh
        // THIS view in place (artifact_update <slug>) — fetching fresh data + re-
        // rendering — rather than spawning a new artifact. Only when the widget is a
        // saved artifact (has a stable slug the agent can target).
        const { saved: isSaved, slug: sl } = liveSlugRef.current
        const text = isSaved && sl ? `${base} (refresh artifact "${sl}" in place)` : base
        window.dispatchEvent(new CustomEvent('ne:widget-action', { detail: { text } }))
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [key])
  const [savePending, setSavePending] = useState(false)
  useEffect(() => {
    if (streaming) return
    let alive = true
    api.artifactExists(effSlug).then((ex) => { if (alive) setSaved(ex) }).catch(() => {})
    return () => { alive = false }
  }, [effSlug, streaming])

  const toggleSave = useCallback(async () => {
    if (savePending) return
    setSavePending(true)
    try {
      if (saved) { await api.deleteArtifact(effSlug).catch(() => {}); setSaved(false) }
      else { await api.createArtifact({ name: title, content: html, kind: 'widget', source: 'chat', slug: effSlug }).catch(() => {}); setSaved(true) }
    } finally { setSavePending(false) }
  }, [saved, savePending, effSlug, title, html])

  const openInNewTab = useCallback(() => {
    // Build the wrapper via DOM API (browser handles escaping) so agent srcdoc/
    // title can't break out; the inner iframe stays sandboxed. Standalone doc
    // (solid theme bg) — outside the app there's no canvas behind it.
    const doc = document.implementation.createHTMLDocument(title)
    const charset = doc.createElement('meta'); charset.setAttribute('charset', 'utf-8')
    doc.head.insertBefore(charset, doc.head.firstChild)
    doc.body.style.margin = '0'; doc.body.style.height = '100vh'
    const f = doc.createElement('iframe')
    f.setAttribute('sandbox', 'allow-scripts'); f.setAttribute('srcdoc', standaloneSrcdoc())
    f.style.cssText = 'width:100%;height:100%;border:none'
    doc.body.appendChild(f)
    const url = URL.createObjectURL(new Blob([`<!DOCTYPE html>\n${doc.documentElement.outerHTML}`], { type: 'text/html;charset=utf-8' }))
    window.open(url, '_blank')
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }, [standaloneSrcdoc, title])

  const download = useCallback(() => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([standaloneSrcdoc()], { type: 'text/html' }))
    a.download = `${title.replace(/[^a-zA-Z0-9-_ ]/g, '') || 'widget'}.html`
    document.body.appendChild(a); a.click(); document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(a.href), 60_000)
  }, [standaloneSrcdoc, title])

  const actionCluster = (
    <>
      <IconBtn label={saved ? 'Saved — click to remove' : 'Save as artifact'} onClick={toggleSave} disabled={savePending} on={saved}>
        <Bookmark size={13} fill={saved ? 'currentColor' : 'none'} />
      </IconBtn>
      <IconBtn label="Download as HTML" onClick={download}><Download size={13} /></IconBtn>
      <IconBtn label="Open in new tab" onClick={openInNewTab}><ExternalLink size={13} /></IconBtn>
      <IconBtn label={expanded ? 'Minimize' : 'Expand'} onClick={() => setExpanded((v) => !v)}>{expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}</IconBtn>
    </>
  )

  return (
    <motion.div
      ref={wrapRef}
      initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
      className={expanded
        ? 'fixed inset-4 z-50 overflow-hidden rounded-xl border border-outline-variant/50 bg-surface shadow-2xl'
        // Frameless inline render. Layout is DYNAMIC based on the natural content
        // width vs the host column: ≤ ~70% of host + text column keeps readable
        // measure → float left (prose wraps beside); wider → full-width block.
        : 'group/widget relative my-3'}
      style={!expanded ? computeWidgetLayout(naturalW, hostW) : undefined}>
      {expanded && (
        <div className="flex items-center gap-2 border-b border-outline-variant/40 bg-surface-container px-3 py-1.5">
          <span className="truncate text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }}>{title}</span>
          {!streaming && <div className="ml-auto flex items-center gap-0.5">{actionCluster}</div>}
        </div>
      )}
      <AnimatePresence mode="wait">
        {streaming ? (
          <BlueprintSkeleton key="bp" height={240} />
        ) : blobUrl ? (
          <motion.iframe
            key="frame"
            ref={iframeRef} src={blobUrl} sandbox="allow-scripts" title={title}
            className="w-full border-none bg-transparent"
            style={{ height: expanded ? 'calc(100% - 36px)' : height }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={spring.effects}
          />
        ) : null}
      </AnimatePresence>
      {/* hover-revealed action pill (frameless mode) — also keyboard-reachable
          via focus-within so the controls aren't mouse-only. */}
      {!expanded && !streaming && (
        <div className="absolute right-2 top-2 z-10 flex items-center gap-0.5 rounded-pill border border-outline-variant/40 bg-surface-container/90 px-1 py-0.5 opacity-0 backdrop-blur-sm transition-opacity duration-100 focus-within:opacity-100 group-hover/widget:opacity-100">
          {actionCluster}
        </div>
      )}
      {expanded && <div className="fixed inset-0 -z-10 bg-black/55 backdrop-blur-sm" onClick={() => setExpanded(false)} />}
    </motion.div>
  )
}

function IconBtn({ children, label, onClick, disabled, on }: { children: React.ReactNode; label: string; onClick: () => void; disabled?: boolean; on?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={label} aria-label={label}
      className="grid size-7 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface disabled:opacity-50"
      style={on ? { color: 'var(--color-primary)' } : undefined}>
      {children}
    </button>
  )
}
