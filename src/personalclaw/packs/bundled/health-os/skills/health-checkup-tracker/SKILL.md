---
name: health-checkup-tracker
description: Track which routine checkups are due, from the user's own stated cadence.
---

# Which checkups are due

## Steps

1. Read the cadence list the user has recorded (checkup name, interval, last date). If a
   cadence has never been recorded, ask for it rather than assuming a standard interval.
2. Compute what is due or overdue, in days. Show the arithmetic — name the last date, the
   interval the user set, and how many days past due it now is.
3. List due items oldest-first. Say plainly when nothing is due.

## What not to do

- Do not invent a recommended interval. Intervals are the user's, or their clinician's.
- Do not infer a condition from a checkup's name.
