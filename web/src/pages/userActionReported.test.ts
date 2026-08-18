import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The last of the swallowed-write census: a USER-INITIATED action that failed silently ───────────
//
// Eight contracts in, the family is down to singletons that share no page and no helper — only the
// property that a person clicked something and was told nothing. Seven fixed here; **two deliberately
// left alone**, which is the half of this cycle worth reading.
//
// FIXED — someone clicked, so a failure is theirs to know about:
//   LoopComposer      `fileUpload`          attached design files silently dropped; the design pass
//                                           then runs from the URL and prompt alone
//   ChatPage          `renameSession`       optimistic title; reverts on next load
//   ChatPage          `setSessionLifecycle` optimistic row move; `load()` snaps it back
//   ChatPage          `createChatFolder`    a name typed into a dialog, no folder appears
//   AgentsListPage    `setDefaultAgent`     the default silently stays where it was
//   ToolsPage         `probeMcp`            spinner runs, list reloads unchanged, probe never ran
//   PinnedTiles       `refreshTile` (button) "a human asked", per its own comment
//
// 🔑 NOT FIXED, and each for a stated reason — reporting these would be the defect:
//   PinnedTiles       `refreshTile` (tick)  a 60-SECOND POLL. Nobody asked; one toast per failed poll
//                                           is noise, and a stale tile still shows its last value with
//                                           its own timestamp.
//   ArtifactViewer    `viewRender`          its comment already rules on this: "a background refresh
//                                           must never block the open or SURFACE AN ERROR TOAST". It is
//                                           a `view`-trigger side effect of opening, not an action.
//
// The distinguishing question, and the reason this file exists rather than a blanket sweep: **did a
// person ask for this?** A poll and a side effect of navigation did not.
//
// 🪤 THREE DIFFERENT GATING DECISIONS here, all deliberate, all asserted below — because "gate the
// refetch" is NOT the universal remedy this family might look like from the outside:
//   gate it      `createChatFolder`, `setDefaultAgent`, `probeMcp` — the refetch only re-renders the
//                same state, so running it reads as "nothing happened, twice".
//   DON'T gate   `setSessionLifecycle` — the optimistic move already lied, so `load()` is the REPAIR.
//   no gate at all `fileUpload` — its comment's "shouldn't block launch" was right; only the silence
//                was wrong.

const F = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')
const strip = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** file → [the write, the phrase its message must contain] */
const FIXED: Array<[string, string, string]> = [
  ['pages/loop/LoopComposer.tsx', 'fileUpload', 'upload '],
  ['pages/ChatPage.tsx', 'renameSession', 'rename this chat'],
  ['pages/ChatPage.tsx', 'setSessionLifecycle', 'this chat'],
  ['pages/ChatPage.tsx', 'createChatFolder', 'create the folder'],
  ['pages/agents/AgentsListPage.tsx', 'setDefaultAgent', 'the default agent'],
  ['pages/tools/ToolsPage.tsx', 'probeMcp', 're-probe the MCP servers'],
  ['pages/dashboard/PinnedTiles.tsx', 'refreshTile', 'refresh this tile'],
]

