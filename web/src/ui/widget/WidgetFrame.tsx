import { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import { fvs } from '../../design/fontWeight'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Minimize2, ExternalLink, Download, Bookmark, Pin, Sliders } from 'lucide-react'
import { useMode } from '../../app/theme'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { buildSrcdoc, readThemeVars } from './widgetSrcdoc'
import { effectiveWidgetSlug } from './widgetSlug'
import { useWidgetWire } from './useWidgetActionBridge'
import { useArtifactIteration } from './useArtifactIteration'
import { ArtifactIterationRail } from './ArtifactIterationRail'
import { BlueprintSkeleton } from './BlueprintSkeleton'
import { SquareIconButton } from '../SquareIconButton'
import { spring } from '../../design/motion'
import { invalidateKeys } from '../../lib/data'

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
  // Expanding dims the page behind a click-away scrim — a MOUSE-only collapse. Escape gives the
  // keyboard the same exit, scoped to `expanded` so a collapsed widget does not consume Escape.
  useEffect(() => {
    if (!expanded) return
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setExpanded(false) } }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [expanded])
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
  // The iteration script rides the INLINE document only (the standalone download /
  // open-in-tab documents below deliberately keep the byte-identical body they had —
  // there is no parent to talk to outside the app).
  const srcdoc = useMemo(() => buildSrcdoc({ html, themeVars, mode, includeHost: !streaming, transparentBody: true, editMode: !streaming }), [html, themeVars, mode, streaming])
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

  // ── artifact iteration (AMBIENT-SURFACES §3+§4) ──
  // Saving an EDITMODE tweak cuts a new artifact VERSION, so it needs the widget to
  // BE an artifact: an unsaved widget is saved first (its stable effectiveWidgetSlug),
  // exactly as pinning does, and thereafter each save snapshots.
  const persistVersion = useCallback(async (next: string) => {
    if (!liveSlugRef.current.saved) {
      await api.createArtifact({ name: title, content: next, kind: 'widget', source: 'chat', slug: effSlug })
      setSaved(true)
      return
    }
    await api.updateArtifact(effSlug, { content: next, snapshot: true, event_type: 'iterated' })
  }, [effSlug, title])
  const [railOpen, setRailOpen] = useState(false)
  const iteration = useArtifactIteration(iframeRef, {
    source: html,
    target: { slug: effSlug, persistVersion },
  })

  // height-sync + action forwarding, over the shared (provenance-validated) wire.
  // This child document carries HOST_SCRIPT's `e.isTrusted` click gate, so it is
  // entitled to forward actions — see useWidgetActionBridge.ts for the contract.
  useWidgetWire(iframeRef, {
    forwardActions: true,
    onHeight: (h, w) => {
      // No max cap — the frameless inline widget grows to fit its content; the
      // page (chat scroll pane) is the scroll container, not the widget.
      const capped = Math.max(h, MIN_HEIGHT)
      setHeight(capped); heightCache.set(key, capped)
      if (w) { setNaturalW(w + BODY_PAD); widthCache.set(key, w + BODY_PAD) }
    },
    liveArtifact: () => liveSlugRef.current,
    ...iteration.wire,
  })
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
    // 🪤 THE SIBLING `pin` BELOW ALREADY STATES THE RULE — "roll back on failure (a swallowed error
    // would look like a success)" — and this function was the counter-example: both branches
    // `.catch(() => {})`d the write and then flipped `saved` regardless. A failed delete left the
    // artifact on disk under a button that now offered to save it again; a failed create left the
    // widget claiming it was saved. The flag now moves only after the write it describes returns.
    try {
      if (saved) {
        await api.deleteArtifact(effSlug)
        setSaved(false)
      } else {
        await api.createArtifact({ name: title, content: html, kind: 'widget', source: 'chat', slug: effSlug })
        setSaved(true)
      }
      // Both directions change the artifact collection, and the composer's attach picker — a sibling
      // on THIS surface — caches it. Without this, saving a widget from a chat turn left the picker
      // unable to offer it, and deleting one left it offering something gone.
      invalidateKeys('artifacts:', true)
    } catch (e) {
      notify(`Couldn't ${saved ? 'remove' : 'save'} this widget: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setSavePending(false) }
  }, [saved, savePending, effSlug, title, html])

  // ── pin-to-dashboard (AMBIENT-SURFACES §1.3) — save-then-POST a tile ──
  // Pinning IMPLIES saving: an unpinned-unsaved widget is first saved via the same
  // createArtifact path (its stable effectiveWidgetSlug), THEN POSTed as an
  // artifact:<slug> tile onto the Overview home. Optimistic: flip the pin state
  // immediately, roll back on failure (a swallowed error would look like a success).
  const [pinned, setPinned] = useState(false)
  const [pinPending, setPinPending] = useState(false)
  const pin = useCallback(async () => {
    if (pinPending || pinned) return
    setPinPending(true)
    setPinned(true) // optimistic
    try {
      if (!saved) {
        await api.createArtifact({ name: title, content: html, kind: 'widget', source: 'chat', slug: effSlug })
        setSaved(true)
      }
      await api.pinTile('overview', { slug: effSlug, size: 'm' })
    } catch (e) {
      setPinned(false) // roll back — the tile did not persist
      notify(`Couldn't pin to dashboard: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      setPinPending(false)
    }
  }, [pinPending, pinned, saved, effSlug, title, html])

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
      {/* The rail is revealed content (`{railOpen && !streaming && …}` below), so this announces
          expansion. Bookmark and Pin beneath it keep `on`: those are states, not disclosures. */}
      <SquareIconButton label={railOpen ? 'Close the iteration rail' : 'Iterate — tweak parameters or mark elements'}
        onClick={() => setRailOpen((v) => !v)} ariaExpanded={railOpen}>
        <Sliders size={13} />
      </SquareIconButton>
      <SquareIconButton label={saved ? 'Saved — click to remove' : 'Save as artifact'} onClick={toggleSave} disabled={savePending} on={saved}>
        <Bookmark size={13} fill={saved ? 'currentColor' : 'none'} />
      </SquareIconButton>
      <SquareIconButton label={pinned ? 'Pinned to dashboard' : 'Pin to dashboard'} onClick={pin} disabled={pinPending || pinned} on={pinned}>
        <Pin size={13} fill={pinned ? 'currentColor' : 'none'} />
      </SquareIconButton>
      <SquareIconButton label="Download as HTML" onClick={download}><Download size={13} /></SquareIconButton>
      <SquareIconButton label="Open in new tab" onClick={openInNewTab}><ExternalLink size={13} /></SquareIconButton>
      <SquareIconButton label={expanded ? 'Minimize' : 'Expand'} onClick={() => setExpanded((v) => !v)}>{expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}</SquareIconButton>
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
          <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{title}</span>
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
      {railOpen && !streaming && (
        <ArtifactIterationRail it={iteration} onClose={() => setRailOpen(false)}
          className="w-full rounded-b-md border-t" />
      )}
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

