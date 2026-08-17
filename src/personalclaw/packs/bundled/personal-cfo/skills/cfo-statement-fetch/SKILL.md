---
name: cfo-statement-fetch
description: Collect the month's bank and card statements from the finance folder and normalise them into one table.
---

# Collect this month's statements

Use this when a budget review needs the raw numbers first.

## Steps

1. Ask which month to close if the user has not said. Default to the previous full month.
2. Read every statement file in the finance folder bound during pack setup (`finance_folder`).
   CSV, TSV and plain-text exports only — do not open anything that looks like a credential
   file, and never copy account numbers into the summary.
3. Normalise every row to `date, description, amount, currency, account`. Negative amounts
   are outflows.
4. Report the row count per source file and any file you could not parse, by name. A silently
   skipped statement is a wrong budget.

## What not to do

- Do not guess at a missing month. Say the month is incomplete and name the gap.
- Do not store the normalised table anywhere outside the working directory for this task.
