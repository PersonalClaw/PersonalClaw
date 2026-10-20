/** One line of a line-level diff: kept, added in the new text, or deleted from the old. */
export type DiffRow = { kind: 'same' | 'add' | 'del'; text: string }

/** Line-level LCS diff of old→new. Returns rows in display order: a removed line
 *  appears (as 'del') just before the kept/added lines that follow it.
 *
 *  Shared by the code cockpit's diff animation (`pages/code/DiffReveal`) and the stale-write
 *  recovery (`lib/staleWrite`), which both merges and shows a difference from these rows — one
 *  LCS, so the difference a user reviews is the one the merge applied. O(n·m); callers with a
 *  file-sized input bound it first (`DiffReveal`'s `LCS_CELL_CAP`). */
export function lineDiff(oldText: string, newText: string): DiffRow[] {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  const n = a.length, m = b.length
  // LCS length table (O(n*m) — fine for typical file sizes).
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const rows: DiffRow[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { rows.push({ kind: 'same', text: a[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { rows.push({ kind: 'del', text: a[i] }); i++ }
    else { rows.push({ kind: 'add', text: b[j] }); j++ }
  }
  while (i < n) { rows.push({ kind: 'del', text: a[i] }); i++ }
  while (j < m) { rows.push({ kind: 'add', text: b[j] }); j++ }
  return rows
}
