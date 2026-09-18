# Writing skills

A **skill** is a directory with a `SKILL.md` in it: a procedure you want the
agent to follow the same way every time, stored as markdown instead of retyped
into a prompt. PersonalClaw ships 17 of them, keeps them in a library you own,
and decides per turn which ones the model gets to see.

This guide is the authoring half: when a skill is the right container, how to
write one, which directory to put it in, and how the surfacing machinery
actually chooses. The field-level contract — every frontmatter key, the parser's
tolerances, the interoperability delta with other harnesses — lives in
[the SKILL.md format reference](../reference/skill-format.md); this page links
there rather than restating it.

Everything below was read out of `src/personalclaw/skills/loader.py`,
`src/personalclaw/skills/surfacing.py` and `src/personalclaw/context.py`. Where
the code does something surprising, this guide says so instead of describing the
tidier thing you would expect.

## When a skill is the right container

A skill earns its keep when **the same procedure recurs and you keep having to
restate it**. Reach for one when:

- the steps are stable but long enough that you would not retype them;
- getting them wrong has a cost you have already paid at least once;
- you want the agent to *find* the procedure itself, without you naming it.

Reach for something else when:

| You want | Use instead |
|---|---|
| One rule that should hold in every conversation | An `always: true` skill (below), or a project instruction doc |
| A fact, not a procedure | Memory / the knowledge base |
| A one-off instruction for this conversation only | Just say it in the message |
| Something that must *run* on a schedule | A workflow or a scheduled task |
| Reference material the agent reads occasionally | A `resources:` file inside a skill, not a second skill |

The dividing line is recurrence plus procedure. A skill that fires once is a
message you over-engineered; a skill that holds a single sentence is a rule, and
rules belong in the always-on tier where they cost one line instead of a match.

## Writing your first one

Create a directory under your home library and put a `SKILL.md` in it:

```bash
mkdir -p ~/.personalclaw/skills/release-notes
$EDITOR ~/.personalclaw/skills/release-notes/SKILL.md
```

```markdown
---
name: release-notes
description: Turn a merged-PR list into user-facing release notes
triggers: release notes, changelog entry, what shipped
---

# Release notes

1. Group the PRs by user-visible surface, not by author or by label.
2. Write one line per group, in the user's language — no PR numbers in prose.
3. Anything with no user-visible effect goes in a single "internals" line.
4. Lead with the thing a returning user notices first.
```

`name` and `description` alone are a complete, valid skill. `triggers` is what
makes it surface on its own; without it the skill still lists, still loads, and
can still be invoked by name — it simply waits to be asked for.

The directory path relative to the skills root is the skill's **key**, so
`utils/tiny-url/SKILL.md` is the skill `utils/tiny-url`. Nesting is free and
purely organisational.

Confirm it landed: `personalclaw skills list`, or open the **Skills** page in the
dashboard (Capabilities → Skills), which shows each skill's tier, description and
trigger phrases.

## The four tiers, and what "precedence" really means

| Tier | Directory | Role |
|---|---|---|
| **Bundled** | `src/personalclaw/skills/bundled/` (inside the wheel) | The 17 skills PersonalClaw ships |
| **Project** | `$PERSONALCLAW_PROJECT_DIR/skills/` | Skills that travel with one workspace |
| **Global** | `~/.personalclaw/skills/` — plus `~/.agents/skills/` | Your library; the install target |
| **Agent-local** | `~/.personalclaw/agents/<agent>/skills/` | Visible to one agent only |

Two of those four are **install sources, not search paths**, and it is worth
knowing which. On startup `_ensure_builtin_skills` *copies* the bundled and
project trees into `~/.personalclaw/skills/<key>/`, whole directory at a time. So
a bundled skill is not consulted in place — by the time anything reads it, it
lives in the global tier.

Name resolution (`SkillsLoader._search_dirs`) then walks exactly three
directories, first hit wins:

1. `~/.personalclaw/agents/<agent>/skills/` — only when the turn belongs to that agent
2. `~/.personalclaw/skills/`
3. `~/.agents/skills/` — the cross-client agentskills.io directory, and where `personalclaw skills install` puts things by default

That order carries two consequences the tier table does not show:

- **An agent-local skill overrides a same-named global one, for that agent only** —
  and a skill in `~/.personalclaw/skills/` likewise wins over one of the same name
  in `~/.agents/skills/`. This is how you give one agent a different version of a
  procedure without forking your library.
- **A project skill does not reliably override a bundled skill of the same name.**
  Both copy into the same destination, and the copy is gated on file mtime
  (`src.stat().st_mtime > dest.stat().st_mtime`), so the newer *file* wins rather
  than the higher tier. Name your project skills distinctly instead of relying on
  a shadowing rule that is not there.

Editing a bundled skill in the home library is also not durable: the next sync
overwrites your copy as soon as the packaged file is newer. Copy it to a new key
and edit that.

## How a skill reaches the model

Two separate paths, and they behave differently enough that mixing them up is
the usual reason a skill "does not work".

### `always: true` — the whole body, every turn

```yaml
always: true
```

`get_always_skills` collects these and `SkillsLoader.get_context` inlines each
one's **full body** (frontmatter stripped) into every turn. They are also
excluded from trigger scoring entirely — an always-on skill never competes for a
match slot, because it is already in.

This is the expensive tier. Every always-on skill is prompt you pay for on every
turn whether or not it is relevant, so it suits short standing rules — an output
convention, a house style, a hard prohibition — and suits a long procedure
badly. If you find yourself wanting `always: true` because triggers are not
firing, fix the triggers.

