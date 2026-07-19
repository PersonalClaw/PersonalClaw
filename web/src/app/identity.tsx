import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../lib/api'

/** The operator's identity — INSTANCE-level, not device-level. PersonalClaw is
 *  self-hosted + single-user, so the operator's name is a fact about the
 *  instance and lives on the SERVER (DashboardConfig.user_name via
 *  /api/dashboard/config). That way it follows the user across browsers/machines
 *  and they're never re-onboarded on a new device. Per-device prefs (theme,
 *  width, nav state) stay in localStorage; identity does not.
 *
 *  `onboarded` is DERIVED — a non-empty server name means onboarding is done.
 *  `loaded` gates the first render so we don't flash onboarding before the
 *  server answers. */
interface Identity {
  name: string
  onboarded: boolean
  loaded: boolean
  setName: (name: string) => Promise<void>
  clearName: () => Promise<void>  // re-triggers onboarding
}

const IdentityCtx = createContext<Identity>({ name: '', onboarded: false, loaded: false, setName: async () => {}, clearName: async () => {} })

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [name, setNameState] = useState('')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let alive = true
    api.dashboardConfig()
      .then((c) => { if (alive) setNameState(c.user_name || '') })
      .catch(() => { /* leave name empty → onboarding */ })
      .finally(() => { if (alive) setLoaded(true) })
    return () => { alive = false }
  }, [])

  const setName = async (n: string) => {
    const trimmed = n.trim()
    setNameState(trimmed)  // optimistic
    await api.saveDashboardConfig({ user_name: trimmed }).catch(() => {})
  }
  const clearName = async () => {
    setNameState('')
    await api.saveDashboardConfig({ user_name: '' }).catch(() => {})
  }

  return (
    <IdentityCtx.Provider value={{ name, onboarded: name.trim().length > 0, loaded, setName, clearName }}>
      {children}
    </IdentityCtx.Provider>
  )
}

export const useIdentity = () => useContext(IdentityCtx)

/** First name for greetings; falls back to a neutral label. */
export function firstNameOf(name: string): string {
  return name.trim().split(/\s+/)[0] || 'there'
}
