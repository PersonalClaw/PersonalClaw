---
name: health-os-setup
description: Finish setting up the Health OS pack — bind the journal folder and the reminder time.
---

# Finish setting up Health OS

Run this once after installing the pack. It is re-runnable.

## Interview

Ask each question, one at a time, and wait for the answer.

1. **Which folder should hold your health journal?** One file per day is written there, and
   nothing else reads it. Accept an absolute path to an existing directory. Record it with:
   `POST /api/packs/health-os/bindings {"key": "journal_folder", "value": "<path>"}`
2. **What time of day should reminders land?** Record it with:
   `POST /api/packs/health-os/bindings {"key": "reminder_time", "value": "<HH:MM>"}`

Then say plainly:

- the checkup-cadence trigger is installed but **disabled** — they enable it from Automations;
- this pack never gives medical advice, and the agents will refuse to.

## What not to do

Do not ask for a diagnosis, a medication list, or an insurance identifier. The pack records
what the user chooses to type and nothing more.
