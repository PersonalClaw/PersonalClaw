import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed create must be seen AND heard ───────────────────────────────────────
//
// Every full-page create flow puts its Create button in a STICKY FOOTER and renders its submit error at
// the bottom of the SCROLLING BODY. Measured on `#/tasks/new` at 1440x900 with `POST /api/tasks` at 500:
//
//   before   button y=848 · message y=1744 (844px BELOW the fold) · role null · live regions []
//   after    button y=848 · message y=816  (in viewport)          · role alert · announced
//
// So clicking Create produced no observable effect: the button un-busied, the reason was off-screen, and
// nothing was announced. The fix is two lines per page — `role="alert"` (which `KnowledgeCreatePage`
// already had, the one sibling of five that got it right) plus a ref that scrolls the message into view
// with `scrollIntoView({ block: 'nearest' })`, the idiom this app already uses in 13 places.
//
// Left alone deliberately: the message is still the RAW response body (`{"detail":"create failed"}`).
// That is the `api`-layer/`LoadError` concern already recorded in the ledger, shared by every adopter —
// trimming it here would fix one surface of many.
//
// Also left alone: converging these onto `ui/InlineError`, whose own doc calls itself "the single shape
// behind the several `{err && <div role="alert" …>}`". That is the declared canonical primitive, but it
// brings a bordered/tinted box — a visible change to four pages' error presentation, i.e. an owner call
// rather than a defect fix.

const PAGES = join(process.cwd(), 'src', 'pages')

/** Every full-page create flow, and the element that carries its submit error. */
const CREATE_PAGES = [
  join('tasks', 'TaskCreatePage.tsx'),
  join('triggers', 'TriggerCreatePage.tsx'),
  join('agents', 'AgentCreatePage.tsx'),
  join('prompts', 'PromptCreatePage.tsx'),
  join('knowledge', 'KnowledgeCreatePage.tsx'),
]

describe('a create form announces its submit failure', () => {
  it.each(CREATE_PAGES)('%s marks the error as an alert', (rel) => {
    const src = readFileSync(join(PAGES, rel), 'utf8')
    expect(src, `${rel} must render a submit error at all`).toMatch(/\{err && </)
    const at = src.indexOf('{err && <')
    const tag = src.slice(at, at + 200)
    expect(tag, `${rel}: an unrequested failure must be announced`).toMatch(/role="alert"/)
  })

  it.each(CREATE_PAGES.slice(0, 4))('%s scrolls the error into view', (rel) => {
    // The four pages whose error sits below a sticky footer. `KnowledgeCreatePage` renders its message
    // inside a `shrink-0` flex column rather than a scrolling body, so it is not part of this half.
    const src = readFileSync(join(PAGES, rel), 'utf8')
    expect(src, `${rel}: the message must be reachable without hunting for it`).toMatch(
      /errRef\.current\?\.scrollIntoView\(\{ block: 'nearest' \}\)/,
    )
    expect(src, `${rel}: and the ref must be attached to the message`).toMatch(/ref=\{errRef\}/)
  })

  it('the scroll fires on the error appearing, not on every render', () => {
    for (const rel of CREATE_PAGES.slice(0, 4)) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      expect(src, `${rel}: guard on err and depend on it`).toMatch(/useEffect\(\(\) => \{ if \(err\) errRef/)
      expect(src, `${rel}: the effect must depend on err`).toMatch(/\}, \[err\]\)/)
    }
  })
})
