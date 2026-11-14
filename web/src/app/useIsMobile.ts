import { useEffect, useState } from 'react'

/** Mobile-width breakpoint (≤ 768px, the tablet-portrait/phone threshold). Reactive
 *  via `matchMedia` so layout responds to rotation / window resize without a reload.
 *  Used by the shell to switch the nav rail from an in-flow column (desktop) to a
 *  collapse-by-default overlay drawer (mobile). */
const MOBILE_QUERY = '(max-width: 768px)'

export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches)
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY)
    const onChange = () => setMobile(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return mobile
}

/** Whether the window is narrower than *px* (`max-width: <px - 1>px`), reactive as `useIsMobile`. */
export function useNarrowerThan(px: number): boolean {
  const query = `(max-width: ${px - 1}px)`
  const [narrow, setNarrow] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setNarrow(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return narrow
}
