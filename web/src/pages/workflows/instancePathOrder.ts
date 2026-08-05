/** Numeric ordering for workflow node instance paths (issue #568).
 *
 *  Instance paths carry their indices INLINE — `root.children[10]`, `root.body@2`,
 *  `root.body#11` — so a plain string sort orders them by digit characters instead of by
 *  value: `children[10]` lands before `children[2]`, and `body@10` before `body@2`. The run
 *  view claims to read "in spec order" and silently stops doing so at the TENTH fan-out item
 *  or loop iteration. Nine items look perfect, which is why no short test and no seeded run
 *  ever caught it.
 *
 *  The engine already sorts numerically (`_natural_key` in `workflows/controller.py`, whose
 *  docstring documents this exact tenth-iteration failure). These are the views onto that
 *  engine's output, so they order paths the same way — a view that disagrees with the engine
 *  about which item is "next" is showing the user a different run than the one executing. */

/** Split a path into alternating text and digit-run tokens.
 *
 *  Splitting on EVERY digit run — not on a bracket/at-sign pattern — is what makes one
 *  comparator cover all three index forms the engine emits (`[n]`, `@n`, `#n`). A
 *  bracket-only parser would still misorder loop iterations, which is half the bug.
 *
 *  Mirrors `re.split(r"(\d+)", path)`: a capturing split, so digit runs survive as their own
 *  tokens and land at odd indices with text at even ones. */
function tokenize(path: string): string[] {
  return path.split(/(\d+)/)
}

/** Order two instance paths the way the engine does.
 *
 *  Digit runs compare as NUMBERS, text runs as text, left to right — the first difference
 *  decides. Two deliberate consequences:
 *
 *  * **A prefix sorts before what extends it.** `n[1]` precedes `n[1].child[0]`, so a
 *    container row always precedes its own descendants. `buildTree` and `visibleRows` derive
 *    parenthood from a path prefix and rely on that: a child ahead of its parent would render
 *    an orphan row above its container.
 *  * **Digits are compared numerically wherever they appear**, including inside a node id
 *    (`step10` after `step2`). Restricting numeric treatment to index positions would need
 *    this module to know the engine's path grammar, and an id whose number sorted by
 *    character while the index beside it sorted by value is a stranger rule than treating
 *    every number as a number.
 *
 *  Text is compared by CODE POINT rather than `localeCompare`, matching the engine's Python
 *  `str` comparison. Locale collation folds case and punctuation (it orders `a` before `B`,
 *  where a code-point sort does not), so a locale-aware tie-break would reintroduce a
 *  frontend/backend divergence on the non-numeric part while fixing the numeric one. */
export function compareInstancePaths(left: string, right: string): number {
  const a = tokenize(left)
  const b = tokenize(right)
  const shared = Math.min(a.length, b.length)

  for (let i = 0; i < shared; i += 1) {
    if (a[i] === b[i]) continue
    // Odd indices are digit runs by construction of the capturing split.
    if (i % 2 === 1) {
      const na = Number(a[i])
      const nb = Number(b[i])
      if (na !== nb) return na < nb ? -1 : 1
      // Equal value, different spelling: `[02]` vs `[2]`. The engine calls these equal too
      // (both key to the int `2`), so fall through to the next token rather than inventing a
      // tie-break the backend does not have.
      continue
    }
    return a[i] < b[i] ? -1 : 1
  }

  // Every shared token matched: the shorter path is a prefix of the longer one.
  if (a.length !== b.length) return a.length < b.length ? -1 : 1
  return 0
}

/** Comparator over anything carrying an `instance_path`, for `.sort()` on node lists. */
export function byInstancePath(
  a: { instance_path: string },
  b: { instance_path: string },
): number {
  return compareInstancePaths(a.instance_path, b.instance_path)
}
