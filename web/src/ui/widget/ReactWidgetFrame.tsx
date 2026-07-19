import { useState, useRef, useEffect, useMemo } from 'react'
import { motion } from 'framer-motion'
import { Maximize2, Minimize2, ExternalLink, AlertTriangle } from 'lucide-react'
import { useMode } from '../../app/theme'
import { buildReactSrcdoc, readThemeVars } from './widgetSrcdoc'

const MIN_HEIGHT = 80
const MAX_HEIGHT = 640

interface Props {
  /** JSX source defining a top-level `App` component (authored against the
   *  window React / ReactDOM globals). */
  jsx: string
  title?: string
}

/** Renders a dynamic React (kind:'react') artifact as a sandboxed, theme-aware
 *  blob-iframe. Same isolation as WidgetFrame (sandbox="allow-scripts" off a
 *  blob null origin + strict CSP — see widgetSrcdoc.ts); React/ReactDOM + Babel
 *  load INSIDE the frame from the CSP-allowed CDNs (Babel ~3MB downloads only
 *  when a react artifact first renders). A render error surfaces inline via the
 *  child's `widget-error` postMessage instead of a blank frame. */
export function ReactWidgetFrame({ jsx, title = 'React widget' }: Props) {
  const { mode } = useMode()
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [height, setHeight] = useState(240)
  const [error, setError] = useState<string | null>(null)

  const themeVars = useMemo(() => readThemeVars(), [mode])
  const srcdoc = useMemo(() => buildReactSrcdoc({ jsx, themeVars, mode }), [jsx, themeVars, mode])

  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  useEffect(() => {
    setError(null)
    const url = URL.createObjectURL(new Blob([srcdoc], { type: 'text/html;charset=utf-8' }))
    setBlobUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [srcdoc])

  // height-sync + error surfacing from the (trusted) child frame only.
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (!iframeRef.current || e.source !== iframeRef.current.contentWindow) return
      if (e.data?.type === 'widget-height' && typeof e.data.height === 'number') {
        setHeight(Math.min(Math.max(e.data.height, MIN_HEIGHT), MAX_HEIGHT))
      } else if (e.data?.type === 'widget-error') {
        setError(String(e.data.message || 'Render error'))
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])

  const openInNewTab = () => {
    const doc = document.implementation.createHTMLDocument(title)
    const charset = doc.createElement('meta'); charset.setAttribute('charset', 'utf-8')
    doc.head.insertBefore(charset, doc.head.firstChild)
    doc.body.style.margin = '0'; doc.body.style.height = '100vh'
    const f = doc.createElement('iframe')
    f.setAttribute('sandbox', 'allow-scripts'); f.setAttribute('srcdoc', srcdoc)
    f.style.cssText = 'width:100%;height:100%;border:none'
    doc.body.appendChild(f)
    const url = URL.createObjectURL(new Blob([`<!DOCTYPE html>\n${doc.documentElement.outerHTML}`], { type: 'text/html;charset=utf-8' }))
    window.open(url, '_blank')
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
      className={expanded
        ? 'fixed inset-4 z-50 overflow-hidden rounded-xl border border-outline-variant/50 bg-surface shadow-2xl'
        : 'my-3 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-low'}>
      <div className="flex items-center gap-2 border-b border-outline-variant/40 bg-surface-container px-3 py-1.5">
        <span className="truncate text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }}>{title}</span>
        {error && (
          <span className="inline-flex items-center gap-1 text-[0.7rem]" style={{ color: 'var(--color-danger)' }}>
            <AlertTriangle size={11} /> error
          </span>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <IconBtn label="Open in new tab" onClick={openInNewTab}><ExternalLink size={13} /></IconBtn>
          <IconBtn label={expanded ? 'Minimize' : 'Expand'} onClick={() => setExpanded((v) => !v)}>{expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}</IconBtn>
        </div>
      </div>
      {blobUrl && (
        <iframe ref={iframeRef} src={blobUrl} sandbox="allow-scripts" title={title}
          className="w-full border-none bg-surface"
          style={{ height: expanded ? 'calc(100% - 36px)' : Math.min(height, MAX_HEIGHT) }} />
      )}
      {expanded && <div className="fixed inset-0 -z-10 bg-black/55 backdrop-blur-sm" onClick={() => setExpanded(false)} />}
    </motion.div>
  )
}

function IconBtn({ children, label, onClick }: { children: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} title={label} aria-label={label}
      className="grid size-7 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface">
      {children}
    </button>
  )
}
