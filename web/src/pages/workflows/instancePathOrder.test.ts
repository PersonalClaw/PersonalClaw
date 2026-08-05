import { describe, expect, it } from 'vitest'

import { byInstancePath, compareInstancePaths } from './instancePathOrder'

// ── Numeric ordering of instance paths (issue #568) ─────────────────────────
//
// The bug these pin: five view sorts used `localeCompare`, which orders paths by digit
// CHARACTER while the engine (`_natural_key` in `workflows/controller.py`) orders them by
// VALUE. The two agree for nine items and diverge at the tenth — late enough that no seeded
// run and no short test ever showed it.
//
// The measured divergence, which is what the first test asserts verbatim:
//
//   paths:          n[1] n[2] n[9] n[10] n[11] n[20]
//   localeCompare:  n[1] n[10] n[11] n[2] n[20] n[9]
//   _natural_key:   n[1] n[2] n[9] n[10] n[11] n[20]

const sortPaths = (paths: string[]) => [...paths].sort(compareInstancePaths)

describe('compareInstancePaths', () => {
  it('orders fan-out indices numerically past the tenth item', () => {
    // Shuffled input, so passing cannot be an accident of the input already being ordered.
    const shuffled = ['n[11]', 'n[2]', 'n[20]', 'n[9]', 'n[1]', 'n[10]']
    expect(sortPaths(shuffled)).toEqual(['n[1]', 'n[2]', 'n[9]', 'n[10]', 'n[11]', 'n[20]'])
  })

  it('disagrees with localeCompare exactly where the engine does', () => {
    // The negative half of the same claim: if this ever matches, the fix has been reverted.
    const shuffled = ['n[11]', 'n[2]', 'n[20]', 'n[9]', 'n[1]', 'n[10]']
    const locale = [...shuffled].sort((a, b) => a.localeCompare(b))
    expect(locale).toEqual(['n[1]', 'n[10]', 'n[11]', 'n[2]', 'n[20]', 'n[9]'])
    expect(sortPaths(shuffled)).not.toEqual(locale)
  })

  it('orders loop iterations numerically — the `@n` form', () => {
    // Splitting on digit runs rather than on brackets is what makes this work. A
    // bracket-only comparator would order `[n]` correctly and still misorder every loop.
    const shuffled = ['root.body@10', 'root.body@2', 'root.body@11', 'root.body@1']
    expect(sortPaths(shuffled)).toEqual([
      'root.body@1', 'root.body@2', 'root.body@10', 'root.body@11',
    ])
  })

  it('orders foreach items numerically — the `#n` form', () => {
    // The third index form. `realBatchFrames.json` is a live 12-item capture whose own
    // snapshot arrived as #0 #1 #10 #11 #2 …, so this form really does reach double digits.
    const shuffled = ['root.body#10', 'root.body#2', 'root.body#0', 'root.body#11', 'root.body#9']
    expect(sortPaths(shuffled)).toEqual([
      'root.body#0', 'root.body#2', 'root.body#9', 'root.body#10', 'root.body#11',
    ])
  })

  it('keeps a container ahead of its own descendants', () => {
    // `buildTree`/`visibleRows` derive parenthood from a path prefix, so a child sorting
    // above its parent would render an orphan row above its container.
    const shuffled = [
      'n[2].child[1]', 'n[10]', 'n[1].child[0]', 'n[2]', 'n[1]', 'n[1].child[10]',
      'n[1].child[2]',
    ]
    expect(sortPaths(shuffled)).toEqual([
      'n[1]',
      'n[1].child[0]',
      'n[1].child[2]',
      'n[1].child[10]',
      'n[2]',
      'n[2].child[1]',
      'n[10]',
    ])
  })

  it('orders a nested loop inside a fan-out at every level', () => {
    // Both forms in one path, both past the tenth — the shape a watcher's synthesize stage
    // really produces (`…children[1].body@3.children[0]`).
    const shuffled = [
      'root.children[10].body@2', 'root.children[2].body@10', 'root.children[2].body@2',
      'root.children[10].body@10',
    ]
    expect(sortPaths(shuffled)).toEqual([
      'root.children[2].body@2',
      'root.children[2].body@10',
      'root.children[10].body@2',
      'root.children[10].body@10',
    ])
  })

  it('compares digits numerically inside a node id too', () => {
    // The deliberate choice: every digit run is a number, wherever it sits. Treating an id's
    // digits as text while the index beside it sorted by value is a stranger rule.
    expect(sortPaths(['step10', 'step2', 'step1'])).toEqual(['step1', 'step2', 'step10'])
  })

  it('orders unindexed segments as text', () => {
    expect(sortPaths(['root.cases[standard]', 'root.cases[deep]'])).toEqual([
      'root.cases[deep]', 'root.cases[standard]',
    ])
  })

  it('treats equal values spelled differently as equal, like the engine', () => {
    // Both key to the int 2 in `_natural_key`, so neither precedes the other.
    expect(compareInstancePaths('n[02]', 'n[2]')).toBe(0)
  })

  it('is a total order: antisymmetric, reflexive, and transitive', () => {
    // A comparator that is not consistent makes `.sort()` output depend on input order,
    // which is how an "intermittent" row-order bug is born.
    const paths = ['n[1]', 'n[2]', 'n[10]', 'n[1].child[0]', 'root.body@11', 'step2', '']
    for (const a of paths) {
      expect(compareInstancePaths(a, a)).toBe(0)
      for (const b of paths) {
        // `|| 0` normalizes the -0 that negating a zero sign produces.
        const forward = Math.sign(compareInstancePaths(a, b))
        expect(forward).toBe(-Math.sign(compareInstancePaths(b, a)) || 0)
      }
    }
    const asc = sortPaths(paths)
    const descThenSorted = sortPaths([...paths].reverse())
    expect(descThenSorted).toEqual(asc)
  })

  it('handles empty and index-only paths without throwing', () => {
    expect(compareInstancePaths('', '')).toBe(0)
    expect(sortPaths(['', 'n[0]'])).toEqual(['', 'n[0]'])
    expect(sortPaths(['20', '3', '100'])).toEqual(['3', '20', '100'])
  })
})

describe('byInstancePath', () => {
  it('sorts node-shaped records, not just bare strings', () => {
    // The form all five call sites use.
    const nodes = [
      { instance_path: 'root.body#10', state: 'done' },
      { instance_path: 'root.body#2', state: 'done' },
      { instance_path: 'root.body#1', state: 'done' },
    ]
    expect([...nodes].sort(byInstancePath).map((n) => n.instance_path)).toEqual([
      'root.body#1', 'root.body#2', 'root.body#10',
    ])
  })
})
