import { useCallback, useRef, useState } from 'react'
import type { AppInstallResult, SkillInstallResult, AppScanReport } from './api'

/** Normalized outcome of a supply-chain-guarded install, folding the app- and
 *  skill-install response shapes into one. The consent state machine only cares
 *  about three things: did it succeed, is it an overridable warning the user can
 *  consent past, and what did the scanner find (incl. a terminal `dangerous`). */
export interface GuardedResult {
  ok: boolean
  /** An overridable WARNING verdict — a re-attempt with consent is allowed.
   *  A `dangerous` verdict is NOT consentable (detect via `scan.verdict`). */
  needsConsent: boolean
  scan: AppScanReport | null
  error?: string
  /** P21: the app must be installed on the user's LOCAL machine — the server can't
   *  install it, but hands back a copy-paste one-liner. Not consentable (no re-attempt
   *  succeeds server-side); the caller renders the command for the user to run. */
  clientInstall?: { shell?: string; postInstall?: string } | null
  /** The install succeeded but the gateway must RESTART before the app fully takes
   *  effect (a new python dependency was installed, or boot-time registration is
   *  needed). Callers surface this to the user rather than pretending it's live. */
  restartRequired?: boolean
}

/** App install/update (`/api/apps`): `needs_consent` + `scan` ride the 409 body;
 *  a P21 platform-gated app rides `needs_client_install` + `client_install`. */
export function guardedFromApp(r: AppInstallResult): GuardedResult {
  return { ok: r.ok, needsConsent: !!r.needs_consent, scan: r.scan, error: r.error,
           clientInstall: r.needs_client_install ? (r.client_install ?? {}) : null,
           restartRequired: !!r.restart_required }
}

/** Skill install (`/api/skills/install`): a 409 warning is `overridable:true`;
 *  a 403 dangerous is `overridable:false` with `scan.verdict === 'dangerous'`. */
export function guardedFromSkill(r: SkillInstallResult): GuardedResult {
  return { ok: !!r.ok, needsConsent: !!r.overridable, scan: r.scan ?? null, error: r.error }
}

export interface GuardedInstall {
  busy: boolean
  /** The blocking scan outcome from the last attempt: an overridable warning
   *  (offer "Install anyway") or a terminal `dangerous` (findings only). null
   *  once resolved or when the failure was a plain error. */
  blocked: GuardedResult | null
  /** A non-scan failure (bad source, already installed, network) — plain text. */
  error: string | null
  /** First attempt, without consent. */
  install: () => Promise<GuardedResult | null>
  /** Re-attempt WITH consent — only meaningful after `blocked.needsConsent`. */
  confirmInstall: () => Promise<GuardedResult | null>
  /** Clear blocked/error state (e.g. on close / source change). */
  reset: () => void
}

/** Centralizes the guarded-install state machine so every install call site —
 *  app catalog card, install/update modal, skill marketplace detail — shares
 *  identical consent semantics and none can silently forget to surface the
 *  scanner's `needs_consent`/findings (the bug that stranded warning-verdict
 *  installs with a dead-end error and no way to consent).
 *
 *  `run(confirm)` performs one install attempt and returns a {@link GuardedResult}
 *  (use {@link guardedFromApp} / {@link guardedFromSkill} to adapt the raw API
 *  result). It's held in a ref so the returned callbacks stay stable and always
 *  invoke the latest closure. */
export function useGuardedInstall(run: (confirm: boolean) => Promise<GuardedResult>): GuardedInstall {
  const [busy, setBusy] = useState(false)
  const [blocked, setBlocked] = useState<GuardedResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const runRef = useRef(run)
  runRef.current = run

  const attempt = useCallback(async (confirm: boolean): Promise<GuardedResult | null> => {
    setBusy(true)
    setError(null)
    if (!confirm) setBlocked(null)
    try {
      const r = await runRef.current(confirm)
      if (r.ok) {
        setBlocked(null)
        if (r.restartRequired) {
          // Surface the boot-time gap loudly: the app is installed but won't fully
          // work until the gateway restarts (new python dep / boot-time registration).
          window.dispatchEvent(new CustomEvent('ne:toast', { detail: {
            level: 'info',
            message: 'Installed — restart the gateway for this app to fully take effect.',
          }}))
        }
        return r
      }
      // A warning (consentable), a dangerous verdict, OR a P21 client-install
      // directive → surface it in the panel (findings / the copy-paste one-liner)
      // rather than dead-ending on a bare error string.
      if (r.needsConsent || r.scan?.verdict === 'dangerous' || r.clientInstall) { setBlocked(r); return r }
      setError(r.error || 'install failed')
      return r
    } catch (e) {
      setError(String((e as Error)?.message || e))
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  const install = useCallback(() => attempt(false), [attempt])
  const confirmInstall = useCallback(() => attempt(true), [attempt])
  const reset = useCallback(() => { setBlocked(null); setError(null) }, [])

  return { busy, blocked, error, install, confirmInstall, reset }
}
