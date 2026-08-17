# SKILL.md format reference

PersonalClaw reads the same `SKILL.md` files the wider agent-skill ecosystem
writes. This page states exactly what the loader accepts, what it ignores, and
which field is a PersonalClaw extension — so a skill written for another harness
can be dropped in without guesswork, and one written here can be handed out
without surprises.

Everything below was **verified against the parser** (`src/personalclaw/skills/loader.py`)
rather than inferred from it, and is pinned by `tests/test_skill_format_compat.py`.

## The shape

A skill is a directory containing a `SKILL.md`. The directory path (relative to
the skills root) is the skill's key, so `utils/tiny-url/SKILL.md` is the skill
`utils/tiny-url`. Everything beside `SKILL.md` — scripts, assets, data — is
copied with the skill and reachable from it.

```markdown
---
name: pdf-tools
description: Fill, split and merge PDF files
---

# PDF tools

Body markdown: the instructions the model actually reads.
```

That file — **`name` and `description`, nothing else** — is a complete, valid
skill in PersonalClaw. It is also a complete, valid skill in the vanilla
ecosystem, which is the point.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | recommended | Display name. Falls back to the directory key when absent. |
| `description` | recommended | One line, shown in listings and used for surfacing. Falls back to the key. |
| `triggers` | no — **PersonalClaw extension** | Phrases that auto-surface the skill. See below. |
| `always` | no | `true` loads the skill on every turn, bypassing trigger matching. |
| `status` | no | `active` (default) or a lifecycle value the curator sets. |
| `resources` | no | Files beside `SKILL.md` an agent may load on demand. See below. |

Unknown fields are read and kept, not rejected — a foreign harness's extra
frontmatter is preserved rather than treated as an error, though PersonalClaw
does nothing with it.

### `triggers` — the auto-surfacing extension

`triggers` is **specific to PersonalClaw** and **entirely optional**. It holds
phrases that cause the skill to be offered when a user's message matches:

```yaml
triggers: pdf, fill form, merge documents, !image     # comma-separated
```

```yaml
triggers:                                             # or a YAML block list
  - pdf
  - fill form
```

Both spellings work; the list form is folded to the comma-separated form on read.
A phrase prefixed with `!` is a **negative** trigger — if any negative trigger
matches, the skill is excluded regardless of positive matches.

**A skill with no `triggers` is fully functional.** It lists, loads, and can be
invoked directly or by an agent; it simply never *auto*-surfaces. An empty
`triggers` value means "never auto-surface" — it is never treated as
"matches everything".

### `resources` — files the agent loads only when it needs them

A skill directory may carry more than its `SKILL.md`:

```
<skills root>/vendor-payloads/SKILL.md
                             /reference/api-notes.md
                             /scripts/check.sh
```

Declaring them makes them **addressable** — the agent can pull one by name
instead of either ignoring them or reading the whole directory:

```yaml
resources:
  - path: reference/api-notes.md
    description: field-by-field notes on the vendor payload
  - path: scripts/check.sh
  - reference/changelog.md
```

`description` is optional, and a bare string (the last item) declares a path on
its own. As everywhere else in this frontmatter, an inline `# comment` after a
value is *not* stripped — put comments on their own line.

`skill_invoke` then returns the skill body **plus a catalog** of those
declarations — one line each, path and description, **never their contents**. The
agent loads one with `skill_resource(skill, path)`. A skill with no `resources`
block behaves exactly as before.

`resources` is the one **list-of-mappings** block the frontmatter reader
understands (see the parser limits below — nested mappings are otherwise skipped).
It has its own small reader, so `path` and `description` are the only keys read
inside an item.

**What `skill_resource` will and will not do** — the rules are worth knowing
because a resource that violates one fails visibly rather than silently:

- **Declared only.** The list is an allowlist. A file that exists in the skill
  directory but is not declared is refused; so is any path with a `..` segment, an
  absolute path, or a backslash. Declarations that break those rules are dropped
  at parse time, so a bad entry never widens what is loadable.
- **No escaping the skill directory.** A declared path that turns out to be a
  symlink out of the skill directory is refused after resolution.
- **Capped, visibly.** A resource larger than 32 KB comes back truncated with an
  explicit notice — never a silent cut.
- **Read, never run.** A `scripts/*` resource is returned as *text*. Running it is
  the ordinary command path's job, with the screening that path applies.
