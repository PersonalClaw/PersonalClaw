import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { ArrowLeftRight, Loader2 } from 'lucide-react'
import { api, type Artifact } from '../../lib/api'
import { useMode } from '../../app/theme'
import { resolveContentType } from '../../ui/content/contentTypes'
import { Segmented } from '../../ui/Segmented'

// Monaco's side-by-side diff editor, lazy — it shares the locally-bundled monaco
// from monacoSetup (never a CDN), same as the code cockpit's DiffView.
import { useDiffTeardown } from '../../ui/useDiffTeardown'
const MonacoDiff = lazy(() => import('@monaco-editor/react').then((m) => ({ default: m.DiffEditor })))

/** Compare two versions of one artifact (ARTIFACTS-EVOLUTION S3, T3.3).
 *
 *  How the comparison renders is decided by the content-type registry rather than a
 *  local list of kinds — the same rule the viewer follows, so a newly registered kind
 *  gets sensible behavior here without editing this file:
 *
 *    * binary (image)  → the two versions side by side, because a pixel diff of a
 *                        rendered image is noise; what a person wants is before/after.
 *    * everything else → a real text diff of the two bodies. Visual kinds (widget,
 *                        html, svg) ARE text underneath, and their source is what
 *                        actually changed; a screenshot-style comparison of two
 *                        sandboxed iframes would be prettier and far less useful for
 *                        answering "what did the agent change?".
 *
 *  Both bodies come from `GET /versions/{n}`, which returns each version's immutable
 *  content. NOTE the label discipline below: that response reports the artifact's
 *  CURRENT version number, not the one requested, so every label here is built from
 *  the requested number and never from the payload. */
