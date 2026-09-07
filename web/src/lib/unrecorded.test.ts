import { describe, expect, it } from 'vitest'
import {
  PROVENANCE_SCHEMA,
  UNRECORDED,
  UNRECORDED_LABEL,
  provenanceRecorded,
  reportSchema,
  tokensUnrecorded,
} from './unrecorded'

/** The page half of the ONE vocabulary (#2540 / #2561 / #2562).
 *
 *  The Python half is `personalclaw.evals.provenance`, and
 *  `tests/test_evals_unrecorded_vocabulary.py` is what keeps the two spelling the word and the
 *  schema number identically. What is tested here is the behaviour a consumer relies on. */
describe('the one vocabulary for "unrecorded"', () => {
  it('reads the schema the report STATES and never defaults it to v1', () => {
    expect(reportSchema({ report_schema: 2 })).toBe(2)
    expect(reportSchema({ report_schema: 1 })).toBe(1)
    // 🔑 THE DEFAULTING TRAP. A report that cannot say what it recorded must not be read as one
    // that said it recorded nothing — that is #2562's collapse with an extra step.
    expect(reportSchema({})).toBeNull()
    expect(reportSchema(undefined)).toBeNull()
    expect(reportSchema(null)).toBeNull()
    expect(reportSchema({ report_schema: NaN })).toBeNull()
  })

  it('answers provenance from the schema, not from whether a key is present', () => {
    expect(provenanceRecorded({ report_schema: PROVENANCE_SCHEMA })).toBe(true)
    expect(provenanceRecorded({ report_schema: PROVENANCE_SCHEMA + 1 })).toBe(true)
    expect(provenanceRecorded({ report_schema: PROVENANCE_SCHEMA - 1 })).toBe(false)
    expect(provenanceRecorded({})).toBe(false)
    // A schema-2 report carrying `provider_binding: null` is RECORDED — the run bound nothing, and
    // that is a measurement. A key-presence check gets this right; a truthiness check does not.
    expect(provenanceRecorded({ report_schema: 2, provider_binding: null } as never)).toBe(true)
    // And the reverse case a key-presence check gets WRONG: schema 1 with the key somehow present.
    expect(provenanceRecorded({ report_schema: 1, provider_binding: null } as never)).toBe(false)
  })

  it('treats an ABSENT tokens_recorded as unrecorded, and only an explicit false as the fact', () => {
    expect(tokensUnrecorded({ tokens_recorded: false })).toBe(true)
    expect(tokensUnrecorded({ tokens_recorded: true })).toBe(false)
    // Absent means the row predates #2540 and carries no flag. It is NOT the unrecorded state:
    // rendering every legacy row as "not recorded" would replace one wrong claim with another, so
    // the caller falls through to the existing `null`/number handling for those.
    expect(tokensUnrecorded({})).toBe(false)
  })

  it('keeps "not recorded" distinct from the panels\' "not measured"', () => {
    expect(UNRECORDED_LABEL).toBe('not recorded')
    expect(UNRECORDED_LABEL).not.toBe('not measured')
    expect(UNRECORDED).toBe('unrecorded')
    expect(PROVENANCE_SCHEMA).toBe(2)
  })
})
