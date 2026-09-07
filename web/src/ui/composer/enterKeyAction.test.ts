import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { enterKeyAction } from './enterKeyAction'

// ── A preference persisted, written by two controls, and read by nothing ────────────────────────
//
// `send_on_enter` shipped as a full-stack setting: a config field, a read, three API touch points, a
// TypeScript type, a Toggle in Chat settings and a Switch in the settings overview. The composer
// never asked for it. Measured before this change: the whole web tree held three references to the
// name — the type declaration and the two writers.
//
// So the toggle moved a stored boolean and nothing else, and the settings copy stated behaviour the
// app did not have. With it OFF the hint promised *"Enter inserts a newline · Cmd/Ctrl+Enter sends"*
// and BOTH halves were false: Enter sent, and the mod chord optimizes the prompt.
//
// 🔑 THE CAPABILITY ALREADY EXISTED, WIRED TO THE WRONG CONDITION. The keymap's Enter branch already
// inserted a newline and left sending to the button — gated on the viewport being phone-sized rather
// than on the reader's setting. So the fix is one condition, not a feature.
//
// 🪤 WHY THERE WAS NO GUARD TO CATCH THIS. The branch lived inside a CodeMirror keymap, which no
// test can drive without mounting the editor — so the decision was structurally untestable, and
// duly untested. Extracting `enterKeyAction` is what makes the behaviour below assertable at all;
// the sibling `sendButtonState` is the same split for the same reason.
//
// 🪤 AND A PURE FUNCTION CAN BE PERFECT WHILE STILL UNWIRED — which is the original defect exactly.
// The second half of this file pins the chain: a config read in the composer, the value reaching the
// keymap, and both writers busting the reader's cache key.

const SRC = join(process.cwd(), 'src')
// Comments are stripped before every match. Prose that quotes a token it is explaining otherwise
// counts as an occurrence of it, which makes a census read its own explanation as code.
const codeOf = (...p: string[]) =>
  readFileSync(join(SRC, ...p), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('Enter means what the reader asked it to mean', () => {
  it('sends by default — the shipped behaviour, unchanged', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: true })).toBe('send')
  })

  it('inserts a newline when the preference is off, even with a sendable draft', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: true, sendOnEnter: false })).toBe('newline')
  })

  it('🔑 keeps sending while the preference is UNKNOWN', () => {
    // The safety property. A config read in flight, or one that failed, leaves this undefined — and
    // silently taking Enter-to-send away from someone who never turned it off would be a worse bug
    // than the one being fixed. Only an explicit `false` may change the key.
    expect(enterKeyAction({ menuOpen: false, canSend: true, sendOnEnter: undefined })).toBe('send')
  })

  it('still inserts a newline on a phone even with the preference on', () => {
    // A phone's return key must not fire off a half-typed message. This case predates the
    // preference and outranks it.
    expect(enterKeyAction({ menuOpen: false, canSend: true, mobile: true, sendOnEnter: true })).toBe('newline')
  })

  it('lets an open typeahead menu own the key, whatever the preference says', () => {
    // The menu's own capture-phase handler selects the highlighted row, so this must DECLINE
    // rather than consume — for every combination, or picking a file would send the draft instead.
    for (const sendOnEnter of [true, false, undefined]) {
      for (const mobile of [true, false]) {
        expect(enterKeyAction({ menuOpen: true, canSend: true, mobile, sendOnEnter })).toBe('menu')
      }
    }
  })

  it('declines when there is nothing to send, so CodeMirror keeps its default', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: false })).toBe('none')
    // …but an empty draft with the preference off is still a newline: that is the whole point of
    // the setting, and it must not depend on having typed enough to send.
    expect(enterKeyAction({ menuOpen: false, canSend: false, sendOnEnter: false })).toBe('newline')
  })

  it('never sends when the preference is off, across every other input', () => {
    for (const mobile of [true, false, undefined]) {
      for (const canSend of [true, false]) {
        expect(enterKeyAction({ menuOpen: false, mobile, canSend, sendOnEnter: false })).not.toBe('send')
      }
    }
  })
})

