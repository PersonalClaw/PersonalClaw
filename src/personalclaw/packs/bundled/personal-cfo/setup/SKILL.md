---
name: personal-cfo-setup
description: Finish setting up the Personal CFO pack — bind the finance folder and the digest day.
---

# Finish setting up Personal CFO

Run this once after installing the pack. It is re-runnable: run it again whenever the answers
change.

## Interview

Ask each question, one at a time, and wait for the answer.

1. **Which folder holds your statements?** This is the only folder the pack's skills read.
   Accept an absolute path to an existing directory. Record it with:
   `POST /api/packs/personal-cfo/bindings {"key": "finance_folder", "value": "<path>"}`
2. **Which day should the weekly digest land on?** Record it with:
   `POST /api/packs/personal-cfo/bindings {"key": "digest_day", "value": "<weekday>"}`

Then tell the user two things plainly:

- the spending-digest trigger is installed but **disabled** — they enable it from Automations
  when they want it to start running;
- the finance connector is unconfigured until they configure or substitute it.

## What not to do

Do not ask for bank credentials. The pack never stores them; the connector requirement
collects them through the credential store if the user chooses to configure it.
