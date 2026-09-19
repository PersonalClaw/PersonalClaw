import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── No write freezes a record field at a literal ──────────────────────────────────────────────────
//
// #542's root cause, stated as a rule. The knowledge-intent editor's save read:
//
//     api.upsertKnowledgeIntent({ id: intent.id || undefined, goal: g, enabled: true, ... })
//                                                             ^^^^^^^^^^^^^^
//
// `enabled` is a real field of the `Intent` record — the backend's `applies_to()` ANDs on it and
// BOTH fan-out paths filter through that — but the only writer of an intent in the entire product
// spelled it as a constant. Three visible symptoms, one cause: the list's `Paused` badge could
// never render, an intent could never be paused, and `propose_skill` was frozen at creation.
//
// MEASURED, and this is why the rail is worth having: pausing is a COST lever. Against a live
// gateway with five items, `POST /api/knowledge/intents/<id>/run` answered `{evaluated: 5}` when
// active and `{evaluated: 0}` when paused — one model call per item, and Delete (which destroys
// everything the intent gathered) was the only way to stop it.
//
// 🔑 THE RATCHET: a NEW bare `field: true|false` inside an `api.<write>*()` body fails this test.
// The exemptions below are not a bypass — each names why it is a per-call OPERATION flag rather
// than a field of the stored record, and the second test asserts each still has that shape.
//
// 🪤 SCOPED TO THE WRITE SEAM DELIBERATELY, and calibrated to under-report the way
// `harness/scanner.py` and `ui/disabledReasonCensus.test.ts` are. A draft that also swept anything
// matching `set[A-Z]` caught **36** sites, of which 32 were React state setters
// (`setSubagents({done: true})`, `setQuery({replace: true})`). A rail that reports a `useState`
// call as a frozen record field is one nobody reads, so the net is `api.<write>*()` plus
// `write<Record>()` helpers — five sites app-wide, four of them classified below.
//
// A per-record round-trip test is still the belt to this braces: `pages/knowledge/
// intentIsEditable.test.tsx` saves an untouched intent and asserts every field comes back, which
// catches a frozen field regardless of the shape of the call site.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })

/** The write seams: an `api.` method that WRITES (a read cannot freeze anything), plus any
 *  `write<Record>()` helper — the naming this codebase uses for a single-writer funnel.
 *
 *  🔴 THE SECOND HALF IS NOT DECORATION — it is the hole this rail fell into once. #542's fix
 *  routed both intent writers through one `writeIntent()`, which moved the object literal OFF
 *  `api.upsertKnowledgeIntent(...)` and behind the helper. MEASURED with the mutation harness:
 *  re-freezing `enabled: true` in the editor's save then produced **0 reds** here — the rail was
 *  vacuous for the exact call site it was written for. Adding `write[A-Z]` costs exactly one
 *  further exemption app-wide, so it is cheap; leaving it out costs the whole rail. */
const WRITE_CALL =
  /(?:api\.(?:upsert|update|create|save|patch|put|set)[A-Z][A-Za-z0-9_]*|\bwrite[A-Z][A-Za-z0-9_]*)\s*\(/g
/** A property whose value is a bare boolean literal, inside an object literal. */
const FROZEN_PROP = /[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(true|false)\b/g

/** The full argument list of the call starting at `i`, balanced on parens. */
function callArgs(src: string, i: number, openParen: number): string {
  let depth = 0
  for (let j = openParen; j < src.length; j++) {
    const c = src[j]
    if (c === '(') depth++
    else if (c === ')') {
      depth--
      if (!depth) return src.slice(i, j + 1)
    }
  }
  return ''
}

function census(): string[] {
  const found: string[] = []
  for (const file of walk(SRC)) {
    // `lib/api.ts` is the client itself: its literals are defaults of the transport, not a
    // caller freezing a record.
    if (file.endsWith(join('lib', 'api.ts'))) continue
    const src = readFileSync(file, 'utf8')
    let m: RegExpExecArray | null
    WRITE_CALL.lastIndex = 0
    while ((m = WRITE_CALL.exec(src))) {
      const args = callArgs(src, m.index, m.index + m[0].length - 1)
      if (!args) continue
      let p: RegExpExecArray | null
      FROZEN_PROP.lastIndex = 0
      while ((p = FROZEN_PROP.exec(args))) {
        found.push(`${file.slice(SRC.length + 1)}  ${m[0].replace(/\s*\($/, '')}  ${p[1]}: ${p[2]}`)
      }
    }
  }
  return found.sort()
}

/** Each remaining frozen literal, with the reason it is an operation flag and not a record field.
 *  Keyed by `<relpath>  <api call>  <prop>: <value>` — the census line verbatim. */
const OPERATION_FLAGS: Record<string, string> = {
  'pages/artifacts/ArtifactViewer.tsx  api.updateArtifact  snapshot: false':
    'Whether THIS save cuts a new version. Paired with `event_type: "edited"`; the artifact has no `snapshot` column.',
  'pages/artifacts/ArtifactViewer.tsx  api.updateArtifact  snapshot: true':
    'The Snapshot action, whose entire meaning is "version this save". Paired with `event_type: "iterated"`.',
  'pages/projects/ProjectsSection.tsx  api.createProject  name_locked: true':
    'A user who TYPED the name is the lock — it stops the LLM auto-renaming. Not frozen: the rename path re-writes it via `patch({name, name_locked: true})`.',
  'ui/widget/WidgetFrame.tsx  api.updateArtifact  snapshot: true':
    'Same per-save versioning flag as the viewer: a widget save always snapshots.',
  'pages/ChatPage.tsx  writeCachedDetail  running: false':
    'A synthetic seed for a just-created session in the CLIENT query cache, not a stored record — and a fact about it (nothing is running yet). `writeCachedDetail` refuses running details outright.',
}

describe('no api write freezes a record field at a literal', () => {
  it('every frozen boolean in an api write body is a classified operation flag', () => {
    const unexplained = census().filter((line) => !(line in OPERATION_FLAGS))
    expect(
      unexplained,
      'A bare `field: true|false` in an api write body freezes that field for every caller of ' +
        'this surface — which is how #542 made a knowledge intent impossible to pause. Bind it to ' +
        'state and give the user a control, or add it to OPERATION_FLAGS with the reason it is a ' +
        'per-call operation flag rather than a field of the stored record.',
    ).toEqual([])
  })

  it('the census still finds the classified flags (the instrument works)', () => {
    // Without this, deleting the detector would make the test above pass vacuously.
    const lines = census()
    expect(lines.length, 'the detector found nothing at all — it has stopped working').toBeGreaterThan(0)
    for (const key of Object.keys(OPERATION_FLAGS)) {
      expect(lines, `${key} is classified but no longer present — drop the stale exemption`).toContain(key)
    }
  })

  it('each exemption is a per-call flag, not a stored field: none names a knowledge intent', () => {
    // The one record this rail was written for must never appear as an exemption — by either
    // the api name or the single-writer helper the fix routed both call sites through.
    for (const key of Object.keys(OPERATION_FLAGS)) {
      expect(key).not.toMatch(/upsertKnowledgeIntent|writeIntent/)
    }
  })

  it('the net actually reaches a write<Record>() helper, not only api.*', () => {
    // Guards the widening itself: without `write[A-Z]` in WRITE_CALL this rail went vacuous for
    // #542's own call site the moment the fix introduced `writeIntent`.
    expect(WRITE_CALL.source).toMatch(/write\[A-Z\]/)
    expect(census().some((l) => /\swrite[A-Z]/.test(l)), 'no write<Record>() site is being seen')
      .toBe(true)
  })
})
