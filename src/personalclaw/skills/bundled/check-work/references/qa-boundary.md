# check-work vs. the deep QA companion — where the line is

Two verification surfaces exist on purpose. This file names the boundary so neither
grows into the other, and so a reader can tell in one glance which one they want.

| | `check-work` (this skill) | deep QA companion |
|---|---|---|
| Question | "is what you just claimed actually true?" | "does this feature meet its spec?" |
| Input | this session's recent turns + tool calls | a spec / plan / whole feature |
| Cost | seconds, 2-4 checks, no model fan-out | minutes, bundled template, model calls |
| Depth | the claims as stated, nothing inferred | derived requirements, edge cases, replay |
| Trigger | user says "check your work"; or an SDLC stage gate passes with `loops.check_work_stages` on | an explicit deep-verify request |
| Output | pass / fail / unverifiable per check, with quoted evidence | a spec-coverage assessment |

## The rules that keep the line sharp

1. **check-work never reads a spec.** The moment it needs one, the answer is a
   hand-off, not a bigger step 1. Its whole value is that it is cheap enough to run
   after every completion turn.
2. **check-work never widens past what was claimed.** A claim the session did not make
   is out of scope even if the task implied it. Scope creep here re-creates the deep
   half badly.
3. **check-work caps at 4 checks.** The cap is the boundary made mechanical: past four
   you are writing a test suite, which is the deep half's job.
4. **Escalation is one-directional and explicit.** When a check-work run keeps failing,
   or the user asks for whole-feature assurance, say so and hand off to the deep
   companion. Never silently deepen.
5. **Both halves obey the same doctrine.** Ground truth over self-report: an unrun check
   is `unverifiable`, never a pass. That rule is shared, so depth is the only difference
   between them.

## Status of the deep half

The deep companion is owned by the SELF-VERIFICATION plan (its QA-Companion session),
not by this skill. Until it lands, escalation is a sentence to the user — "this needs a
deeper, spec-driven pass than check-work does" — and the run stops there. check-work
does **not** grow a substitute in the meantime; a soft dependency stays soft.

## The shared core

Both entry points into the light half (this skill, and the SDLC post-gate hook behind
`loops.check_work_stages`) call `personalclaw.check_work` — one derivation module, so
there is never a second behavior wearing this name.
