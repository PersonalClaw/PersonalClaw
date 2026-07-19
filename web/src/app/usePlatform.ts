import { useCachedData } from '../lib/useCachedData'
import { api } from '../lib/api'

/** The gateway's OS platform token (sys.platform: 'darwin' | 'linux' | 'win32' | '').
 *  Server-authoritative — OS-specific affordances (Finder reveal, screencapture)
 *  run a subprocess on the GATEWAY host, not the browser, so this is the correct
 *  gate. Cached + persisted since it never changes for a running gateway. '' while
 *  loading (so callers hide OS-gated UI until it resolves). */
export function usePlatform(): string {
  const { data } = useCachedData('system:platform', () => api.system().then((s) => s.platform || '').catch(() => ''), { persist: true })
  return data ?? ''
}

/** Convenience: is the gateway running on macOS? */
export function useIsMac(): boolean {
  return usePlatform() === 'darwin'
}
