import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The gate panel's empty-prompt fallback is now REACHED, not merely written ─────────────────────
//
// `WorkflowAsk` renders `{ask.prompt || 'This run needs your input.'}`. That fallback existed and was
// **shadowed**: the engine's `_ask_payload` manufactured `prompt = "Approval needed"` for every gate
// that authored none, so `ask.prompt` was always truthy and the `||` branch was dead.
//
// Measured on the shipped templates: **7 of the 19 gate/approval nodes author neither `prompt` nor
// `message`**. The same default also killed `attention.ask_title`'s "{workflow}: {node} needs your
// input" and `needs_input._blocker_text`'s ladder — three written fallbacks, one upstream default.
//
// The engine no longer invents a prompt (`workflows/engine.py::_ask_payload`, and
// `tests/test_workflows_attention.py` asserts the ask it produces is empty). So this line is now
// load-bearing, and deleting the `||` would render an empty paragraph where the question goes.
//
// 🪤 A source assertion, deliberately: the defect is that a fallback is UNREACHED, and a render test
// that supplies its own props proves nothing about which branch production takes. The reachability
// proof lives on the Python side, at the call site that composes the ask.

const SRC = join(process.cwd(), 'src', 'pages', 'workflows', 'WorkflowAsk.tsx')
const raw = readFileSync(SRC, 'utf8')

describe('the gate panel still answers an empty prompt', () => {
  it('renders a sentence rather than an empty paragraph', () => {
    expect(raw, 'the prompt slot must keep its fallback').toContain(
      "{ask.prompt || 'This run needs your input.'}",
    )
  })

  it('the fallback is a real sentence, not a placeholder', () => {
    const m = /ask\.prompt \|\| '([^']+)'/.exec(raw)
    const fallback = m?.[1] ?? ''
    expect(fallback.length, 'a blank or stub fallback is the defect this guards').toBeGreaterThan(10)
    expect(fallback).toMatch(/[.!?]$/)
    // Not the generic literal the engine used to manufacture — that identified nothing, which is
    // why it was removed rather than moved down here.
    expect(fallback).not.toBe('Approval needed')
  })
})
