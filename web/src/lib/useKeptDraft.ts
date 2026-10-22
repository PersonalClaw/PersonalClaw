import { useCallback, useState } from 'react'

// ── An unsent comment, kept for what it is about ──────────────────────────────────────────────
//
// A comment written on a change or a plan step and not yet sent is kept in this browser under what
// it is about (`kept-draft:<key>`), so a reload, a closed pane or a crash gives it back (#3821's
// local-draft recovery). Sending clears it. Each key has its own draft: moving to another change or
// step shows that one's, and comes back to this one's.

const PREFIX = 'kept-draft:'

function read(key: string): string {
  try { return localStorage.getItem(PREFIX + key) ?? '' } catch { return '' }
}

export function useKeptDraft(key: string): [text: string, setText: (text: string) => void, clear: () => void] {
  const [kept, setKept] = useState(() => ({ key, text: read(key) }))
  // Another key reads its own draft, in this render, rather than showing the last one's.
  if (kept.key !== key) setKept({ key, text: read(key) })
  const text = kept.key === key ? kept.text : read(key)
  const setText = useCallback((next: string) => {
    setKept({ key, text: next })
    try {
      if (next) localStorage.setItem(PREFIX + key, next)
      else localStorage.removeItem(PREFIX + key)
    } catch { /* storage is unavailable: the draft lives as long as the page */ }
  }, [key])
  const clear = useCallback(() => setText(''), [setText])
  return [text, setText, clear]
}
