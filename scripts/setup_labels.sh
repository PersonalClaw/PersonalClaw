#!/usr/bin/env bash
# Create/refresh the PersonalClaw issue+PR label taxonomy (plan 37 T1.2).
# Idempotent: `gh label create --force` updates a label if it already exists.
# Usage: scripts/setup_labels.sh [owner/repo]   (defaults to PersonalClaw/PersonalClaw)
set -euo pipefail

REPO="${1:-PersonalClaw/PersonalClaw}"

label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force; }

# Triage / workflow
label "needs-triage"     "d4c5f9" "Not yet triaged by the maintainer"
label "good-first-issue" "7057ff" "Well-scoped, low-context entry point for new contributors"
label "blocked"          "b60205" "Waiting on an external dependency or decision"

# Type
label "bug"              "d73a4a" "Something behaves incorrectly"
label "feature"          "0e8a16" "New capability or improvement"
label "docs"             "0075ca" "Documentation only"

# Area (mirror the package layout)
label "area:gateway"     "1d76db" "Gateway / dashboard API"
label "area:memory"      "1d76db" "Memory + learning"
label "area:knowledge"   "1d76db" "Knowledge base + ingestion"
label "area:loops"       "1d76db" "Goal loops / workflows"
label "area:apps"        "1d76db" "App platform / providers"
label "area:security"    "1d76db" "Auth, sandbox, egress, scanner, SEL"
label "area:ci"          "1d76db" "CI / release engineering"
label "area:frontend"    "1d76db" "web/ SPA"

# Wave (roadmap execution waves 0-4)
for w in 0 1 2 3 4; do
  label "wave:$w" "fef2c0" "Roadmap execution wave $w"
done

echo "Labels created/updated on $REPO."