### `triggers` — matched per turn, then budgeted

Everything else appears as a one-line index entry (`- **name**: description`) and
is pulled on demand by the agent with `skill_invoke{name}`, which also records
the use. What decides whether a skill is offered at all:

**The keyword score** (`get_triggered_skills`) splits `triggers` on commas,
lowercases each phrase, and scores it as

```
                    words the phrase and the message share
phrase score  =     -------------------------------------
                          words in the phrase
```

taking the best phrase's score and requiring **≥ 0.7**. That denominator is the
part worth internalising, because it makes phrase *length* a policy:

| Phrase length | Words that must appear | Miss budget |
|---|---|---|
| 1 word | 1 | none — 0/1 = 0.0 |
| 2 words | 2 | none — 1/2 = 0.5 |
| 3 words | 3 | none — 2/3 ≈ 0.67 |
| 4 words | 3 | one — 3/4 = 0.75 |
| 5 words | 4 | one — 4/5 = 0.8 |

So a two- or three-word phrase is an **all-or-nothing** phrase. `fill form`
matches "fill in this form" and does *not* match "fill this out". Prefer several
short phrases over one long one: each is scored independently and only the best
counts, so extra phrases cost nothing and each adds a way in.

**Negative triggers** are phrases prefixed with `!`, and they use a stricter,
different test — a negative fires when **every** one of its words appears in the
message (`neg_words <= text_words`), and one firing negative excludes the skill
outright, whatever the positives scored. Use them to split near neighbours
(`!image` on a PDF skill), and keep them to one or two words so the "all words
present" rule stays predictable.

**Semantic matching runs alongside it.** `get_surfaced_skills` prefers
`surfacing.surface_skills`, which scores each candidate as
`max(cosine similarity of the message against the skill's cached description
embedding, keyword score)` — semantic gate 0.55, keyword gate 0.7 — then ranks
by score and **breaks ties by `use_count`**. Two practical consequences:

- The embedding is of your `description`, not your body and not your triggers. A
  vague description ("helper for docs") is the single biggest reason a skill fails
  to surface on a paraphrase. Write the description as the sentence a user would
  say.
- No embedding model bound, or any error at all, degrades to the pure keyword
  path. Triggers are the floor, not the optional extra.

**Then the budget.** At most `skills.max_triggered` skills surface per turn
(**default 3**), so on a broad message your skill is competing with two others at
most and losing quietly. Each surfaced body is then capped by its declared
`context_tier` — `light` / `standard` / `heavy`, described in
[the format reference](../reference/skill-format.md#context_tier--what-this-skill-may-spend).

There is a third knob, `skills.progressive_disclosure_threshold` (**default 8**),
which switches the turn to an index-only block once *more* than that many skills
match. Note that it cannot fire on the defaults: the match list is already capped
at `max_triggered`, so it stays at 3 and never exceeds 8. Raising
`max_triggered` past 8 is what brings that path into play.

**One exception to all of it:** surfacing is for the built-in agent only.
`context.py` skips both the always-on block and trigger surfacing when the turn
belongs to a custom agent, which carries its own instructions — though a loop's
confirmed skills, which load actively every cycle, still do.

## Files beside `SKILL.md`

Everything in the skill directory is copied with it. Reference documents, data
files and helper scripts do not belong in the body — declare them as
`resources:` and the agent pulls one on demand with `skill_resource`, instead of
either ignoring them or reading the whole directory. The declared list is an
**allowlist**: an undeclared path is refused even when it plainly exists. See
[`resources` in the format reference](../reference/skill-format.md#resources--files-the-agent-loads-only-when-it-needs-them).

## Skills you did not write

Two sources put skills in your library on their own, and both are visible in the
same list:

- **`personalclaw skills install <id>`** fetches from a marketplace into
  `~/.agents/skills/`, after a supply-chain scan whose `DANGEROUS` verdict is not
  overridable. `personalclaw skills verify` re-checks installed skills' file
  hashes against their install baseline, so a skill mutated after install is
  detectable.
- **Auto-created skills** land under the `auto/` namespace when
  `skills.auto_create_from_sessions` is on (it is **off** by default): a session
  with a non-trivial multi-step procedure gets one synthesized. They carry
  `source: auto` provenance in their frontmatter, which is exactly how you tell
  them from anything you wrote.

`personalclaw skills curate` ages the `auto/` library by last use
(active → stale → archived); an archived skill stays on disk but is kept off the
turn index. `--dry-run` reports without writing.

## Where things live

| Path | What |
|---|---|
| `~/.personalclaw/skills/<key>/SKILL.md` | Your library — and where bundled/project skills are synced to |
| `~/.agents/skills/<key>/SKILL.md` | Cross-client directory; the default install target |
| `~/.personalclaw/agents/<agent>/skills/<key>/SKILL.md` | One agent's private override |
| `~/.personalclaw/skills/.skill_embeddings.json` | Cached description embeddings, keyed by mtime + model |

Nothing here rewrites a `SKILL.md`. Refinements ride as a sidecar overlay merged
at load time, so the file you wrote stays the file on disk.

## See also

- [SKILL.md format reference](../reference/skill-format.md) — the field-level contract this guide leans on.
- `src/personalclaw/skills/bundled/` — 17 working examples, and the best place to steal structure from.
- `src/personalclaw/skills/loader.py` — tiers, trigger scoring, and the context block.
- `src/personalclaw/skills/surfacing.py` — the semantic half of surfacing.
- [Configuration reference](../reference/configuration.md) — the `skills.*` settings named above.
