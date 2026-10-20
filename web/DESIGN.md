---
name: PersonalClaw
description: Self-hosted personal AI agent dashboard — warm coral energy on calm dark surfaces
colors:
  coral-primary: "#ff6b5b"
  coral-emphasis: "#ff9a86"
  coral-container: "#5a1d12"
  on-coral: "#3f1008"
  amber-secondary: "#ffb454"
  canvas-night: "#0f0f0f"
  surface: "#131314"
  surface-low: "#1b1b1b"
  surface-container: "#1e1f20"
  surface-high: "#282a2c"
  surface-highest: "#333537"
  ink: "#e3e3e3"
  ink-var: "#c4c7c5"
  ink-low: "#9a9b9c"
  outline-variant: "#444746"
  ok-green: "#0ebc5f"
  warn-orange: "#ff8d41"
  danger-red: "#f55e57"
  info-blue: "#4e8ff8"
typography:
  # Machine-readable mirror of the tokens.css `data-type` ramp (src/design/
  # tokens.css) — one entry per distinct size rung. The impeccable design hook
  # parses these fontSize values as the blessed type scale (±0.5px). Keep this in
  # lockstep with tokens.css; the prose "Hierarchy" below summarizes it.
  display-l:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "2.625rem"
    fontWeight: 280
    lineHeight: 1.14
  display-m:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "2.25rem"
    fontWeight: 320
    lineHeight: 1.22
  display-s:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "2rem"
    fontWeight: 360
    lineHeight: 1.19
  headline-l:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 350
    lineHeight: 1.29
  headline-m:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 380
    lineHeight: 1.17
  title-l:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 470
    lineHeight: 1.2
  body-l:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.41
  body-m:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.33
  label-m:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 470
    lineHeight: 1.33
  body-s:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.38
  caption:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 470
    lineHeight: 1.33
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.38
rounded:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "28px"
  squircle: "34px"
  pill: "9999px"
spacing:
  xs: "4px"
  s: "8px"
  m: "12px"
  l: "16px"
  xl: "20px"
  2xl: "24px"
  3xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.coral-primary}"
    textColor: "{colors.on-coral}"
    rounded: "{rounded.pill}"
    height: "40px"
    padding: "0 20px"
  button-primary-hover:
    backgroundColor: "{colors.coral-emphasis}"
  button-secondary:
    backgroundColor: "{colors.surface-high}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    height: "40px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-highest}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
  button-danger:
    backgroundColor: "{colors.danger-red}"
    textColor: "#ffffff"
    rounded: "{rounded.pill}"
  icon-button:
    backgroundColor: "transparent"
    textColor: "{colors.ink-var}"
    rounded: "{rounded.pill}"
    size: "40px"
  icon-button-hover:
    backgroundColor: "{colors.surface-high}"
    textColor: "{colors.ink}"
  card:
    backgroundColor: "{colors.surface-container}"
    rounded: "{rounded.lg}"
    padding: "16px"
---

# Design System: PersonalClaw

## 1. Overview

**Creative North Star: "The Friendly Machine in the Night Studio"**

