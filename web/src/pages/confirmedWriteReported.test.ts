import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A write the user CONFIRMED must tell them when it fails ──────────────────────────────────
//
// `pages/userActionReported.test.ts` closed the swallowed-write family down to singletons on the
// question **"did a person ask for this?"** — a poll and a side effect of navigation did not, so
// they stay silent. This is that question at its sharpest: the app STOPPED and asked, the user
// said yes, and then nothing came back.
//
// 🪤 WHY THIS IS DERIVED AND THAT FILE IS ENUMERATED. That rail hand-lists seven fixed sites, and
// ten more had the same defect — it could not see them, because a list cannot contain a member
// nobody put in it. Four were `confirmDelete` deletes whose failure path was `catch { /* ignore */ }`
// or `catch { setBusy(false) }` (a spinner reset is not a message: putting the row's opacity back is
// exactly what the screen looks like when the click never landed), and five were bare
// `try { … } finally { … }` with **no catch at all**, where the rejection went unhandled. Two
// mechanisms, one user outcome. So this rail derives its population from source instead:
//
//   population = the innermost function containing BOTH an `await confirm(`/`confirmDelete(` gate
//                AND a call to an `api.*` method that `lib/api.ts` implements with post/put/patch/del.
//
// The write set is read out of `lib/api.ts` rather than listed, because a name like `verifySkill`
// or `sideTurn` gives no clue that it POSTs, and the earlier hand-rolled census missed every write
// whose wrapper was not literally named `delete*` — it found `addTaskComment` only by accident.
//
// ── It polices the MECHANISM, not the wording, and that is what keeps it honest ──
//
// Three shapes look silent to a regex and are not. Each is credited explicitly, because a rail with
// false positives gets weakened, and a weakened rail is worse than none:
//
//   · DELEGATION — `settings/SecurityPanel`'s `move()` passes the write as a thunk
//     (`run('Moved', () => api.migrateCredentialsToKeychain())`) and the `catch` lives in `run`.
//     The write's text is inside `move()`; the handling is not. An arrow thunk means someone else
//     runs it, so it is credited.
//   · `.catch()` — `dashboard/widgets/SystemHealth`'s `runUpdate()` reports by dispatching
//     `ne:toast`, deliberately, because the shell's overlay owns progress and a rejected START
//     pushes no events. Attached handling is handling.
//   · A `catch` that reports through a LOCAL state name — `setErr`, `setMsg`, `setNote`,
//     `setActionErr`, `setTestOut`, `setResult({ ok: false })`. There is no single house helper, so
//     the vocabulary is matched rather than a single symbol.
//
// What is left after those three credits is the real defect: the app asked, the user said yes, and
// the failure reached them as nothing at all.

const SRC = join(process.cwd(), 'src')

/** Every `.ts`/`.tsx` under `src/`, excluding tests. */
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

/** The `api.*` methods `lib/api.ts` implements with a non-GET verb.
 *
 *  Shape there is uniform: `name: (args) => post<T>(url, body)`. Reading the VERB is the only way
 *  to know a write from a read — `verifySkill` POSTs, `sideTurn` POSTs, `grillTree` POSTs. */
function writeMethods(): string[] {
  const src = readFileSync(join(SRC, 'lib/api.ts'), 'utf8')
  const decl =
    /\n {2}([a-zA-Z_][\w]*)\s*:\s*(?:async\s*)?\([^\n]*?\)\s*(?::[^=\n]*?)?=>\s*\n?\s*(get|post|put|patch|del)\b/g
  const out = new Set<string>()
  for (const m of src.matchAll(decl)) if (m[2] !== 'get') out.add(m[1])
  return [...out]
}

/** `[start, end, name]` for every function-ish body in the file, brace-matched. */
function functionSpans(s: string): [number, number, string][] {
  const head =
    /(?:async\s+function|function)\s+([A-Za-z_][\w]*)\s*\(|(?:const|let)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]*?)?=>\s*\{/g
  const out: [number, number, string][] = []
  for (const m of s.matchAll(head)) {
    const from = s[m.index! + m[0].length - 1] === '{' ? m.index! + m[0].length - 1 : m.index! + m[0].length
    const open = s.indexOf('{', from)
    if (open < 0) continue
    let depth = 0
    for (let j = open; j < s.length; j++) {
      if (s[j] === '{') depth++
      else if (s[j] === '}') {
        depth--
        if (depth === 0) {
          out.push([open, j + 1, m[1] ?? m[2]])
          break
        }
      }
    }
  }
  return out
}