- Resource content is treated as untrusted data (it is third-party authored), so
  it arrives fenced: the model reads it, it does not obey it.

## Interoperability

**Importing a foreign skill.** Vanilla `SKILL.md` files import cleanly. The
loader needs only the frontmatter delimiters and reads `name`/`description` when
present. No conversion step, no manifest, no registration.

**Exporting ours.** Another harness consuming a PersonalClaw skill gets
everything except `triggers`, which it will not recognize. Since `triggers` is
metadata about *when to offer* the skill and not part of its instructions, a
foreign harness ignoring it loses auto-surfacing and nothing else — the body
markdown, the scripts, and the description all still apply.

**Practical consequence:** write `triggers` for the benefit of PersonalClaw, and
keep the body self-contained so the skill still reads correctly without it.

### The conformance delta, stated exactly

PersonalClaw stays on the shared `SKILL.md` + YAML-frontmatter format rather than
diverging, and the delta is additive in both directions:

| Key | Who defines it | What PersonalClaw does with it |
|---|---|---|
| `name`, `description` | the shared format | reads both (falls back to the directory key) |
| `license`, `allowed-tools`, `metadata`, any other foreign key | another harness | **preserved on disk, unused** — never rewritten, never an error |
| `triggers`, `always`, `status` | PersonalClaw | auto-surfacing, always-load, curator lifecycle |
| `resources` | PersonalClaw | the on-demand resource tier described above |

So: **a conformant third-party skill installs unmodified** — same directory shape,
same frontmatter, no conversion pass, and it lists, loads and (if it declares
`resources`) exposes its catalog straight away. A PersonalClaw skill handed out
loses `triggers`/`resources` handling and nothing else.

It buys interoperability and **not** a trust exemption. Every install — foreign or
local — goes through the one supply-chain gate (quarantine → scan at the source's
trust tier → commit the exact scanned bytes), and a `dangerous` verdict is refused
with no override, `--force` included. `tests/test_skill_install_guarded.py` pins
both halves: the conformant skill committing byte-identical, and the floor holding
for that same skill when it ships a destructive script.

## The parser, and its limits

Frontmatter is read by a deliberate **line parser**, not a YAML library — a
~40-line `key: value` reader with no dependency. That choice is intentional
(skills are read on every turn; a YAML parse per skill per turn is not free), but
it means the accepted grammar is narrower than YAML. What that means concretely:

**Tolerated** (each of these used to lose every field, silently):

- a UTF-8 BOM before `---` (Windows editors add one)
- leading blank lines or whitespace before `---`
- CRLF line endings
- single or double quotes around values (stripped)
- a `:` inside a value — `description: Use x: then y` keeps `Use x: then y`
- YAML comment lines (skipped)
- block lists, indented or at column 0 — folded to comma-separated
- block and folded scalars (`|`, `>`, `|-`, `>+`, …) — the indented block is
  folded onto one line, so a multi-line `description` reads correctly

**Not supported** — and these fail *quietly*, so avoid them:

| Construct | What happens (measured, not assumed) |
|---|---|
| Nested mappings | The nested keys are **skipped**, not hoisted. `meta:` with `sub: v` under it yields `meta: ""` and no `sub`. |
| Flow mappings | Kept as raw text: `meta: {k: v}` yields the string `"{k: v}"`. |
| Anchors and aliases | Not interpreted: `name: &x a` yields the string `"&x a"`, and `*x` stays `"*x"`. |
| Duplicate keys | Last one wins. |
| Block scalars keeping line breaks | Folded to one line. `\|` does not preserve newlines here — these fields are single-line by contract. |

If a file lacks frontmatter delimiters entirely, it parses as no metadata and the
skill falls back to its directory key for both name and description. That is a
supported state, not an error.

### Why the tolerance matters

A skill whose frontmatter fails to parse is **not** an obvious failure. It
installs, lists (under its directory key), never trigger-matches, and logs
nothing. From the outside it is indistinguishable from a skill the author simply
never wrote triggers for. That silent-failure mode is why the BOM and
leading-whitespace cases are handled rather than documented as gotchas, and why
`tests/test_skill_format_compat.py` pins each one.

## See also

- `src/personalclaw/skills/loader.py` — the parser itself; this page is its contract.
- `tests/test_skill_format_compat.py` — the executable version of this page.
- The Skills surface in the dashboard (Settings → Skills) lists what is installed,
  which tier it came from, and its trigger phrases.