describe('a user-initiated write that fails tells the user', () => {
  it('none of the seven swallows — the ratchet, keyed on the WRITES', () => {
    const offenders: string[] = []
    for (const [rel, call] of FIXED) {
      const scan = strip(F(rel)).replace(/=>/g, '⇒')
      const found = [...scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))]
      expect(found.length, `${rel}: api.${call} must still be called`).toBeGreaterThan(0)
      for (const m of found) {
        const chain = scan.slice(m.index!, m.index! + 240)
        // `refreshTile` legitimately has one swallowing occurrence (the poll), asserted separately.
        if (call === 'refreshTile' && !/force: true/.test(chain)) continue
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) offenders.push(`${rel}:${call}`)
      }
    }
    expect(offenders, 'a person clicked; silence is not an answer').toEqual([])
  })

  it('each reports through the shared contract and names its subject', () => {
    for (const [rel, call, phrase] of FIXED) {
      const src = strip(F(rel))
      expect(src, `${rel} must import the shared reporter`).toMatch(
        /import \{[^}]*report(ingWrite|ActionFailure)[^}]*\} from '[^']*app\/reportingWrite'/,
      )
      // 🪤 `indexOf` finds the FIRST occurrence, which for `refreshTile` is the 60s poll — the one
      // deliberately left swallowing. Target the occurrence this contract covers, not the first.
      const occurrences = [...src.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))].map((m) => m.index!)
      const at = call === 'refreshTile'
        ? occurrences.find((i) => /force: true/.test(src.slice(i, i + 120)))!
        : occurrences[0]
      expect(at, `${rel}: the covered api.${call} call must exist`).toBeGreaterThan(-1)
      const around = src.slice(Math.max(0, at - 260), at + 240)
      expect(around, `${rel}: api.${call} must be reported`).toMatch(/report(ingWrite|ActionFailure)\(/)
      expect(src, `${rel}: the message must name its subject (${phrase})`).toContain(phrase)
    }
  })

  it('THE POLL still swallows — reporting it would be the defect', () => {
    // A 60s timer nobody asked for. If this ever gains a report, the dashboard starts shouting on
    // every transient failure, and the tile's own timestamp already tells the honest story.
    const src = strip(F('pages/dashboard/PinnedTiles.tsx')).replace(/=>/g, '⇒')
    const at = src.indexOf('const tick = useCallback(')
    expect(at, 'the poll must exist').toBeGreaterThan(-1)
    const tick = src.slice(at, src.indexOf('useVisiblePoll', at))
    expect(tick, 'the poll must not report').toMatch(/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/)
    expect(tick, 'and it is the un-forced call').not.toContain('force: true')
  })

  it('the VIEW-RENDER side effect still swallows — its own comment rules on it', () => {
    const raw = F('pages/artifacts/ArtifactViewer.tsx')
    // 🪤 The ruling wraps across two comment lines ("… or surface\n  // an error toast"), so a
    // contiguous literal never matches. Normalise the comment whitespace first.
    const flat = raw.replace(/\n\s*\/\/\s*/g, ' ')
    expect(flat, 'the ruling must stay recorded next to the code').toMatch(
      /must never block the open or surface an error toast/,
    )
    const scan = strip(raw).replace(/=>/g, '⇒')
    const at = scan.indexOf('api.viewRender(')
    expect(at).toBeGreaterThan(-1)
    expect(scan.slice(at, at + 160)).toMatch(/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/)
  })

  it('the three GATING decisions are each what they should be', () => {
    const chat = strip(F('pages/ChatPage.tsx'))

    // gate it: the refetch would only re-render the same state
    const folder = chat.slice(chat.indexOf('async function createFolder()'), chat.indexOf('async function createFolder()') + 520)
    expect(folder, 'createChatFolder gates its reload').toMatch(/\)\)\) return/)
    const gate = folder.indexOf(')) return')
    expect(gate, 'and the guard precedes it').toBeLessThan(folder.indexOf('load()', gate))

    // DON'T gate: the optimistic move already lied, so the refetch is the repair
    const life = chat.slice(chat.indexOf('async function setLifecycle('), chat.indexOf('async function setNeverArchive('))
    expect(life, 'the optimistic move is still there').toContain('setSessions((prev)')
    expect(life, 'the repair refetch still runs').toContain('load()')
    expect(life, 'and must NOT be gated — that would leave the row lying').not.toMatch(/\)\)\) return/)

    // no gate at all: launch proceeds, per the composer's own documented decision
    const composer = strip(F('pages/loop/LoopComposer.tsx'))
    const up = composer.slice(composer.indexOf('api.fileUpload('), composer.indexOf('onCreated(loop.id'))
    expect(up, 'a failed upload must not block the launch').not.toMatch(/\breturn\b/)
  })

  it('the optimistic rename reports rather than reverting', () => {
    // Same ruling as `selectionPersistReported`: tell, do not fight input the user may still be editing.
    const chat = strip(F('pages/ChatPage.tsx'))
    const at = chat.indexOf('api.renameSession(')
    const around = chat.slice(Math.max(0, at - 200), at + 200)
    expect(around, 'the optimistic set stays').toContain('setTitle(v)')
    expect(around, 'and no revert was added').not.toMatch(/setTitle\(title\)|setTitle\(prev/)
  })
})
