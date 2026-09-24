import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A state badge says the app's own word for the state, not the backend's ────────────────────
//
// Measured on `#/projects` against a seeded home with one archived project: three pills on one
// screen read `builtin`, `builtin`, `archived` — bare lowercase machine tokens, while the SAME app
// already ships the prose spelling for each concept:
//
//   Built-in   pages/apps/AppsSection.tsx (source-group label) · pages/agents/AgentDetail.tsx
//   Archived   pages/ChatPage.tsx (filter option) · KnowledgeListPage's own bulk-action toast
//   Disabled   pages/apps/AppsSection.tsx (filter option) · ScheduleDetail · LifecycleDetail
//
// So this was `labels` drift with a clear majority form, not a style choice — and two of the five
// outliers sat in files that ALREADY used the capitalized form for the same concept a few hundred
// lines away.
//
// 🔑 WHAT IS DELIBERATE AND STAYS. Two neighbouring idioms look identical to a careless scan:
//
//   1. `uppercase`-styled badges — SAVED, DEPRECATED, SUNSET, SUGGESTED. The source is lowercase
//      but the rendered pixels are not, so there is no machine token on screen. Left alone.
//   2. Lowercase tokens with NO prose counterpart anywhere in the app (see EXEMPT). Capitalizing
//      those would be inventing a vocabulary rather than converging on one, which is a taste call
//      for the owner, not a defect fix.
//
// This rail therefore polices exactly the three concepts that HAVE a canonical spelling, and keeps
// the exemption list honest by asserting each exempt token is still lowercase — if one of them ever
// gains a prose form, this fails and the list gets revisited instead of quietly rotting.

const SRC = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

/** Every bare single-token text node rendered inside a pill/badge, with the class list that styles it. */
function badgeTokens(): { file: string; token: string; uppercased: boolean }[] {
  const re = /className="([^"]*rounded-(?:pill|md|lg)[^"]*)"[^>]*>([a-z][a-z0-9_-]{2,19})</g
  const hits: { file: string; token: string; uppercased: boolean }[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(re)) {
      hits.push({
        file: abs.slice(SRC.length + 1),
        token: m[2],
        uppercased: /\buppercase\b/.test(m[1]),
      })
    }
  }
  return hits
}

/** Lowercase badge tokens with no prose counterpart in the app — deliberately left as-is.
 *
 *  `refine` LEFT this list (LV-5), and it left by converging, not by being excused: the skill
 *  proposal pill no longer renders the bare `kind` value at all. It renders "Refine", plus the
 *  stumble that produced the proposal ("Refine · you corrected it") — so there is no lowercase
 *  machine token on screen for the exemption to describe. This rail's own honesty check is what
 *  noticed; that is the check doing its job, so the entry was removed rather than the check. */
const EXEMPT = new Set(['esc', 'manual', 'suppressed', 'multi-instance', 'span', 'div'])

/** The three concepts whose prose spelling already ships. */
const CONVERGED: [string, string][] = [
  ['builtin', 'Built-in'],
  ['archived', 'Archived'],
  ['disabled', 'Disabled'],
]

describe('state badges use the prose spelling the app already ships', () => {
  const tokens = badgeTokens()

  it('finds the badge population it is meant to police', () => {
    // Vacuity floor: if the regex stops matching (a class rename, a refactor to a primitive), every
    // assertion below passes while checking nothing.
    expect(tokens.length, 'bare-token badges found across web/src').toBeGreaterThanOrEqual(8)
  })

  it('no badge renders a lowercase token for a concept with a canonical spelling', () => {
    const offenders = tokens
      .filter((t) => !t.uppercased)
      .filter((t) => CONVERGED.some(([lower]) => t.token === lower))
      .map((t) => `${t.file}: "${t.token}"`)
    expect(offenders, 'use the prose form these files already use elsewhere').toEqual([])
  })

  it('each converged concept still ships its prose form as a visible label', () => {
    // Guards the other direction: the fix is not "delete the badge".
    const all = walk(SRC).map((abs) => readFileSync(abs, 'utf8')).join('\n')
    for (const [lower, prose] of CONVERGED) {
      expect(all.includes(`>${prose}<`) || all.includes(`'${prose}'`), `${lower} → ${prose}`).toBe(true)
    }
  })

  it('the exemptions stay honest — each is still a lowercase badge with no prose form', () => {
    // A listed token that has since gained a capitalized spelling is drift the list is now hiding.
    const seen = new Set(tokens.filter((t) => !t.uppercased).map((t) => t.token))
    const stale = [...EXEMPT].filter((t) => !['span', 'div'].includes(t) && !seen.has(t))
    expect(stale, 'exempt token no longer present as a lowercase badge — re-check whether it converged')
      .toEqual([])
  })
})
