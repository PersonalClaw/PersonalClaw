import { useEffect, useState } from 'react'

/**
 * The desktop window's sidebar material.
 *
 * On macOS the desktop shell gives the window the system's translucent material and says so by
 * writing `data-window-material="sidebar"` on this page's root (`desktop/windowChrome.js`). The
 * shell leaves it off on other platforms and while the system asks for reduced transparency. The
 * page then leaves the sidebar clear, so the material shows through it, and keeps every reading
 * surface opaque.
 *
 * The page can also say no: a browser asking for `prefers-reduced-transparency: reduce` keeps the
 * sidebar opaque even when the shell offers the material. In a browser tab no shell writes the
 * attribute, so nothing changes there.
 */

export const MATERIAL_ATTRIBUTE = 'data-window-material'
export const REDUCED_TRANSPARENCY = '(prefers-reduced-transparency: reduce)'

function reducedTransparency(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(REDUCED_TRANSPARENCY).matches
}

/** Whether the sidebar shows the window material right now. */
export function readWindowMaterial(): boolean {
  if (typeof document === 'undefined') return false
  return document.documentElement.getAttribute(MATERIAL_ATTRIBUTE) === 'sidebar' && !reducedTransparency()
}

/**
 * Whether the sidebar shows the window material, following the shell and the browser setting.
 * While it shows, the body is clear so the material reaches the sidebar; the shell's own root
 * keeps the work surface opaque.
 */
export function useWindowMaterial(): boolean {
  const [on, setOn] = useState(readWindowMaterial)

  useEffect(() => {
    const update = () => setOn(readWindowMaterial())
    const observer = new MutationObserver(update)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: [MATERIAL_ATTRIBUTE] })
    const mq = typeof window.matchMedia === 'function' ? window.matchMedia(REDUCED_TRANSPARENCY) : null
    mq?.addEventListener('change', update)
    update()
    return () => {
      observer.disconnect()
      mq?.removeEventListener('change', update)
    }
  }, [])

  useEffect(() => {
    if (!on) return
    document.body.style.background = 'transparent'
    return () => { document.body.style.background = '' }
  }, [on])

  return on
}
