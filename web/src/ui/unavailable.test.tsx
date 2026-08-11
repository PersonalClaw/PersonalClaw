import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { unavailableWhen } from './unavailable'

// ── The raw-<button> half of "an unavailable control says why" ────────────────────────
//
// `Button` got a `disabledReason` prop, which reached 29 of 37 validity-gated submits. The last
// 10 were RAW `<button>`s — icon-only sends in chat / the activity panel / task comments /
// onboarding, and the Add-row submits in the security, voice, projection-rules and Ollama
// panels. A raw element cannot inherit a prop, and hand-rolling `aria-disabled` + a click guard
// + a title ten times is how three of them end up subtly different.
//
// `unavailableWhen()` is the same logic `Button` already runs, extracted so both paths agree.
// Verified live on the security panel's Add button:
//
//   empty  { ariaDisabled: "true", nativeDisabled: false, title: "Enter a host first",
//            focusable: TRUE, opacity: "0.4", cursor: "not-allowed" }
//   typed  { ariaDisabled: null, title: null, opacity: "1", cursor: "default" }
//   click while unavailable → 112 code rows → 112 (refused) · axe: 0 blocking
//
// 🔑 WHY `aria-disabled` AND NOT `disabled`. A native disabled button is removed from the tab
// order, so a keyboard user cannot reach it to hear anything — they tab past the action with no
// way to learn what is missing. The native attribute is stronger protection, but a submit that
// cannot explain itself is the worse failure.
//
// 🪤 THE CLICK GUARD MUST BE ON THE CAPTURE PHASE. `aria-disabled` is advisory to the browser,
// so the handler has to refuse. On the bubble phase the button's own `onClick` has ALREADY run —
// `onClickCapture` is what makes the refusal actually refuse.
//
// 🪤 `busy` KEEPS THE NATIVE ATTRIBUTE. An in-flight action must not be re-clickable, and its
// spinner + `aria-busy` already carry that state. The reason a control is unavailable *while
// working* is self-evident; the reason it is unavailable *because a field is empty* is not.

describe('unavailableWhen', () => {
  it('makes the control reachable-but-unavailable, with the reason on title', () => {
    const props = unavailableWhen(true, 'Enter a host first')
    expect(props['aria-disabled']).toBe(true)
    expect(props.disabled, 'the native attribute would remove the tab stop').toBeUndefined()
    expect(props.title).toBe('Enter a host first')
  })

  it('returns nothing when the input is present', () => {
    expect(unavailableWhen(false, 'Enter a host first')).toEqual({})
  })

  it('preserves an existing title, appending the reason', () => {
    const props = unavailableWhen(true, 'Enter a host first', { title: 'Add to the denylist' })
    expect(props.title).toBe('Add to the denylist — Enter a host first')
  })

  it('keeps a plain title when nothing is missing', () => {
    expect(unavailableWhen(false, 'x', { title: 'Add' })).toEqual({ title: 'Add' })
  })

  it('goes NATIVELY disabled while busy, and says nothing extra', () => {
    // An in-flight action must not be re-clickable; aria-busy already announces the state.
    const props = unavailableWhen(true, 'Enter a host first', { busy: true })
    expect(props.disabled).toBe(true)
    expect(props['aria-disabled']).toBeUndefined()
    expect(props.title, 'the busy reason is self-evident from the spinner').toBeUndefined()
  })

  it('busy wins even when nothing is missing', () => {
    expect(unavailableWhen(false, 'x', { busy: true }).disabled).toBe(true)
  })

  it('refuses the click on the CAPTURE phase', () => {
    // On the bubble phase the button's own onClick has already run.
    const onClick = vi.fn()
    render(
      <button type="button" onClick={onClick} {...unavailableWhen(true, 'Enter a host first')}>
        Add
      </button>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('lets the click through once the input is present', () => {
    const onClick = vi.fn()
    render(
      <button type="button" onClick={onClick} {...unavailableWhen(false, 'x')}>
        Add
      </button>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('keeps the control findable by its own name', () => {
    // The reason rides `title`, never the label — the same regression that killed the first
    // `disabledReason` implementation (an sr-only span concatenated into the accessible name).
    render(<button type="button" {...unavailableWhen(true, 'Enter a host first')}>Add</button>)
    expect(screen.getByRole('button', { name: 'Add' })).toBeTruthy()
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────
const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const ADOPTERS = [
  'app/Onboarding.tsx',
  'ui/PlanningWalkthrough.tsx',
  'pages/ChatPage.tsx',
  'pages/chat/ChatActivityPanel.tsx',
  'pages/tasks/TaskDetail.tsx',
  'pages/settings/SecurityPanel.tsx',
  'pages/settings/VoicePanel.tsx',
  'pages/settings/ProjectionRulesPanel.tsx',
  'pages/settings/OllamaModelManager.tsx',
]

describe('every converted raw submit uses the helper', () => {
  for (const rel of ADOPTERS) {
    it(`${rel} spreads unavailableWhen on its gated submit`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must spread the helper').toMatch(/\{\.\.\.unavailableWhen\(/)
      expect(src, 'must import it').toMatch(/import \{ unavailableWhen \}/)
    })
  }

  it('every converted button dims at ITS OWN level, not a blanket one', () => {
    // The old `disabled:opacity-*` no longer fires — nothing sets the native attribute — so each
    // converted button needs the aria-disabled variant, or it looks fully enabled while refusing
    // clicks. Verified live: opacity 0.4, cursor not-allowed.
    //
    // 🪤 THE LEVEL HAS TO MATCH THE BUTTON'S OWN. The tree uses BOTH `opacity-40` and
    // `opacity-50`, and a blanket `aria-disabled:opacity-40` silently dimmed the security panel's
    // Add button harder than before (0.5 → 0.4). A capture pair caught it; a source grep could
    // not. Whether 40-vs-50 should be unified at all is a separate family — this rail's job is
    // only that the conversion is appearance-preserving.
    const mismatched: string[] = []
    const missing: string[] = []
    for (const rel of ADOPTERS) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      for (const m of src.matchAll(/unavailableWhen\(/g)) {
        const start = src.lastIndexOf('<', m.index!)
        let depth = 0
        let end = -1
        for (let i = start; i < src.length; i++) {
          const ch = src[i]
          if (ch === '{') depth++
          else if (ch === '}') depth--
          else if (ch === '>' && depth === 0) {
            end = i
            break
          }
        }
        const tag = src.slice(start, end + 1)
        const at = `${rel}:${src.slice(0, start).split('\n').length}`
        const aria = /aria-disabled:opacity-(\d+)/.exec(tag)
        const own = /(?<!aria-)disabled:opacity-(\d+)/.exec(tag)
        if (!aria) missing.push(at)
        else if (own && own[1] !== aria[1]) mismatched.push(`${at} (${own[1]} → ${aria[1]})`)
      }
    }
    expect(
      missing,
      'a converted button with no aria-disabled dimming looks enabled while refusing clicks:\n  ' +
        missing.join('\n  '),
    ).toEqual([])
    expect(
      mismatched,
      'the aria-disabled dim level must equal the button\'s own disabled level, or the conversion ' +
        'changes how it looks:\n  ' + mismatched.join('\n  '),
    ).toEqual([])
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})
