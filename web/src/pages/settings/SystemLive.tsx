import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { api, isSwitchedOff } from '../../lib/api'
import type { DashboardStatus, DoctorReport, SystemInfo } from '../../lib/api'

// ── The machine's live feed ────────────────────────────────────────────────────────────────────
// What Settings › System shows, and nothing else: the gateway's status, the host's metrics and the
// Doctor rollup. Home's feed (`dashboard/DashboardLive`) carries the owner's work and no longer
// reads the host metrics or the probe, because no Home band shows them; this page mounts this
// provider on its own, so it polls none of Home's lanes either.

export interface SystemLiveData {
  status: DashboardStatus | null
  /** The status read's own failure. Without it a failed read left `status` null, and the strip
   *  showed its loading skeleton for as long as the page stayed open. */
  statusErr: unknown
  /** Live host metrics (cpu/mem/net/disk/load) from /api/system, on the fast visibility-gated
   *  cadence so the strip shows live rates. */
  system: SystemInfo | null
  /** The metrics read's own failure: the strip says the host could not be read, rather than
   *  leaving the metrics out as if this host had none. */
  systemErr: unknown
  /** Re-run the status and metrics reads, the Retry beside their failure. */
  retry: () => void
  /** The Doctor rollup, cached 30s server-side, so read on the slow cadence. */
  doctor: DoctorReport | null
  /** The probe's own failure. Distinct from `doctor: null`, which also means "not read yet", and
   *  from a healthy report: a health surface that goes quiet is read as "nothing wrong", so a
   *  consumer must render "unknown", never silence. */
  doctorErr: unknown
  /** The Doctor is switched off (`resilience.doctor_enabled`): its read answered the decided
   *  `{"enabled": false}`. A third state beside a report and a failure, rendered as neither.
   *  `doctor` is `null` while this is true. */
  doctorOff: boolean
}

const SystemLiveContext = createContext<SystemLiveData | null>(null)

/** Consume the machine's live feed. Throws outside the provider, so the strip can never render
 *  an empty feed as if it had been read. */
export function useSystemLive(): SystemLiveData {
  const ctx = useContext(SystemLiveContext)
  if (!ctx) throw new Error('useSystemLive must be used within <SystemLiveProvider>')
  return ctx
}

const FAST_POLL = 8000
const SLOW_POLL = 20000

export function SystemLiveProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<DashboardStatus | null>(null)
  const [statusErr, setStatusErr] = useState<unknown>(null)
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [systemErr, setSystemErr] = useState<unknown>(null)
  const [doctor, setDoctor] = useState<DoctorReport | null>(null)
  const [doctorErr, setDoctorErr] = useState<unknown>(null)
  const [doctorOff, setDoctorOff] = useState(false)

  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const guard = <T,>(set: (v: T) => void) => (v: T) => { if (alive.current) set(v) }

  // Each read keeps its failure, and a later success clears it. A later failure keeps the last
  // good value, so a strip that has read once goes on showing it.
  const loadStatus = useCallback(() => {
    api.status().then((d) => { guard(setStatus)(d); guard(setStatusErr)(null) }).catch((e) => guard(setStatusErr)(e))
  }, [])
  const loadSystem = useCallback(() => {
    api.system().then((d) => { guard(setSystem)(d); guard(setSystemErr)(null) }).catch((e) => guard(setSystemErr)(e))
  }, [])
  // 🔴 The probe's failure is kept, not swallowed: the strip surfaces its health row only when
  // `!doctor.ok`, so a failed probe left `doctor` null and impersonated health.
  const loadDoctor = useCallback(() => {
    api.doctor().then((d) => {
      const off = isSwitchedOff(d)
      guard(setDoctorOff)(off); guard(setDoctor)(off ? null : d); guard(setDoctorErr)(null)
    }).catch((e) => guard(setDoctorErr)(e))
  }, [])

  const refreshAll = useCallback(() => { loadStatus(); loadSystem(); loadDoctor() }, [loadStatus, loadSystem, loadDoctor])

  // An update stepping through its pipeline changes `update_available`; re-read status on it.
  const onMessage = useCallback((m: WsMessage) => { if (m.type === 'update_progress') loadStatus() }, [loadStatus])
  useChatSocket(onMessage, refreshAll)

  useEffect(() => { refreshAll() }, [refreshAll])
  useVisiblePoll(loadSystem, FAST_POLL, { immediate: false })
  useVisiblePoll(() => { loadStatus(); loadDoctor() }, SLOW_POLL, { immediate: false })

  const retry = useCallback(() => { loadStatus(); loadSystem() }, [loadStatus, loadSystem])
  const value: SystemLiveData = { status, statusErr, system, systemErr, retry, doctor, doctorErr, doctorOff }
  return <SystemLiveContext.Provider value={value}>{children}</SystemLiveContext.Provider>
}