export function ArtifactCompare({ art, versions }: { art: Artifact; versions: number[] }) {
  const { mode } = useMode()
  const ordered = useMemo(() => versions.slice().sort((a, b) => a - b), [versions])
  // Default to the two most recent versions — "what changed in the last iteration?"
  // is the question this surface exists to answer.
  const [left, setLeft] = useState<number>(() => ordered[ordered.length - 2] ?? ordered[0] ?? 1)
  const [right, setRight] = useState<number>(() => ordered[ordered.length - 1] ?? 1)
  const [bodies, setBodies] = useState<{ left: string; right: string } | null>(null)
  const [error, setError] = useState('')

  const ctype = useMemo(() => resolveContentType({ kind: art.kind }), [art.kind])
  // Issue 582: detach the models before unmount — Close compare hard-unmounts this
  // component, the one teardown route the bodies-keeping comment below cannot cover.
  const onDiffMount = useDiffTeardown()
  const isBinary = !!ctype?.binary

  useEffect(() => {
    let alive = true
    setError('')
    // Deliberately NOT clearing `bodies` here. Blanking it swaps the mounted diff for
    // a spinner, and unmounting DiffEditor while its text models are still attached
    // makes monaco throw "TextModel got disposed before DiffEditorWidget model got
    // reset" on every version switch. Keeping the previous diff on screen until the
    // next pair arrives avoids the teardown AND reads better — the content updates in
    // place instead of flashing empty. The spinner still covers the FIRST load, when
    // there is nothing to keep.
    Promise.all([api.artifactVersion(art.slug, left), api.artifactVersion(art.slug, right)])
      .then(([a, b]) => { if (alive) setBodies({ left: a.content ?? '', right: b.content ?? '' }) })
      .catch((e) => { if (alive) setError(String((e as Error)?.message || e)) })
    return () => { alive = false }
  }, [art.slug, left, right])

  if (ordered.length < 2) {
    return (
      <div className="px-m py-l text-center text-on-surface-low text-[0.8125rem]">
        Only one version so far — there's nothing to compare yet. Snapshot a change and
        this will show what moved.
      </div>
    )
  }

  // `h-full` rather than `flex-1`: the host is a plain block wrapper, not a flex
  // column, so flex-1 would resolve to zero height and Monaco (which needs a sized
  // container) would render an invisible one-pixel strip. Matches DiffView's root.
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-outline/40 px-m py-2">
        <VersionPicker label="Compare" value={left} options={ordered} onChange={setLeft} />
        <button type="button" onClick={() => { setLeft(right); setRight(left) }}
          title="Swap which version is on each side"
          aria-label="Swap the two versions"
          className="inline-flex items-center rounded-md px-1.5 h-7 text-on-surface-low hover:bg-surface-high hover:text-on-surface transition-colors">
          <ArrowLeftRight size={13} />
        </button>
        <VersionPicker label="with" value={right} options={ordered} onChange={setRight} />
        {left === right && (
          <span className="text-on-surface-low text-[0.75rem]">
            Same version on both sides — pick two to see a difference.
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {error
          ? <div className="px-m py-l text-center text-[0.8125rem]" style={{ color: 'var(--color-error)' }}>
              Couldn't load those versions: {error}
            </div>
          : !bodies
            ? <div className="flex h-full items-center justify-center">
                <Loader2 size={20} className="animate-spin text-on-surface-low" />
              </div>
            : isBinary
              ? <ImagePair art={art} left={left} right={right} />
              : <Suspense fallback={<div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}>
                  <MonacoDiff
                    onMount={onDiffMount}
                    original={bodies.left} modified={bodies.right}
                    language={diffLanguage(art.kind)}
                    theme={mode === 'light' ? 'light' : 'vs-dark'}
                    // ignoreTrimWhitespace defaults to TRUE, which silently hides
                    // whitespace-only changes. An agent re-rendering a widget often
                    // re-indents it, and "nothing changed" would be a lie. Always
                    // show whitespace differences (the DiffView rationale, verbatim).
                    options={{
                      readOnly: true, renderSideBySide: true,
                      renderSideBySideInlineBreakpoint: 700, automaticLayout: true,
                      fontSize: 13, minimap: { enabled: false }, ignoreTrimWhitespace: false,
                    }}
                  />
                </Suspense>}
      </div>
    </div>
  )
}

/** Before/after for a binary kind. `/versions/{n}` hands back a raw-URL reference
 *  rather than bytes, and each version's raw URL is immutable-cached, so these are
 *  plain <img> tags pointed straight at the two versions. */
function ImagePair({ art, left, right }: { art: Artifact; left: number; right: number }) {
  return (
    <div className="grid h-full grid-cols-1 gap-m overflow-auto p-m sm:grid-cols-2">
      {[left, right].map((v, i) => (
        <figure key={v} className="flex min-w-0 flex-col gap-1.5">
          <figcaption className="text-on-surface-low text-[0.75rem]">
            {i === 0 ? 'Before' : 'After'} · v{v}
          </figcaption>
          <img
            src={`/api/artifacts/${encodeURIComponent(art.slug)}/raw?version=${v}`}
            alt={`${art.name}, version ${v}`}
            className="min-h-0 w-full rounded-md border border-outline/40 object-contain"
            style={{ background: 'var(--color-surface-high)' }} />
        </figure>
      ))}
    </div>
  )
}

/** Version picker built on the shared `Segmented` primitive rather than a bespoke
 *  dropdown: versions are a small ordered set, so a strip shows the whole timeline at
 *  a glance instead of hiding it behind one. `collapse="menu"` keeps that honest on a
 *  long history — past the fit threshold it becomes one pill that opens the full list,
 *  so a 40-version artifact doesn't overflow the toolbar.
 *
 *  (Prose here deliberately avoids naming the raw element in angle brackets: the
 *  primitive-adoption scanner is a regex over source TEXT and counts matches inside
 *  comments, so a comment explaining the avoidance would itself trip the ratchet.) */
function VersionPicker({ label, value, options, onChange }: {
  label: string; value: number; options: number[]; onChange: (v: number) => void
}) {
  const segOptions = useMemo(
    () => options.slice().reverse().map((v) => ({ key: String(v), label: `v${v}` })),
    [options],
  )
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="shrink-0 text-on-surface-low text-[0.75rem]">{label}</span>
      <Segmented options={segOptions} value={String(value)} size="sm" collapse="menu"
        ariaLabel={`${label} version`} onChange={(k) => onChange(Number(k))} />
    </span>
  )
}

/** Monaco language for an artifact kind. Deliberately small and local: artifact
 *  kinds are a closed set that has nothing to do with filenames, so reusing
 *  `monacoLang` (which parses extensions) would mean inventing a fake filename. */
function diffLanguage(kind: string): string {
  switch (kind) {
    case 'widget':
    case 'html':
    case 'infographic':
    case 'document':
      return 'html'
    case 'react':
      return 'typescript'
    case 'svg':
      return 'xml'
    case 'json':
      return 'json'
    case 'markdown':
      return 'markdown'
    case 'csv':
      return 'plaintext'
    default:
      return 'plaintext'
  }
}
