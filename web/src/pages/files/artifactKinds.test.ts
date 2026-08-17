import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ARTIFACT_KINDS, UNKNOWN_ARTIFACT_KIND, artifactKindMeta } from './fileMeta'

/** The backend's ALLOWED_KINDS, read from the Python source rather than duplicated here.
 *
 *  Duplicating the list is what let it drift in the first place: `csv`, `docx`, `xlsx`,
 *  `pptx`, `pdf` and `video` were all accepted by the backend and absent from the frontend
 *  table, and because `artifactKindMeta` falls back to its FIRST entry, every generated
 *  document rendered as a "Widget" — silently, with no error anywhere. */
function backendAllowedKinds(): string[] {
  const src = readFileSync(
    join(__dirname, '../../../../src/personalclaw/artifacts/models.py'),
    'utf8'
  )
  const block = src.match(/ALLOWED_KINDS\s*=\s*\{([\s\S]*?)\}/)
  if (!block) throw new Error('could not find ALLOWED_KINDS in artifacts/models.py')
  return [...block[1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1])
}

describe('artifact kinds', () => {
  it('covers every kind the backend accepts', () => {
    const declared = new Set(ARTIFACT_KINDS.map((k) => k.key))
    const missing = backendAllowedKinds().filter((k) => !declared.has(k as never))
    expect(missing, `kinds the backend accepts but the UI cannot label: ${missing.join(', ')}`)
      .toEqual([])
  })

  it('declares no kind the backend would reject', () => {
    const allowed = new Set(backendAllowedKinds())
    const extra = ARTIFACT_KINDS.map((k) => k.key).filter((k) => !allowed.has(k))
    expect(extra, `kinds the UI offers that the backend rejects: ${extra.join(', ')}`).toEqual([])
  })

  it('gives every kind its own label', () => {
    const labels = ARTIFACT_KINDS.map((k) => k.label)
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('resolves a real kind to itself rather than the fallback', () => {
    // The specific regression: a document kind must not resolve to Widget.
    for (const kind of ['csv', 'docx', 'xlsx', 'pptx', 'pdf', 'video']) {
      expect(artifactKindMeta(kind).key, `${kind} fell through to the fallback`).toBe(kind)
    }
  })

  it('says "unknown" for a genuinely unknown kind instead of impersonating one', () => {
    // The two rails above keep the closed set closed at TEST time. This is what the
    // runtime does if it ever drifts anyway — and the point is that it must not answer
    // with a REAL kind. Returning `ARTIFACT_KINDS[0]` is what made the original bug
    // silent: a docx confidently labelled "Widget" is indistinguishable from a correct
    // answer, so nothing anywhere reported a problem for four releases.
    const meta = artifactKindMeta('not-a-real-kind')
    expect(meta).toBe(UNKNOWN_ARTIFACT_KIND)
    expect(meta.key, 'an unknown kind must match no registered kind').toBe('')
    expect(ARTIFACT_KINDS.map((k) => k.key)).not.toContain(meta.key)
    expect(meta.label).not.toBe(ARTIFACT_KINDS[0].label)
  })

  it('offers the unknown fallback nowhere a user can pick it', () => {
    // The library toolbar's kind filter maps ARTIFACT_KINDS directly, so a fallback
    // entry inside that table would become a selectable filter matching nothing.
    expect(ARTIFACT_KINDS).not.toContain(UNKNOWN_ARTIFACT_KIND)
  })
})
