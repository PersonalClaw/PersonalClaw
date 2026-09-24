You are adjudicating candidate memories against what is already stored. For each candidate below you are given the fact we just extracted and the existing rows it might collide with (`existing`, each with how it was matched).

Return ONLY valid JSON, no markdown fences:

{"verdicts": [{"index": <candidate index>, "verdict": "ADD"|"UPDATE"|"SUPERSEDE"|"NOOP", "target": "<existing key, for UPDATE/SUPERSEDE>", "unsure": false, "reason": "<one short line>"}]}

Choose the verdict by what is TRUE of the pair, not by what is tidy:

- `ADD` — the candidate is genuinely new. None of the existing rows is about the same thing.
- `UPDATE` — the same thing, changed value (the old value is simply out of date). `target` is the existing key to write onto.
- `SUPERSEDE` — the candidate CONTRADICTS an existing row and replaces it. `target` is the row being retired. The old row is kept and marked superseded, never deleted.
- `NOOP` — already stored. The candidate adds nothing (same fact, same value, or a strictly weaker restatement).

Set `"unsure": true` on an `UPDATE`/`SUPERSEDE` whenever you cannot tell which of the two is true — for example both could be current, they describe different time periods, or they may be about different subjects that share a name. Both rows are then KEPT and the contradiction is flagged for the user. That is the correct answer when the evidence is genuinely ambiguous: never guess in order to return a clean verdict.

Some rows carry `holder` and `weight` — whose claim it is and how strongly it is held. A claim from a lower-authority holder must not overwrite one from a higher-authority holder (what the user stated outranks a compiled synthesis, which outranks an outside/second-hand source). If the candidate is lower-authority than its target, prefer `unsure` or `NOOP` over `SUPERSEDE`.

Emit exactly one verdict per candidate index. Omit a candidate only if you have no opinion at all.