const CONFIRM = /\bawait\s+confirm(?:Delete)?\(/
/** The three credited handling shapes, plus the app's own reporting vocabulary. */
const REPORTS =
  /reportingWrite\(|reportActionFailure\(|\.catch\(|notify\(|setToast|ne:toast|throw\b|set[A-Za-z]*(?:Err|Error|Msg|Message|Note|Detail|Result|Out|Failure)\b/

type Finding = { file: string; line: number; fn: string; method: string }

function unhandled(): { population: number; findings: Finding[] } {
  const writes = writeMethods()
  // 🪤 TWO regexes from one source, and NOT one shared `/g`. `RegExp.test()` on a global regex
  // ADVANCES `lastIndex`, so a `WRITE.test(s)` pre-filter followed by `s.matchAll(WRITE)` starts
  // the scan partway through the file — that silently cut the population from 44 to 24 and would
  // have shipped a rail that passed by not looking.
  const pattern = `\\bapi\\.(${writes.sort((a, b) => b.length - a.length).join('|')})\\b`
  const HAS_WRITE = new RegExp(pattern)
  let population = 0
  const findings: Finding[] = []
  const seen = new Set<string>()
  for (const abs of sources(SRC)) {
    const rel = abs.slice(SRC.length + 1).replace(/\\/g, '/')
    if (rel === 'lib/api.ts') continue
    const s = readFileSync(abs, 'utf8')
    if (!CONFIRM.test(s) || !HAS_WRITE.test(s)) continue
    const spans = functionSpans(s)
    for (const m of s.matchAll(new RegExp(pattern, 'g'))) {
      const enclosing = spans.filter(([a, b]) => a <= m.index! && m.index! < b)
      if (!enclosing.length) continue
      const [a, b, fn] = enclosing.reduce((best, x) => (x[1] - x[0] < best[1] - best[0] ? x : best))
      const body = s.slice(a, b)
      if (!CONFIRM.test(body)) continue
      const key = `${rel}:${fn}:${m[1]}`
      if (seen.has(key)) continue
      seen.add(key)
      population++
      // Delegation: the write is handed to someone else as a thunk, so the handling is theirs.
      const delegated = new RegExp(`=>\\s*api\\.${m[1]}\\b`).test(body)
      if (delegated || REPORTS.test(body)) continue
      findings.push({ file: rel, line: s.slice(0, a).split('\n').length, fn, method: m[1] })
    }
  }
  return { population, findings }
}

describe('a write the user confirmed reports its failure', () => {
  const { population, findings } = unhandled()

  it('found the population (a scan that matches nothing passes every check it makes)', () => {
    // Both halves must parse. If `lib/api.ts`'s shape changes, `writeMethods()` empties and every
    // confirm-gated write silently leaves the population — the exact failure this floor exists for.
    expect(writeMethods().length, 'no write methods parsed from lib/api.ts').toBeGreaterThan(200)
    expect(
      population,
      'no confirm-gated writes found — the scan broke, it did not find a clean tree',
    ).toBeGreaterThan(30)
  })

  it('every one of them handles the failure', () => {
    expect(
      findings.map((f) => `${f.file}:${f.line} ${f.fn}() → api.${f.method}`),
      'These functions stop and ASK the user, then drop the failure on the floor — the dialog is ' +
        'consent for an action that may never have happened. Satisfy this in one of four ways:\n' +
        '  · wrap it: `if (!(await reportingWrite("delete the thing", () => api.x()))) return`\n' +
        '  · attach `.catch(reportActionFailure("…"))` when the caller needs the result\n' +
        '  · report in the catch through this surface\'s own error state\n' +
        '  · hand the write to a helper as a thunk, and report there\n' +
        'A `finally { setBusy(false) }` with no catch is NOT one of them: the rejection goes ' +
        'unhandled and the spinner stopping is what success looks like too.',
    ).toEqual([])
  })
})
