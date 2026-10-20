// The design detector's baseline, and the exact comparison against it.
//
// The detector's deterministic rules run over web/src, and the gate compares what they find with
// a committed baseline, so a new finding fails and a fixed one lowers its line. The baseline holds
// one JSON line per (file, rule), sorted by file and then rule, with the number of that rule's
// findings in that file:
//
//     {"file":"src/pages/Home.tsx","rule":"side-tab","count":1}
//
// Paths are relative to web/. Line numbers are not part of the key, so an edit above a finding
// leaves its line alone. Pure: no file is read or written here, so the comparator is tested on
// planted findings (src/design/designDetectorRatchet.test.ts).

/** The baseline file, relative to web/. */
export const BASELINE_FILE = 'design-detector.baseline.jsonl'

const KEYS = ['count', 'file', 'rule'].join(', ')

const keyOf = (file, rule) => JSON.stringify([file, rule])
const plural = (n, one, many) => (n === 1 ? one : many)

/** The line a (file, rule) count is written as. */
export function baselineLine({ file, rule, count }) {
  return JSON.stringify({ file, rule, count })
}

/** The baseline's counts by (file, rule), and every way its text breaks the file's shape. */
export function parseBaseline(text) {
  const counts = new Map()
  const problems = []
  let previous
  text.split('\n').forEach((raw, i) => {
    if (!raw.trim()) return
    const at = `${BASELINE_FILE} line ${i + 1}`
    let row
    try {
      row = JSON.parse(raw)
    } catch {
      problems.push(`${at} is not JSON: ${raw}`)
      return
    }
    const r = row && typeof row === 'object' && !Array.isArray(row) ? row : {}
    const shaped =
      Object.keys(r).sort().join(', ') === KEYS &&
      typeof r.file === 'string' && r.file !== '' &&
      typeof r.rule === 'string' && r.rule !== '' &&
      Number.isInteger(r.count) && r.count >= 1
    if (!shaped) {
      problems.push(`${at} must hold a file, a rule and a count of at least 1: ${raw}`)
      return
    }
    const key = keyOf(r.file, r.rule)
    if (counts.has(key)) {
      problems.push(`${at} lists ${r.file} ${r.rule} a second time: keep one line per (file, rule)`)
      return
    }
    if (previous && (r.file < previous.file || (r.file === previous.file && r.rule < previous.rule))) {
      problems.push(`${at} is out of order: the lines are sorted by file, then rule`)
    }
    counts.set(key, { file: r.file, rule: r.rule, count: r.count })
    previous = r
  })
  return { counts, problems }
}

/** Every difference between the findings and the baseline, each a sentence. An empty list passes. */
export function compareFindings(findings, baselineText) {
  const { counts: base, problems } = parseBaseline(baselineText)
  const out = [...problems]
  if (findings.length === 0 && base.size > 0) {
    const listed = [...base.values()]
    const total = listed.reduce((n, { count }) => n + count, 0)
    const files = new Set(listed.map(({ file }) => file)).size
    out.push(
      `the detector reported no findings, but ${BASELINE_FILE} lists ${total} in ${files} ` +
        `${plural(files, 'file', 'files')}: a run that read no files must not pass. ` +
        'If every finding really is fixed, empty the baseline in the same commit',
    )
    return out
  }
  const found = new Map()
  for (const { file, rule } of findings) {
    if (/^([A-Za-z]:)?[\\/]/.test(file)) {
      out.push(`a finding names ${file}: findings are named relative to web/`)
      continue
    }
    const key = keyOf(file, rule)
    const seen = found.get(key)
    if (seen) seen.count += 1
    else found.set(key, { file, rule, count: 1 })
  }
  const keys = [...new Set([...found.keys(), ...base.keys()])].sort()
  for (const key of keys) {
    const [file, rule] = JSON.parse(key)
    const now = found.get(key)?.count ?? 0
    const was = base.get(key)?.count ?? 0
    if (now === was) continue
    if (was === 0) {
      out.push(
        `${file}: ${now} new ${rule} ${plural(now, 'finding', 'findings')}, where the baseline has none; ` +
          `fix ${plural(now, 'it', 'them')}`,
      )
    } else if (now > was) {
      out.push(`${file}: ${rule} rose from ${was} to ${now}; fix the new ${plural(now - was, 'finding', 'findings')}`)
    } else if (now === 0) {
      out.push(`${file}: ${rule} is fixed: delete its line from ${BASELINE_FILE}`)
    } else {
      out.push(
        `${file}: ${rule} fell from ${was} to ${now}: change its line in ${BASELINE_FILE} to ` +
          baselineLine({ file, rule, count: now }),
      )
    }
  }
  return out
}
