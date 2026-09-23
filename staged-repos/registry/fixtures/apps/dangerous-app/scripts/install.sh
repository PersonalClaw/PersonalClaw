#!/usr/bin/env bash
# FIXTURE — NEVER RUN THIS FILE.
#
# It exists so the registry validator can be proven to REFUSE a listing rather
# than merely warn about one. The scanner reads files; it does not run them, and
# neither does the validator. The guard below means an accidental execution exits
# before reaching the destructive line, while the line itself stays intact so the
# fixture keeps matching the rule it is here to trip (`destructive_root`).
echo "scanner fixture — not executable" >&2
exit 1

rm -rf / --no-preserve-root
