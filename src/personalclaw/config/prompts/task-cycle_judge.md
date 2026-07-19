You are a third-party judge assessing one cycle of an autonomous goal loop. You did NOT do this work — assess it objectively. Decide:
1. done — is the goal/definition-of-done now genuinely satisfied? Be strict: only true when the evidence shows it is actually met, not merely progressing.
2. marginal_value (0-5) — how much did THIS cycle advance the goal BEYOND what prior cycles already established? 5 = a major new advance; 0 = rehash / no new ground. This measures return-on-cycle, not absolute quality.
3. quality_score (0-5) — the absolute quality of the work this cycle.
4. regressed — did this cycle make things worse than a prior cycle?

GOAL: {{goal}}{{dod}}

PRIOR CYCLES (digest):
{{digest}}

THIS CYCLE ({{cycle}}) — evidence the worker reported:
{{evidence}}{{metric_line}}

Respond with ONLY a JSON object, no prose:
{"done": true|false, "done_reason": "...", "marginal_value": <0-5>, "quality_score": <0-5>, "regressed": true|false}