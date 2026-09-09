# Atomic plan catalog

The roadmap's plans were too large and too interdependent: parts of a plan would finish, then the rest would block on *another* plan, so ten-plus plans sat in flight at once and no status read was accurate.

This catalog is the fix. Every plan is decomposed into **atoms**: one coherent feature, executable start-to-finish in a single go. The cut line is exactly the dependency seam — anything that would force you to pause an atom and go execute other work is instead its own atom with an explicit dependency edge.

**684 atoms** across **70 plans** — 650 done, 34 remaining. 850 dependency edges.

## How to use it

1. `dag.json` is the machine-readable source; the roadmap dashboard renders it (tiers, ready frontier, validation).
2. **Start only from the ready frontier** — atoms whose dependencies are all `done`. Those need nothing else in flight.
3. One atom per branch/PR. Mark it `done` in `dag.json` when its PR lands.
4. `<CODE>.md` holds the human-readable atoms for one source plan.

## Startable now

- `CA-8` **S4 desktop connect-to-gateway mode + multi-gateway switcher (T4.1 + amendment T4.4)** — CA
- `PP-16` **A Loop becomes a WorkflowRun (retire the second work-unit noun)** — PP

## Validation problems

- **1 dependency cycle(s)** — must be broken

## Execution order (topological)

All 684 atoms, deps strictly before dependents (first 60 of 684):

```
AAP-1 → AAP-2 → AAP-3 → AAP-4 → AAP-5 → AAP-6 → AAP-7 → AAP-8 → AAP-9 → AAP-10 → AE-1 → AE-2 → AE-3 → AE-4 → AE-5 → AE-6 → AE-7 → AE-8 → AE-9 → AE-10 → AG-1 → AG-2 → AG-3 → AG-4 → AG-5 → AG-6 → AG-7 → AG-8 → AG-9 → AG-10 → AG-11 → AG-12 → AG-14 → AP-1 → AP-2 → AP-3 → AP-4 → AP-5 → AP-6 → AP-7 → APE-1 → APE-2 → APE-3 → APE-4 → APE-5 → APE-7 → APE-8 → APE-9 → APE-10 → APE-11 → APE-6 → APE-12 → AR-9 → AR-1 → AR-2 → AR-3 → AR-4 → AR-5 → AR-6 → AR-7 → …
```

The 34 not-yet-done atoms, in that same order:

```
AR-1 → AR-2 → AR-3 → AR-4 → AR-5 → AR-6 → AR-7 → AR-8 → CA-8 → CE-9 → CRE-7 → DCU-3 → DIST-11 → DIST-12 → DL-9 → DL-11 → DL-10 → ET-7 → ET-8 → ET-9 → ET-4 → ET-5 → HC-3 → LMMV-7 → LV-7 → OU-11 → PCS-9 → PUBL-10 → SH-9 → SH-11 → DC-1 → SH-4 → PP-16 → WF2UNI-14
```
