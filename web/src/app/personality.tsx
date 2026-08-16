import { createContext, Suspense, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useAppearance } from './appearance'
import {
  DEFAULT_PERSONALITY,
  PERSONALITIES,
  getShellElement,
  resolvePersonality,
  type Personality,
} from '../design/personalities'
import { getErrorTreatment, type ErrorTreatment } from '../design/errorTreatments'

/** Applies the active PERSONALITY's non-color behaviors (PERSONALITY-THEMES §S1).
 *
 *  Colors are deliberately NOT handled here — a personality names a `baseScheme`
 *  and the existing appearance store applies it, so palettes keep their single
 *  code path (and their automatic contrast coverage). What this owns is the
 *  identity chrome that had no home before: the wordmark label, the tab title,
 *  the favicon, and the UI density.
 *
 *  The load-bearing property is **full restore**. Activating a personality must
 *  be completely undoable, so every document-level mutation is captured on first
 *  use and written back when the default identity is selected. Without that,
 *  trying a personality would silently leave a wrong favicon or title behind, and
 *  the switch would feel like damage rather than a preference.
 *
 *  Anything that changes SERVER state (the assistant's name) is not applied here
 *  at all — the picker offers it with a checkbox. See DesignPanel. */

const STORAGE_KEY = 'personality'

interface Ctx {
  personality: Personality
  /** Every entry, for the picker. */
  all: Personality[]
  /** Switch identity. Passing the default id restores the original chrome. */
  activate: (id: string) => void
  /** The wordmark label to render (falls back to the product name). */
  wordmarkLabel: string
}

const PersonalityCtx = createContext<Ctx | null>(null)

/** The document state as it was BEFORE any personality touched it. Captured once,
 *  at module scope, so a re-render or a provider remount can't overwrite the
 *  pristine values with already-personalized ones. */
const pristine = {
  title: typeof document !== 'undefined' ? document.title : 'PersonalClaw',
  favicon:
    typeof document !== 'undefined'
      ? (document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.getAttribute('href') ?? null)
      : null,
}

function readStored(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_PERSONALITY
  } catch {
    return DEFAULT_PERSONALITY
  }
}

function setFavicon(href: string | null) {
  if (typeof document === 'undefined') return
  const link = document.querySelector<HTMLLinkElement>('link[rel~="icon"]')
  if (!link) return
  if (href) link.setAttribute('href', href)
  else link.removeAttribute('href')
}

export function PersonalityProvider({ children }: { children: ReactNode }) {
  const { applyScheme, setSelect } = useAppearance()
  const [id, setId] = useState<string>(readStored)
  const personality = useMemo(() => resolvePersonality(id), [id])
  const isDefault = personality.id === DEFAULT_PERSONALITY

  // Apply the document-level behaviors. Each branch restores the PRISTINE value
  // rather than a hardcoded one, so the default identity is genuinely "as it was".
  useEffect(() => {
    const b = personality.behavior
    if (typeof document !== 'undefined') {
      document.title = isDefault ? pristine.title : (b.documentTitle ?? pristine.title)
      setFavicon(isDefault ? pristine.favicon : (b.faviconHref ?? pristine.favicon))
      // A data attribute so CSS (and embedded surfaces) can key off the identity
      // without re-reading every token — mirrors how the scheme sets data-theme.
      document.documentElement.dataset.personality = personality.id
    }
  }, [personality, isDefault])

  const activate = useCallback(
    (next: string) => {
      const target = resolvePersonality(next)
      setId(target.id)
      try {
        localStorage.setItem(STORAGE_KEY, target.id)
      } catch {
        /* private mode — the identity just won't persist across reloads */
      }
      // Colors + density go through the EXISTING appearance mechanisms.
      applyScheme(target.baseScheme)
      setSelect('--ui-density', target.behavior.uiDensity ?? 'comfortable')
    },
    [applyScheme, setSelect],
  )

  const value = useMemo<Ctx>(
    () => ({
      personality,
      all: PERSONALITIES,
      activate,
      wordmarkLabel: personality.behavior.wordmarkLabel ?? 'PersonalClaw',
    }),
    [personality, activate],
  )

  return <PersonalityCtx.Provider value={value}>{children}</PersonalityCtx.Provider>
}

/** The App-shell slot for the active personality's decorative shell element
 *  (PERSONALITY-THEMES §S2). Mounted once in `App.tsx`'s main shell.
 *
 *  The default identity — and every standard scheme, which has no `Personality`
 *  entry at all — declares no `shellElement`, so this returns `null` and NOTHING
 *  mounts. That is the additive guarantee stated as code: not a hidden element, not
 *  an empty wrapper, no node in the tree. `personalityShellElement.test.tsx` asserts
 *  absence under every personality that declares none and presence under the one
 *  that does, so neither half can pass by always doing the same thing.
 *
 *  `Suspense fallback={null}` because the entry is lazy (see `SHELL_ELEMENTS`): the
 *  chunk arrives a frame or two after the shell, and the honest placeholder for a
 *  decoration is nothing at all. Deliberately NOT rendered in embed mode or on the
 *  `#/companion` route — both return early with page content only, and a host
 *  iframe inheriting the shell's atmosphere is exactly the nesting embed mode
 *  exists to avoid. */
export function PersonalityShellElement() {
  const Element = getShellElement(usePersonality().personality.behavior.shellElement)
  if (!Element) return null
  return (
    <Suspense fallback={null}>
      <Element />
    </Suspense>
  )
}

/** The active personality's error-surface treatment, or `null` for an identity
 *  that declares none (which is every standard scheme, including the default).
 *
 *  Guarded on purpose. This runs while an error surface is already rendering, and
 *  a throw there would escape the boundary that is trying to draw it — turning one
 *  broken page into a blank app. `usePersonality` already tolerates a missing
 *  provider and `getErrorTreatment` is total, so the `catch` only covers the last
 *  case (a provider handing over a malformed value) — but the error path is the
 *  one place where "shouldn't happen" is not good enough. `useContext` still runs
 *  unconditionally, so hook order never varies between renders. */
export function useErrorTreatment(): ErrorTreatment | null {
  try {
    return getErrorTreatment(usePersonality().personality.behavior.errorTreatment)
  } catch {
    return null
  }
}

export function usePersonality(): Ctx {
  const ctx = useContext(PersonalityCtx)
  if (ctx) return ctx
  // Tolerate use outside the provider (tests, isolated stories) rather than
  // throwing — the identity layer is decoration, never a hard dependency.
  return {
    personality: resolvePersonality(DEFAULT_PERSONALITY),
    all: PERSONALITIES,
    activate: () => {},
    wordmarkLabel: 'PersonalClaw',
  }
}
