import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A toggle that silently does nothing is its own defect ─────────────────────────────────────────
//
// 🔑 THIS FILE ALREADY NAMED THE BUG. `removeServer` unwraps the backend's message and reports it, with
// the comment: "Surface the backend's message instead of silently 'refreshing' (the bug), so the user
// knows to uninstall the app." The four toggles ten lines below were doing precisely that bug:
//
//     await api.toggleMcpServer(s.name, !s.enabled).catch(() => {})
//     setTimeout(load, 400)
//
// 🔑 AND THE FAILURE HERE IS NOT THE OPTIMISTIC LIE THE OTHER CONTRACTS FIX. These switches are
// DATA-DRIVEN — `<Toggle on={!!g.server.enabled} />` reads the refetched list, there is no local flip. So
// a failed write leaves no lying control; it leaves NOTHING. The user clicks, the switch does not move, no
// message appears, and the only reasonable guess is to click again. "An action that silently did not
// happen" is a distinct defect from "a control showing a value the server refused", and it needs its own
// rail because the optimistic-write sweeps cannot see it.
//
// Four sites, one file, one reporter — lifted from `removeServer`'s own pattern rather than invented:
// unwrap a JSON error body, `notify(…, 'error')`, and (new) return the outcome so the caller can skip the
// refetch when the write never landed.
//
// 🪤 `reconnectServer` IS DELIBERATELY LEFT SWALLOWING. Its `catch { /* status surfaces on reload */ }`
// carries its own reason and it is true: the server's status is re-rendered from the reload, so the
// outcome is visible without a toast. Converging it would add noise to a path that already reports
// through the UI. Recorded so the next pass does not "finish the job".

const SRC = readFileSync(join(process.cwd(), 'src/pages/tools/ToolsPage.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const TOGGLE_WRITES = ['toggleMcpServer', 'toggleMcpTool', 'toggleTool', 'toggleToolProvider']

describe('a tool toggle that fails tells the user', () => {
  it('the reporter is the SHARED one, and this file keeps no copy of it', () => {
    // Re-pointed, not relaxed. The helper began here and moved to `app/reportingWrite` when
    // `knowledge/KnowledgeListPage` became its second adopter — one implementation rather than two
    // copies of nine lines. So the assertions move from "it is defined here" to "it is imported
    // here, and no local definition shadows it".
    expect(CODE, 'the shared contract is imported').toMatch(
      /import \{ reportingWrite \} from '\.\.\/\.\.\/app\/reportingWrite'/,
    )
    const localDefs = [...CODE.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)]
    expect(localDefs.length, 'a page-local copy would shadow the shared one silently').toBe(0)

    // The contract itself, asserted where it now lives.
    // 🪤 Strip comments first: this file's own doc comment NAMES `JSON.parse(msg)` to explain why it
    // is absent, and the assertion below would match the explanation. Same trap as the design
    // ratchets that counted markup inside comments.
    const shared = readFileSync(join(process.cwd(), 'src/app/reportingWrite.ts'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    expect(shared).toMatch(
      /export async function reportingWrite\(what: string, run: \(\) => Promise<unknown>\): Promise<boolean>/,
    )
    expect(shared, 'it reports through the app toast').toMatch(/notify\(`Couldn't \$\{what\}: /)
    expect(shared, 'and returns the outcome so a caller can skip its refetch').toMatch(/return true/)
    // 🪤 The JSON-unwrap this used to assert is GONE ON PURPOSE, not dropped: `lib/errText` is the
    // app's single funnel for failure text and `api.ts` throws `ApiError(await errText(r))`, so
    // `e.message` is already the backend's sentence. Re-parsing it could never fire.
    expect(shared, 'no dead JSON unwrap').not.toMatch(/JSON\.parse\(msg\)/)
  })

  it('no toggle write swallows its rejection', () => {
    const offenders: string[] = []
    for (const call of TOGGLE_WRITES) {
      for (const m of CODE.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = CODE.slice(m.index!, m.index! + 200)
        if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain)) offenders.push(call)
      }
    }
    expect(offenders, 'a silently dead toggle is the bug this file already named').toEqual([])
  })

  it('all four go through the one reporter', () => {
    let routed = 0
    for (const m of CODE.matchAll(/reportingWrite\([\s\S]{0,140}?api\.(\w+)\(/g)) {
      expect(TOGGLE_WRITES, `unexpected call routed: ${m[1]}`).toContain(m[1])
      routed++
    }
    expect(routed, 'toggle writes routed through reportingWrite').toBe(4)
  })

  it('a failed write SKIPS the refetch — the point of returning the outcome', () => {
    // Refetching after a failure re-renders the same state and reads as "nothing happened twice".
    // Every caller must gate its `load` on the result.
    const gated = [...CODE.matchAll(/if \(ok\) setTimeout\(load, \d+\)/g)]
    expect(gated.length, 'callers gating the refetch on success').toBe(3)
    // …and none of them refetch unconditionally right after a reportingWrite.
    expect(CODE).not.toMatch(/await reportingWrite\([\s\S]{0,160}?\)\s*\n\s*setTimeout\(load/)
  })

  it('each report names WHICH toggle failed, with its subject', () => {
    // "Couldn't disable" alone is useless on a page with dozens of switches.
    expect(CODE).toMatch(/\$\{s\.enabled \? 'disable' : 'enable'\} "\$\{s\.name\}"/)
    expect(CODE).toMatch(/\$\{enabled \? 'enable' : 'disable'\} "\$\{t\.name\}"/)
    expect(CODE).toMatch(/\$\{g\.providerDisabled \? 'enable' : 'disable'\} "\$\{g\.key\}"/)
  })

  it('the switches are still DATA-DRIVEN — the premise of the whole finding', () => {
    // If a local optimistic flip is ever added, the failure mode changes from "nothing happened" to "the
    // control lies", and the reasoning in the header needs rewriting rather than silently passing.
    expect(SRC).toMatch(/<Toggle on=\{!!g\.server\.enabled\} \/>/)
  })

  it('reconnectServer keeps its documented swallow — the deliberate non-fix', () => {
    // Its status is re-rendered from the reload, so a toast would be noise. Left alone on purpose.
    expect(SRC).toMatch(/await api\.reconnectMcp\(s\.name\) \} catch \{ \/\* status surfaces on reload \*\/ \}/)
  })

  it('removeServer’s original reporting is untouched — this converged ONTO it', () => {
    expect(CODE).toMatch(/await api\.removeMcpServer\(s\.name\)/)
    expect(CODE).toMatch(/notify\(msg, 'error'\)/)
  })
})
