# tools/spikes — provisional, not shipped

Everything in this directory is a **proposal under review**, not a landed capability.

- Nothing here is imported by `src/personalclaw/**`. The dependency edge is one-way and
  absent: a spike may read the shipped code, the shipped code never reads a spike.
- Nothing here changes a shipped verdict, gate, or default. A spike that wants to
  demonstrate a behaviour change does so by returning a *new* object next to the real
  one, so the two can be diffed by a reviewer.
- A spike is deleted or promoted into `src/` by an explicit decision. It is not a place
  to park code that is "nearly ready".

Each spike states, at the top of its module, the question it exists to answer and what a
reviewer has to decide.