describe('the preference actually reaches the key', () => {
  // 🔑 THE HALF THAT WAS MISSING. Everything above can pass while the setting is still inert.

  it('the composer reads the stored preference and hands it to the editor', () => {
    const code = codeOf('ui', 'Composer.tsx')
    expect(code.length, 'read Composer.tsx').toBeGreaterThan(1000)
    expect(code).toMatch(/useQuery\(\s*'chat:send-on-enter'/)
    expect(code).toMatch(/\.send_on_enter\b/)
    expect(code).toMatch(/sendOnEnter=\{sendOnEnter\}/)
  })

  it('the editor declares the prop and feeds it to the decision', () => {
    const code = codeOf('ui', 'composer', 'MarkdownInput.tsx')
    expect(code.length, 'read MarkdownInput.tsx').toBeGreaterThan(1000)
    expect(code).toMatch(/sendOnEnter\?:\s*boolean/)
    expect(code).toMatch(/from '\.\/enterKeyAction'/)
    expect(code).toMatch(/sendOnEnter:\s*cb\.current\.sendOnEnter/)
    // A falsy test would read an unresolved read as "off" and take Enter-to-send away on first
    // paint — the exact hazard the third behaviour test above pins.
    expect(code).not.toMatch(/!\s*cb\.current\.sendOnEnter/)
  })

  it('both writers bust the reader key, so the keyboard changes when the switch does', () => {
    // Two surfaces write this preference and each holds its own cache. Refreshing only the writer's
    // copy leaves the composer on the value the reader just changed — indistinguishable, from the
    // reader's chair, from the toggle doing nothing at all.
    expect(codeOf('pages', 'settings', 'ChatPanel.tsx')).toMatch(/invalidateKeys\('chat:send-on-enter'\)/)
    expect(codeOf('pages', 'settings', 'settingsWidgets.tsx')).toMatch(/'chat:send-on-enter'/)
  })
})

describe('the settings copy describes what the key does', () => {
  const panel = codeOf('pages', 'settings', 'ChatPanel.tsx')
  const hint = panel.match(/label="Send on Enter" hint=\{([^}]*)\}/)?.[1] ?? ''

  it('found the hint — otherwise every assertion below passes vacuously', () => {
    expect(hint, 'the Send on Enter row hint expression').not.toBe('')
    expect(hint).toMatch(/\?/) // a two-branch hint: one string per state
  })

  it('names both real behaviours', () => {
    expect(hint).toMatch(/Enter sends/)
    expect(hint).toMatch(/Enter inserts a newline/)
  })

  it('🪤 promises no mod-chord send while that chord optimizes the prompt', () => {
    // The original copy did, and this is the assertion that ties the claim to the code rather than
    // to a preference of mine: the chord is bound to the optimize callback, asserted right below,
    // so a hint that advertises it as a send route is false. Rebinding it later reds THAT line,
    // which is the prompt to revisit this copy — a design decision, deliberately not made here.
    expect(hint).not.toMatch(/(Cmd|Ctrl|⌘|Mod)[^']*Enter sends/i)

    const editor = codeOf('ui', 'composer', 'MarkdownInput.tsx')
    // Bounded by the NEXT binding rather than by a closing brace: the handlers are one-liners
    // wrapped in braces, so a brace-shaped end anchor matches the wrong one (and, on the first
    // attempt here, nothing at all — which the vacuity guard below is what caught).
    const modEnter = editor.match(/key: 'Mod-Enter', run:([\s\S]*?)(?=\{ key: '|\]\))/)?.[1] ?? ''
    expect(modEnter, 'the Mod-Enter binding').not.toBe('')
    expect(modEnter).toMatch(/onOptimize/)
    expect(modEnter).not.toMatch(/onSend/)
  })
})
