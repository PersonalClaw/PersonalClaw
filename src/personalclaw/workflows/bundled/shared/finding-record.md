Return each issue as a **Finding** record:

```
{severity: Critical|Major|Minor|Nit, location, problem, why, recommended_fix, status: Open}
```

What the severities mean — the ladder is the whole point, because a gate predicate like "no open
Critical or Major" is only meaningful if every stage grades the same way:

- **Critical** — wrong or unusable as delivered. Acting on this output would cause harm.
- **Major** — a reader or caller would be misled, blocked, or make a bad decision.
- **Minor** — a real flaw that does not block anyone.
- **Nit** — preference. Say so honestly rather than inflating it to look thorough.

`location` must be specific enough to act on: a file and symbol, a line, a section heading. "The
error handling" is not a location; "`_resolve_items`, the None branch" is.

`why` is the consequence, not a restatement of the problem. "Missing null check" is the problem;
"a foreach over an unresolved binding fans out zero items and the run reports success" is why it
matters.

An empty findings list is a valid and useful answer. Inventing a finding to look thorough
poisons every consumer of this record: it makes gates fire on noise, it makes an until-dry loop
run forever, and it teaches the reader to ignore the output.
