import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ApprovalCard, REMEMBER_SCOPES } from './ApprovalCard'
import type { ApprovalSegment } from './chatTypes'

// OU-8 — the approval brief is a four-zone DESCRIPTION of a pending tool call:
//   1 WHAT  — tool + arguments
//   2 WHY   — the runner's one-line purpose, when it supplied one
//   3 TOUCH — the established blast-radius facets
//   4 REACH — how far the answer is remembered
// Two properties of that brief are security-relevant and therefore asserted, not assumed:
// it never claims a facet it did not establish, and it never advocates approval.

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', ...over,
})

/** The scope picker, addressed the way a user does. */
const scopeTab = (label: string) => screen.getByRole('tab', { name: label })
const touchList = () => screen.queryByRole('list', { name: /what this can touch/i })

describe('ApprovalCard — the four zones', () => {
  it('renders what, why, what-it-can-touch and how-far-it-reaches', () => {
    const { container } = render(
      <ApprovalCard
        seg={seg({ tool: 'bash', input: 'rm -rf /tmp/scratch', purpose: 'Clearing the scratch dir before the rebuild', risk: 'destructive' })}
        onAct={() => {}}
      />,
    )
    // 1 WHAT — the tool and its arguments, not a paraphrase of them.
    expect(container.textContent).toContain('bash(rm -rf /tmp/scratch)')
    // 2 WHY — the purpose line the runner supplied.
    expect(container.textContent).toContain('Clearing the scratch dir before the rebuild')
    // 3 TOUCH — a NAMED list of established facets. `bash` + destructive establishes the
    // shell facet; it establishes no read, which is why "Reads only" must be absent.
    const list = touchList()
    expect(list).not.toBeNull()
    const chips = [...list!.querySelectorAll('li')].map((li) => li.textContent?.trim())
    expect(chips).toContain('Runs a command')
    expect(chips).not.toContain('Reads only')
    // 4 REACH — the scope picker plus the promise it makes, in visible text.
    expect(screen.getByRole('tablist', { name: 'Remember this choice' })).toBeTruthy()
    expect(container.textContent).toContain('Nothing is remembered. The next tool call asks again.')
  })

  it('omits the WHY zone rather than inventing one when the runner gave no purpose', () => {
    const { container } = render(<ApprovalCard seg={seg({ purpose: undefined })} onAct={() => {}} />)
    // Nothing stands in for an absent purpose — no "no purpose given", no filler.
    expect(container.textContent).not.toMatch(/purpose/i)
  })

  it('shows the risk chip when the wire carried a risk, and no chip when it did not', () => {
    for (const risk of ['safe', 'caution', 'destructive'] as const) {
      const { container, unmount } = render(<ApprovalCard seg={seg({ risk })} onAct={() => {}} />)
      expect(container.textContent, risk).toMatch(/Safe|Caution|Destructive/)
      unmount()
    }
    const { container } = render(<ApprovalCard seg={seg({ risk: undefined })} onAct={() => {}} />)
    expect(container.textContent).not.toMatch(/Safe|Caution|Destructive/)
  })

  it('survives a risk level this build has never heard of, claiming nothing extra', () => {
    // A session written by another build can carry an unmapped level. The card must not
    // crash and must not silently treat it as safe.
    const { container } = render(
      <ApprovalCard seg={seg({ tool: 'bash', risk: 'apocalyptic' as ApprovalSegment['risk'] })} onAct={() => {}} />,
    )
    expect(container.textContent).toContain('Permission needed')
    expect(container.textContent).not.toContain('Reads only')
  })
})

describe('ApprovalCard — the blast-radius zone never over-claims', () => {
  it('renders NO facet zone at all when the inputs establish nothing', () => {
    // THE CENTRAL RAIL. `deriveBlastRadius` returns `undefined` here on purpose: an
    // all-false radius rendered as four negative chips would be a confident all-clear
    // derived from zero evidence. The renderer must not undo that by enumerating the
    // facets with on/off states.
    const { container } = render(<ApprovalCard seg={seg({ tool: 'ponder' })} onAct={() => {}} />)
    expect(container.textContent).toContain('Permission needed')  // the card DID render
    expect(touchList()).toBeNull()
    for (const negative of ['No writes', 'No network', 'No shell', 'Not read', 'no writes', 'none']) {
      expect(container.textContent, negative).not.toContain(negative)
    }
  })

  it('shows only the ESTABLISHED facets, never the full four with on/off states', () => {
    render(<ApprovalCard seg={seg({ tool: 'web_fetch', risk: 'caution' })} onAct={() => {}} />)
    const chips = [...touchList()!.querySelectorAll('li')].map((li) => li.textContent?.trim())
    expect(chips).toEqual(['Uses the network'])
  })

  it('claims a read only on positive evidence, and both facets for a read-only shell call', () => {
    // EFFECTIVE-safe is already derived FROM read-only-ness in task_modes.py, so a safe
    // bash call is the one case that legitimately claims shell AND read-only.
    render(<ApprovalCard seg={seg({ tool: 'bash', risk: 'safe' })} onAct={() => {}} />)
    const chips = [...touchList()!.querySelectorAll('li')].map((li) => li.textContent?.trim())
    expect(chips).toEqual(['Runs a command', 'Reads only'])
  })
})

