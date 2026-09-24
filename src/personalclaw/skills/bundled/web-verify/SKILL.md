---
name: web-verify
description: Verify a built web page or app actually renders and works — drive it in a real browser, check the console and network, confirm the thing you changed is the thing being served, and capture evidence. Use before claiming a frontend change works.
always: false
triggers: verify the page, does it render, render correctly, screenshot the page, check in the browser, browser check, headless browser, preview the build, smoke test the ui, devtools console, console errors, broken layout, page is blank, check the site, verify the frontend, !verify email, !verify signature, !verify checksum
---

# Web verify

A frontend change is not done when the build succeeds. It is done when the page a
user actually loads shows the change, with a clean console. This skill is the loop
that closes that gap.

The failure this prevents is the one that keeps happening: the build output looked
fine, so the change was declared working, and the browser was serving something
else entirely — a stale bundle, a different process on the port, a dist directory
that shadowed the real one.

## The loop

1. **Build, and check the exit code.** A failed build leaves the previous bundle in
   place. If you screenshot after a failed build you are looking at the old code and
   will report a change that never shipped.
2. **Confirm you are serving the bundle you just built.** Read the asset hash out of
   the generated `index.html` and compare it to the chunk the browser actually
   loaded. If they differ, stop — everything after this is measuring the wrong
   artifact.
3. **Establish a mounted-ness floor.** Before asserting anything about your change,
   assert the app mounted at all: a known-good element that must exist on every
   render. A blank page and a page missing your feature look identical to a selector
   that finds nothing.
4. **Add a positive control.** Assert something you know is true. If the control
   fails, your harness is broken, not the app.
5. **Drive the actual interaction.** Click, type, submit. A rendered control is not
   a working one.
6. **Read the console and the network panel.** A silent 500 on a background fetch
   renders as an empty state, which looks like "no data yet" rather than "broken".
7. **Capture evidence** — before/after for a visual change, the console log for a
   behavioural one.

## What a check never sees

State your scope honestly. A single screenshot at one viewport, on first render, in
one theme, with a warm cache, sees a narrow slice:

- **Viewport** — a defect can exist only at 390px or only at 1440px. Name the width
  you measured.
- **Theme** — if the app stores its theme itself, forcing the OS-level preference
  does not switch it. Set the app's own key.
- **Timing** — a layout that settles 400ms after mount looks fine in a screenshot
  taken at 2s. Sweep for transient states.
- **Cold state** — an empty backend, a cleared local storage, and a first-ever visit
  are three different renderings.
- **Inner scrollers** — a full-page capture of a layout whose content scrolls inside
  a child element captures only the viewport.

## Worked example

A change adds a "Copy link" button to the artifact detail page.

```
1. Build          → npm run build --workspace web; echo "exit=$?"   → exit=0
2. Serving mine?  → grep -o 'index-[a-z0-9]*\.js' web/dist/index.html → index-9f2a1c4b.js
                    browser's loaded chunk                            → index-9f2a1c4b.js   ✅ same
3. Mounted floor  → the page's <h1> is present                       → ✅
4. Positive ctrl  → the artifact title matches the seeded fixture     → ✅ (harness sees real data)
5. Drive it       → click "Copy link"; read the clipboard             → got the artifact URL
6. Console        → 0 errors, 0 failed requests
7. Evidence       → before/after at 1440px AND 390px; noted that only
                    light theme was checked
```

Reported as: *"Copy link works on artifact detail — verified at 1440px and 390px,
light theme, clipboard receives the canonical URL, console clean. Not checked: dark
theme, keyboard-only activation."*

That last sentence is the part people skip. Naming what you did not check is what
makes the part you did check trustworthy.