PersonalClaw's dashboard is a capable engine with a soft shell, working in a studio after hours. The room is dark and calm — near-black canvas (#0f0f0f), tonal surface layers instead of hard borders — and the machine's warmth shows through one coral voice (#ff6b5b) that glows, blooms, and sweeps through gradient accents wherever the agent is alive: the composer focus ring, the agent orb, the spark. Two influences are deliberately blended: a **neural-expressive** signature (fractional variable-font weights like `wght 280` display, ambient glow, gradient energy that reads as live intelligence) and **Google's playful expressive element language** (pill shapes, tonal containers, springy overshoot on press) — both re-tinted through PersonalClaw's own coral/terracotta identity rather than copied.

Controls are physical and friendly: pills by default, squircles for large sheets, press and hover shown by colour at once, and springs kept for what changes space (a panel opening, a card moving). Personality is *budgeted and tunable* — a global expressiveness knob and bounciness slider scale every playful moment, and `prefers-reduced-motion` makes all of it instant. Each identity spends that budget its own way (§7). The default look draws no ambient light: the wave field, the halo and the sheen are Claw Arcade's. This system explicitly rejects hacker-terminal cosplay: no green-on-black, no scanlines, no readability sacrificed to look "technical." The optional `data-ui="cli"` density mode tightens spacing and squares corners as a utilitarian layout choice; it is not a costume.

**Key Characteristics:**
- Dark-first tonal layering (canvas → surface → container → high → highest); light mode is an override, dark is home.
- One warm coral accent carrying all "agent is alive" moments; amber (#ffb454) as its gradient partner.
- DM Sans variable at fractional weights (280–500) — weight, not size, does the expressive work.
- Pill-shaped controls, springy interaction, squircle sheets; radius and spacing ride user-tunable scale tokens.
- Motion in two families: spatial springs (overshoot allowed) and critically-damped effects (never bounce opacity).

## 2. Colors

A restrained dark neutral ramp with a single committed coral accent and a warm gradient family behind it.

### Primary
- **Warm Coral** (#ff6b5b): the brand voice. Primary buttons, active/selected states, the focus ring, agent-activity glow. Hover shifts to **Coral Emphasis** (#ff9a86). Ink on solid coral is **Deep Ember** (#3f1008). Tonal container fills use **Ember Container** (#5a1d12) with **Peach Ink** (#ffe0d6) on top.

### Secondary
- **Warm Amber** (#ffb454): coral's gradient partner — the tail of the brand spectrum (`#c85a48 → #ff6b5b → #ff9a7a → #ffb454`), used in the spark, and in Claw Arcade's bloom and focus-ring sweep (§7). Rarely a fill on its own.

### Neutral
- **Night Canvas** (#0f0f0f): the app background — the studio's darkness.
- **Surface ramp** (#131314 → #1b1b1b → #1e1f20 → #282a2c → #333537): elevation by tone, not shadow. Panels, cards, hover fills, and the highest tier for pressed/selected chrome.
- **Ink** (#e3e3e3): primary text. **Ink Variant** (#c4c7c5): secondary text and idle icons. **Ink Low** (#9a9b9c): metadata only, never body copy.
- **Outline Variant** (#444746): hairline dividers and input strokes; full-strength outline (#8e918f) is reserved for focus/emphasis.
- **The desktop buddy** has no colour of its own. Its orb takes the agent orb's colour, `--color-orb-alive` (the scheme's primary), while an agent is alive ([`docs/design/agent-orb.md`](../docs/design/agent-orb.md#the-component)), and `--color-orb-rest` (Ink Variant, the idle-icon neutral above) where nothing is alive. So no buddy token joins this ramp, the front matter, `tokens.css` or the token registry ([`docs/design/buddy/character.md`](../docs/design/buddy/character.md#constraints)).

### Semantic
- **OK Green** (#0ebc5f), **Warn Orange** (#ff8d41), **Danger Red** (#f55e57), **Info Blue** (#4e8ff8) — status pills, alerts, destructive actions. Aliased as success/warning/error; never used decoratively.

### Named Rules
**The One Voice Rule.** Coral is the only accent allowed to mean "the agent" — selection, focus, primary action, live activity. If coral appears where nothing is active, actionable, or alive, remove it.
**The Tone-Not-Line Rule.** Depth and grouping come from the surface ramp; borders are 1px hairlines (#444746) or nothing. Never a colored side-stripe.

The desktop buddy keeps the One Voice Rule and the Glow-Is-Alive Rule (§4): its orb takes coral only while an agent is alive and draws no glow, and the semantic colours reach it only as a badge or a wash, never as the whole orb ([`docs/design/buddy/character.md`](../docs/design/buddy/character.md#constraints)).

### The token layers

Every colour reaches a component through four layers, so a scheme, an identity or the light mode recolours the whole app by reassigning tokens rather than by rules of its own.

1. **Palette** (layer 1): raw values, named by what they are: `--<hue>-<step>`, where the step is the colour's OKLCH lightness × 1000. `--gray-0` is black and `--gray-1000` white. The steps are the contrast-tuned values the app ships, so a scale is sparse; no step is invented to fill a gap. Declared in `tokens.css`'s `:root`; the naming is `src/design/palette.ts`.
2. **Semantic** (layer 2): `--color-*` (surfaces, ink, outline, primary and its pairings, the status tones), the brand gradient (`--grad-*`, `--glow-*`) and the ring highlight, written only in terms of layer 1, or as an alias of another layer-2 token. Tailwind's colour utilities (`bg-surface-low`, `text-on-surface`) are generated from these names.
3. **Editor** (layer 3): what the code editor and the terminal take: their surface, text, selection, current line, gutter and cursor, and the terminal's ANSI colours, written only in terms of layer 2 and never reassigned. Each token is named after the key it feeds, Monaco's colour id (`editor.selectionBackground` is `--editor-selection-background`) or xterm's theme field (`brightBlack` is `--terminal-bright-black`). Monaco and xterm take concrete colours, so `src/design/editorTheme.ts` reads the computed tokens, builds one Monaco theme and the terminal's theme from them, and rebuilds both when the scheme, the mode or an owner's colour changes. No page carries editor colours of its own. Monaco keeps its own syntax colours and diff tints, and the ANSI colours are xterm's defaults in both modes.
4. **Component**: a token composed from layer 2 for one component: the composer ring's stops, the glass material, the lift and composer-focus shadows. Never reassigned per mode or per scheme; it follows because what it is made of does.

Rules:

- **Dark is the default and light is a reassignment.** `.light` re-points layer 2 onto light palette steps and does nothing else. Elevation shadows sit outside the colour layers and keep a light reassignment of their own, since their alpha differs per mode as well as their colour.
- **A scheme is one small override sheet.** `src/design/schemes/<id>.css` sets layers 1 and 2 only: the palette steps the base sheet lacks, then the scheme's semantic tokens for dark and for light. The sheets are generated from `schemes.ts` (`npm run generate:schemes`), which stays the one input the contrast rails read. `data-theme` on `<html>` picks the active sheet. An identity carries no colour (§7), so its sheet is its base scheme's.
- **Only the owner's own colours ride inline.** A saved theme or an unsaved edit is written on `<html>` over the base sheet; a curated scheme and the default are pure stylesheet.
- **One set of brand and state tokens.** Coral, the agent orb, the buddy and the status language read the same layer-2 tokens, so a scheme recolours them together: `--color-brand` (coral: the scheme's primary), `--color-orb-alive` and `--color-orb-rest` (the orb's dots, and the buddy's, while an agent is alive and where nothing is), and `--color-state-quiet`, `-active`, `-waiting` and `-blocked`, the colour each of the four families reads in (the idle neutral, coral, the warn tone, the danger tone). Each is an alias of a colour already named here, so it follows the mode and the scheme and carries no value of its own. A state is never shown by its colour alone: `ui/StateMark` puts its words beside its mark, and the mark keeps a ring in forced colours.
- **No component reads layer 1.** Components read layers 2 to 4; `src/design/` is the only place a palette token is named (`design/tokenLint.test.ts`).

## 3. Typography

**Display/Body Font:** DM Sans (variable, 100–1000; system-ui fallback)
**Mono Font:** JetBrains Mono (variable; ui-monospace fallback)

**Character:** One warm rounded sans carries everything; the neural-expressive signature is *fractional variable weight* — big type gets lighter (display at `wght 280`), small type gets firmer (labels at `wght 470`), so hierarchy reads as confidence, not shouting.

### Hierarchy
Applied via `data-type` roles, not per-component sizes. The discrete size rungs
are `0.75 · 0.8125 · 0.9375 · 1.0625 · 1.25 · 1.5 · 1.75 · 2 · 2.25 · 2.625rem`
(mirrored in the YAML frontmatter above and `tokens.css`); type sizes off these
rungs are drift.
- **Display** (wght 280–360, 2rem–2.625rem, ~1.14): page heroes and empty-state headlines only.
- **Headline** (wght 350–470, 1.25rem–1.75rem): section and panel titles.
- **Title** (wght 470–500, 0.9375rem–1.25rem): card titles, list-row leads.
- **Body** (wght 400, 0.8125rem–1.0625rem, 65–75ch max for prose): default reading text at #e3e3e3.
- **Label** (wght 470, 0.8125rem–1.0625rem): buttons, chips, form labels — the same size as body, one weight step firmer.
- **Caption** (wght 470, 0.75rem): the smallest tier — dense chip/badge/timestamp micro-text below the body floor.
- **Mono** (JetBrains Mono, 0.8125rem): code, terminal panes, IDs, and the `data-ui="cli"` density mode.

### Named Rules
**The Weight-First Rule.** Emphasis is a variable-weight step (400 → 470 → 500), never uppercase-with-tracking and never a new font.

## 4. Elevation

A hybrid model: tonal layering does the everyday work (the surface ramp IS elevation), while two named materials handle the extremes — **neumorphic ground** (soft dual shadow + faint inset highlight, no hard border: `--neu-extrude`) for controls that sit *in* the surface, and **glass sky** (frosted translucent overlay, 16px backdrop blur at 0.72 alpha, 1px inset top highlight) for overlays that float *above* it. Drop shadows are soft and ambient, never hard-edged.

### Shadow Vocabulary
- **Rest** (`0 2px 8px -2px rgb(0 0 0 / 0.16)`): the composer and grounded cards at rest.
- **Menu** (`0 0 20px rgb(0 0 0 / 0.28)`): popovers and dropdowns.
- **Sheet** (`0 16px 40px rgb(0 0 0 / 0.42)`): modals and large sheets.
- **Lift** (deep ambient + coral-tinted glow at 50%): a card rising under the pointer or a drag.
- **Composer focus** (`--shadow-composer-focus`: contact shadow + tight coral rim): Claw Arcade's focused and drag-over composer (§7). In the default look a focused composer keeps its Rest shadow. Sized to the 16px page gutter the composer sits in — docked above the page bottom, and beside the rail at the default `full` width — because the page box clips anything past it, and a shadow that outruns its gutter is cut into a hard line.

### Named Rules
**The Glow-Is-Alive Rule.** Coral-tinted glow (`--glow`) appears only when the agent is active or, in Claw Arcade (§7), the user is focused into an input — never as static decoration.

## 5. Components

Controls are pill-shaped and tonal; every interactive component has default, hover, focus-visible, active/pressed, disabled, and (where async) loading states.

### Buttons
- **Shape:** pill (9999px); `squircle` (34px) opt-in for large-sheet contexts.
- **Primary:** coral fill (#ff6b5b), deep-ember ink, 40px height, 20px side padding, label at wght 470.
- **Hover / Press:** hover and press both lift the fill to #ff9a86, at once, with no scale: a button is pressed many times an hour, and one that shrinks and grows under the pointer only wobbles. In Claw Arcade, solid buttons also carry a pointer-tracking radial sheen (§7).
- **Secondary:** tonal fill (#282a2c → #333537 on hover). **Ghost:** transparent → tonal hover. **Danger:** #f55e57 with white ink.
- **Loading:** label cross-fades out, centered spinner in; width preserved.
- **Disabled:** 40% opacity, pointer-events off.
- **Focus:** the global 2px `:focus-visible` outline, 2px outside the button. On every solid fill (Button's Primary and Danger, a `filled` icon button, the header's Primary and Danger actions, and an app's primary header action) it is drawn in the ink (`--color-on-surface`), not coral: a coral ring on a coral fill reads as part of the button. The page shows between the ring and the fill, and the ring clears 3:1 against every page surface in every scheme and theme (`design/solidButtonFocusRing.test.ts`).

### Icon Buttons
- Round pill hit area (40px), idle at ink-variant, hover fills surface-high and brightens to ink; `filled` variant uses solid coral, with the ink focus ring of a solid button (Buttons, Focus). Optional icon-morph crossfade and one-shot success "bloom" pop (scales with bounciness).

### Cards / Containers
- **Corner Style:** 16px (`lg`) for cards, 28px (`xl`) and squircle for sheets.
- **Background:** surface-container (#1e1f20) on canvas; nested content steps one ramp tier, never a nested card.
- **Shadow Strategy:** tonal difference first; rest shadow only when the card floats (composer).
- **Border:** none or 1px #444746 hairline.

### Inputs / Fields
- Tonal fill (surface-low/container), 1px hairline stroke, 8–12px radius, ink text with ink-low placeholder (metadata only — placeholders never carry required information).
- **Focus:** ordinary inputs get the global 2px coral `:focus-visible` outline. In the default look the composer's focus is a plain 2px coral ring: no animated gradient ring, no rim glow, no spring rise. Claw Arcade adds them (§7).

### Navigation
- **NavRail:** drag-resizable side rail (196px default, 64px icon-only collapsed) on rail tone (#1f1f1f); active item carries a tonal fill + coral accent; mobile becomes a 264px overlay drawer with scrim.

### Status line
- **The strip:** 24px along the window's bottom edge, under the rail and the work surface, on the canvas with a hairline above; caption type in Ink Low. No accent, no glow, nothing that moves.
- **The connection:** a still 8px dot in its state's semantic tone (OK Green connected, Warn Orange connecting, Danger Red disconnected), alone while connected, with the state in words beside it otherwise. It never pulses: a resting animation says nothing the colour does not.
- **Its banner:** a lost connection that lasts escalates to a glass-sky notice over the page, below the shell corners, with a Danger Red glyph and body ink, so nothing moves under the owner when it comes and goes.

### Motion (component-level doctrine)
- **Spatial springs** (stiffness 200–800, visible overshoot) for position/scale; **effects curve** (0.2s, `cubic-bezier(0.2,0,0,1)`, critically damped) for opacity/color.
- **Four named presets — `physics.snappy` / `smooth` / `fluid` / `playful`** — are the whole spring vocabulary; `playful` marks the ~3–4 sanctioned personality moments (menu open, success bloom). All four interpolate toward calm via the user's bounciness setting and all four collapse to an instant swap under reduced motion. Full author guide, including the budget and the gesture helpers: [`docs/design/motion.md`](../docs/design/motion.md).
- **Later work, with the buddy's play layer** ([#3894](https://github.com/PersonalClaw/PersonalClaw/issues/3894)): the desktop buddy's gaze follow becomes a sanctioned personality moment. It moves on `gaze`, the one spring the buddy adds, minted in `motion.ts` beside the four presets and following the bounciness dial and reduced motion as they do ([`docs/design/motion.md` §9](../docs/design/motion.md#9-the-desktop-buddys-timings)).

## 6. Do's and Don'ts

### Do:
- **Do** route every color, radius, spacing, and shadow through the token system (`--color-*`, `--radius-*`, `--spacing-*`); the whole app must survive a scheme retint and the density/roundness sliders.
- **Do** use the surface ramp for hierarchy: one tonal step per nesting level, hairline dividers at #444746.
- **Do** keep body text at #e3e3e3 (≥4.5:1 on all surface tiers) and reserve #9a9b9c for metadata.
- **Do** scale every playful moment through `expr()`/bounciness and provide a reduced-motion path — springs become instant, sheens and halos disappear.
- **Do** express emphasis with fractional variable weight (wght 470–500), and put the coral gradient only where the agent is alive.

### Don't:
- **Don't** ship hacker terminal cosplay — no green-on-black palettes, scanlines, CRT effects, or ASCII chrome (PRODUCT.md's named anti-reference); `data-ui="cli"` may tighten layout but never changes the palette.
- **Don't** hardcode hex, px radii, or spacing in components; no `#ff6b5b` literals outside the token/gradient modules.
- **Don't** use colored side-stripes (`border-left` > 1px), gradient text, nested cards, or glassmorphism outside the sanctioned glass-sky overlay material.
- **Don't** bounce opacity or color — overshoot belongs to spatial properties only; effects are critically damped.
- **Don't** put coral on inactive states, decorative fills, or more than ~10% of a task screen; semantic colors (green/orange/red/blue) never decorate.
- **Don't** use uppercase tracked eyebrows or display sizes inside panels — weight steps carry hierarchy.

## 7. Identities

An identity (`src/design/personalities.ts`) is a theme that carries behaviour: a base scheme from `SCHEMES`, a closed behaviour block, and the dials it seeds into the owner's appearance overrides when it is switched on. It never carries colours of its own, so every identity's palette is a contrast-checked scheme. Each dial writes an existing token, the way its Settings control does, so the owner can dial it straight back.

### The five dials

**Decision: the backdrop is the fifth dial,** because an identity arrives with its backdrop. `PersonalityDials` stays a short, deliberate list rather than "any token": an identity brings a motion temperament, a dot glyph and its backdrop, and it still never resizes the type, recolours outside its base scheme or reaches the layout.

| Dial | Token | Sets |
|---|---|---|
| Expressiveness | `--expressiveness` | motion intensity, 0 refined to 1 bold |
| Bounciness | `--bounciness` | spring overshoot, 0 calm to 1 playful |
| Dot shape | `--dot-shape` | the glyph the backdrop paints |
| Dot pattern | `--dot-pattern` | the lattice the dots sit on |
| Backdrop | `--bg-style` | `none`, `still` (one frozen frame), `waves` (the animated field) or `glow` (light around the composer, no dots) |

| Identity | Base scheme | Expressiveness | Bounciness | Dot shape | Dot pattern | Backdrop (`--bg-style`) |
|---|---|---|---|---|---|---|
| PersonalClaw (default) | `coral` | token default | token default | token default | token default | `none`, the token default |
| Retro Terminal | `phosphor` | 0.25 | 0 | `square` | `grid` | `still` |
| Claw Arcade | `amber` | 1 | 1 | `sparkle` | `diamond` | `waves` |

- `--bg-style` defaults to `none`. The default identity declares no dial: it gets `none` from the token default.
- A backdrop the owner picked by hand wins over the default. Switching identity seeds or resets the backdrop exactly like the other four dials, so a hand-picked backdrop lasts until the next switch.
- `glow` keeps its meaning, and it is never a default.
- Under reduced motion a backdrop paints one still frame, or nothing for `none`.

### The default look

The default identity draws no decorative light. Coral is a solid accent on neutral surfaces, with no ambient glow, no particles and no sheen. The composer's focus is a plain 2px coral ring, with no animated gradient ring, no rim glow and no spring rise. The agent shows it is working through the agent orb at the head of its reply ([`docs/design/agent-orb.md`](../docs/design/agent-orb.md)), never through light around the composer. Retro Terminal keeps the same rules over its still field, which is painted once and has no halo.

### Claw Arcade

Claw Arcade is the one identity that keeps the light, all of it as decoration. The working signal is still the orb.

- **The wave field** (`waves`), which pauses whenever nobody can see it: in a hidden tab, and when its stage is offscreen. While another app has the focus it keeps moving, at 30 fps.
- **The halo:** the wide glow around the composer (`ui/DotGlow`), its bloom, and the focus aura that gathers close around the composer while it has the caret.
- **The composer's focus:** the animated 4-stop gradient ring (coral spectrum + highlight stop), the tight coral rim of `--shadow-composer-focus`, and the spring rise.
- **The sheen:** a pointer-tracking radial sheen on solid buttons.

**The Contained-Light Rule.** The wide glow around the composer is the halo (`ui/DotGlow`), and it fades to nothing before every edge of the column it lights. A glow never ends in a line: not at the rail, not at the page bottom, not at a docked panel. Light also needs a surface to come from — a glow with no emitter reads as a stain behind the content, so there is one light, and it is the composer's.
