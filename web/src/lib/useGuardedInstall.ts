import { useCallback, useRef, useState } from 'react'
import type { SkillInstallResult, AppScanReport } from './api'

/** Normalized outcome of a supply-chain-guarded SKILL install. The consent state machine
 *  only cares about three things: did it succeed, is it an overridable warning the user can
 *  consent past, and what did the scanner find (incl. a terminal `dangerous`).
 *
 *  Apps do not use this: an app install is reviewed on the server BEFORE any install request
 *  and committed against a consent digest, through `pages/apps/installConsent.useAppInstall`. */
export interface GuardedResult {
  ok: boolean
  /** An overridable WARNING verdict — a re-attempt with consent is allowed.
   *  A `dangerous` verdict is NOT consentable (detect via `scan.verdict`). */
  needsConsent: boolean
  scan: AppScanReport | null
  error?: string
}

/** TERMINAL refusals — a gate outcome no amount of consent overrides. Two causes today:
 *  a `dangerous` scan verdict (malicious CONTENT) and an `invalid` signature (broken
 *  PROVENANCE — the bundle is not the bytes its signature covers). Returns the sentence
 *  to show the user, or `''` when the result is consentable.
 *
 *  One function rather than a `dangerous` boolean per surface: the install surfaces each had
 *  their own copy of the verdict check, so a second terminal cause would otherwise have to be
 *  remembered in several places — and the one that forgot would offer "Install anyway" on a
 *  tampered artifact. It reads only the scan, so an app review and a skill result share it. */
export function terminalRefusalReason(r: { scan?: AppScanReport | null } | null | undefined): string {
  if (!r) return ''
  if (r.scan?.signature?.state === 'invalid') {
    return r.scan.signature.reason
      ? `This bundle's signature is invalid — ${r.scan.signature.reason}. It cannot be installed.`
      : "This bundle's signature is invalid. It cannot be installed."
  }
  if (r.scan?.verdict === 'dangerous') {
    return 'The security scanner flagged dangerous content. This app cannot be installed.'
  }
  return ''
}

/** Should this failure open the consent/findings panel rather than dead-end as a bare
 *  error string? A consentable warning or a terminal refusal — each has findings to show. */
export function isBlockingResult(r: GuardedResult | null | undefined): boolean {
  if (!r) return false
  return !!(r.needsConsent || terminalRefusalReason(r))
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

/** The guarded-install state machine for the skill marketplace, so its install cannot
 *  silently forget to surface the scanner's warning/findings (the bug that stranded
 *  warning-verdict installs with a dead-end error and no way to consent).
 *
 *  `run(confirm)` performs one install attempt and returns a {@link GuardedResult}
 *  (adapt the raw API result with {@link guardedFromSkill}). It's held in a ref so the
 *  returned callbacks stay stable and always invoke the latest closure. */
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
        return r
      }
      // A warning (consentable) or a terminal refusal → surface its findings in the panel
      // rather than dead-ending on a bare error.
      if (isBlockingResult(r)) { setBlocked(r); return r }
      // 🔑 A CONFIRMED re-attempt that fails for a reason the scan gate never anticipated is
      // NOT a re-offer of the same findings, and `blocked` must not be left holding the FIRST
      // attempt's warning — or the findings stay mounted with this error rendered where the
      // user is not looking, and "Install anyway" reads as doing nothing (issue #3540).
      setBlocked(null)
      setError(r.error || 'install failed')
      return r
    } catch (e) {
      // Same reasoning: a thrown exception during a confirmed re-attempt is exactly as
      // unblocking as a plain `ok:false`.
      setBlocked(null)
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
