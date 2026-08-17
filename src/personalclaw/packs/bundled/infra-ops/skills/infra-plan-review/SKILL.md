---
name: infra-plan-review
description: Read a Terraform plan and report what it would create, replace and destroy — counts first, reasoning second.
---

# Review a Terraform plan

## Steps

1. Ask for the plan output (a saved `terraform plan` text file, or the path to run it in). If
   neither exists yet, say so and stop — there is nothing to review.
2. Count the actions the plan reports: create, update, replace, destroy. Report the four counts
   before any commentary, so the shape of the change is visible before an opinion about it.
3. List every RESOURCE ADDRESS the plan would destroy or replace, one per line. A replace is a
   destroy plus a create, so it belongs in this list even when the resource name is unchanged.
4. For each destroy or replace, name the attribute that forces it, quoting the line from the
   plan. If the plan does not say, write "the plan does not say" rather than guessing.

## What not to do

- Do not run `terraform apply`, and do not offer to. This skill reads a plan; applying is a
  separate decision a human makes with the counts in front of them.
- Do not estimate cost. The plan does not contain prices, so any number here would be invented.
- Do not summarise a plan you could not read in full. Say which part was truncated.