describe('ApprovalCard — remember-scope is a closed set that maps to real backend actions', () => {
  // The actions `api_chat_session_approve` (chat_handlers.py) implements. A scope may only
  // post one of these; inventing a fifth would mean the button does nothing.
  const BACKEND_ACTIONS = ['approved', 'rejected', 'trust', 'trust_agent', 'trust_reads', 'yolo']

  it('every scope posts a distinct action the backend already implements', () => {
    const actions = REMEMBER_SCOPES.map((s) => s.action)
    expect(new Set(actions).size).toBe(actions.length)
    for (const a of actions) expect(BACKEND_ACTIONS, a).toContain(a)
  })

  it('every scope is reachable and posts exactly its own action — no unmapped option', () => {
    // Enumerated, not sampled: an option added to the vocabulary with no working click path
    // (or a `default:` that quietly funnelled it into `trust`) fails here.
    for (const s of REMEMBER_SCOPES) {
      const onAct = vi.fn()
      const { unmount } = render(<ApprovalCard seg={seg()} onAct={onAct} />)
      fireEvent.click(scopeTab(s.label))
      fireEvent.click(screen.getByRole('button', { name: new RegExp(`^Allow bash`) }))
      expect(onAct, s.key).toHaveBeenCalledWith('a1', s.action)
      unmount()
    }
  })

  it('starts on the narrowest scope, so an unmodified Allow remembers nothing', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg()} onAct={onAct} />)
    expect(scopeTab('Just this once').getAttribute('aria-selected')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash/ }))
    expect(onAct).toHaveBeenCalledWith('a1', 'approved')
  })

  it('states the promise for the selected scope in visible text, and updates it on change', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    fireEvent.click(scopeTab('This chat'))
    expect(container.textContent).toContain('Every tool in this chat runs without asking')
    // The promise must not claim per-TOOL memory: nothing in the backend remembers a
    // decision for one tool, so no label may imply it (see the ApprovalCard comment).
    expect(container.textContent).not.toMatch(/always for this tool|only this tool|this tool from now on/i)
    fireEvent.click(scopeTab('This agent'))
    expect(container.textContent).toContain('Saved on this agent')
  })

  it('carries the scope into the Allow control\'s accessible name', () => {
    render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    // A bare "Allow" does not say how far the answer reaches — the name must.
    expect(screen.getByRole('button', { name: /^Allow bash — just this once: Nothing is remembered/ })).toBeTruthy()
    fireEvent.click(scopeTab('This agent'))
    expect(screen.getByRole('button', { name: /^Allow bash — this agent: Saved on this agent/ })).toBeTruthy()
  })

  it('denies single-shot whatever the scope says, and says so in the name', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg()} onAct={onAct} />)
    fireEvent.click(scopeTab('This agent'))
    const deny = screen.getByRole('button', { name: 'Deny bash — nothing is remembered' })
    fireEvent.click(deny)
    // No backend action persists a refusal, so a broad scope must not silently widen one.
    expect(onAct).toHaveBeenCalledWith('a1', 'rejected')
  })
})

describe('ApprovalCard — the brief describes, it never advocates', () => {
  // A prompt that nudges toward yes trains the reflex it exists to interrupt. This holds
  // that line mechanically instead of by copy review alone.
  const ADVOCACY = [
    /looks safe/i, /safe to (run|allow|approve)/i, /recommend/i, /we suggest/i, /suggested/i,
    /should (allow|approve|be fine)/i, /probably/i, /harmless/i, /no risk/i, /low risk/i,
    /go ahead/i, /nothing to worry/i, /usually fine/i, /it'?s fine/i, /trusted tool/i,
  ]

  /** Everything a user can read on the card: visible text plus every title/aria-label,
   *  because a nudge hidden in a tooltip is still a nudge. */
  function readableText(container: HTMLElement): string {
    const attrs = [...container.querySelectorAll('[title],[aria-label]')]
      .flatMap((el) => [el.getAttribute('title'), el.getAttribute('aria-label')])
      .filter(Boolean)
    return [container.textContent ?? '', ...attrs].join(' \n ')
  }

  it('contains no advocacy copy in any zone, at any risk level, under any scope', () => {
    for (const risk of ['safe', 'caution', 'destructive'] as const) {
      const { container, unmount } = render(
        <ApprovalCard seg={seg({ tool: 'bash', input: 'ls -la', purpose: 'Listing the repo root', risk })} onAct={() => {}} />,
      )
      for (const s of REMEMBER_SCOPES) {
        fireEvent.click(scopeTab(s.label))
        const text = readableText(container)
        // Vacuity guard: the scan is looking at a real, fully rendered card.
        expect(text).toContain('Permission needed')
        expect(text.length, `${risk}/${s.key}`).toBeGreaterThan(200)
        for (const pattern of ADVOCACY) expect(text, `${risk}/${s.key} ${pattern}`).not.toMatch(pattern)
      }
      unmount()
    }
  })

  it('does not make Allow the visual primary', () => {
    render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    const allow = screen.getByRole('button', { name: /^Allow bash/ })
    const deny = screen.getByRole('button', { name: /^Deny bash/ })
    // `tone: 'primary'` paints a solid --color-primary fill: the conventional "this is the
    // action to take". On a permission prompt that IS a recommendation, so neither verb
    // gets it. Deny keeps its long-standing tinted danger edge.
    expect(allow.style.background).not.toContain('--color-primary')
    expect(deny.style.background).toContain('--color-danger')
  })

  it('focuses and pre-submits nothing on arrival', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    // An autofocused or default-submitting Allow turns a stray Enter into consent.
    expect(container.querySelectorAll('[autofocus]')).toHaveLength(0)
    const buttons = [...container.querySelectorAll('button')]
    expect(buttons.length).toBeGreaterThan(0)
    for (const b of buttons) expect(b.getAttribute('type')).toBe('button')
    expect(document.activeElement).toBe(document.body)
  })
})
