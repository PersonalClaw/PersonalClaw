import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

export type Mode = 'dark' | 'light'
export type Preference = 'dark' | 'light' | 'auto'

interface Ctx {
  mode: Mode            // the RESOLVED mode (what's actually applied)
  preference: Preference // the user's choice (auto follows the OS)
  toggle: () => void     // dark ⇄ light (sets an explicit preference)
  setPreference: (p: Preference) => void
}
const ThemeCtx = createContext<Ctx>({ mode: 'dark', preference: 'dark', toggle: () => {}, setPreference: () => {} })

const KEY = 'mode'

function systemMode(): Mode {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

/** Mode provider — resolves preference (dark | light | auto→OS) to a concrete
 *  mode, applies `.light` to <html>, and persists the choice. Auto live-updates
 *  with the OS color-scheme. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPref] = useState<Preference>(() => {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' || v === 'auto' ? v : 'dark'
  })
  const [sysMode, setSysMode] = useState<Mode>(systemMode)

  // track the OS preference while in auto
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: light)')
    if (!mq) return
    const onChange = () => setSysMode(mq.matches ? 'light' : 'dark')
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])

  // live-sync the preference across same-origin documents (other tabs, and
  // embedded app iframes that load the full shell with ?embed=1). The `storage`
  // event fires in every OTHER document when one writes localStorage, so a theme
  // toggle in the host shell re-themes any open ChatEmbed without a reload.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== KEY) return
      const v = e.newValue
      if (v === 'light' || v === 'dark' || v === 'auto') setPref(v)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const mode: Mode = preference === 'auto' ? sysMode : preference

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('light', mode === 'light')
    root.dataset.mode = mode
    localStorage.setItem(KEY, preference)
  }, [mode, preference])

  return (
    <ThemeCtx.Provider value={{
      mode, preference,
      toggle: () => setPref(mode === 'dark' ? 'light' : 'dark'),
      setPreference: setPref,
    }}>
      {children}
    </ThemeCtx.Provider>
  )
}

export const useMode = () => useContext(ThemeCtx)
