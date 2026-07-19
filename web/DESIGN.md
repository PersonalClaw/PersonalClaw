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
  display:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "2.625rem"
    fontWeight: 280
    lineHeight: 1.14
  headline:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 380
    lineHeight: 1.17
  title:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 470
    lineHeight: 1.2
  body:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.33
  label:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "0.9375rem"
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

PersonalClaw's dashboard is a capable engine with a soft shell, working in a studio after hours. The room is dark and calm — near-black canvas (#0f0f0f), tonal surface layers instead of hard borders — and the machine's warmth shows through one coral voice (#ff6b5b) that glows, blooms, and sweeps through gradient accents wherever the agent is alive: the composer focus ring, the thinking pulse, the spark. Two influences are deliberately blended: a **neural-expressive** signature (fractional variable-font weights like `wght 280` display, ambient glow, gradient energy that reads as live intelligence) and **Google's playful expressive element language** (pill shapes, tonal containers, springy overshoot on press) — both re-tinted through PersonalClaw's own coral/terracotta identity rather than copied.

Controls are physical and friendly: pills by default, squircles for large sheets, spring-driven press/hover with earned overshoot. Personality is *budgeted and tunable* — a global expressiveness knob and bounciness slider scale every playful moment, and `prefers-reduced-motion` collapses all of it to crossfades. This system explicitly rejects hacker-terminal cosplay: no green-on-black, no scanlines, no readability sacrificed to look "technical." The optional `data-ui="cli"` density mode tightens spacing and squares corners as a utilitarian layout choice; it is not a costume.

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
- **Warm Amber** (#ffb454): coral's gradient partner — the tail of the brand spectrum (`#c85a48 → #ff6b5b → #ff9a7a → #ffb454`), used in the spark, bloom, and focus-ring sweeps. Rarely a fill on its own.

### Neutral
- **Night Canvas** (#0f0f0f): the app background — the studio's darkness.
- **Surface ramp** (#131314 → #1b1b1b → #1e1f20 → #282a2c → #333537): elevation by tone, not shadow. Panels, cards, hover fills, and the highest tier for pressed/selected chrome.
- **Ink** (#e3e3e3): primary text. **Ink Variant** (#c4c7c5): secondary text and idle icons. **Ink Low** (#9a9b9c): metadata only, never body copy.
- **Outline Variant** (#444746): hairline dividers and input strokes; full-strength outline (#8e918f) is reserved for focus/emphasis.

### Semantic
- **OK Green** (#0ebc5f), **Warn Orange** (#ff8d41), **Danger Red** (#f55e57), **Info Blue** (#4e8ff8) — status pills, alerts, destructive actions. Aliased as success/warning/error; never used decoratively.

### Named Rules
**The One Voice Rule.** Coral is the only accent allowed to mean "the agent" — selection, focus, primary action, live activity. If coral appears where nothing is active, actionable, or alive, remove it.
**The Tone-Not-Line Rule.** Depth and grouping come from the surface ramp; borders are 1px hairlines (#444746) or nothing. Never a colored side-stripe.

## 3. Typography

**Display/Body Font:** DM Sans (variable, 100–1000; system-ui fallback)
**Mono Font:** JetBrains Mono (variable; ui-monospace fallback)

**Character:** One warm rounded sans carries everything; the neural-expressive signature is *fractional variable weight* — big type gets lighter (display at `wght 280`), small type gets firmer (labels at `wght 470`), so hierarchy reads as confidence, not shouting.

### Hierarchy
Applied via `data-type` roles, not per-component sizes:
- **Display** (wght 280–360, 2rem–2.625rem, ~1.14): page heroes and empty-state headlines only.
- **Headline** (wght 350–470, 1.25rem–1.75rem): section and panel titles.
- **Title** (wght 470–500, 0.9375rem–1.25rem): card titles, list-row leads.
- **Body** (wght 400, 0.8125rem–1.0625rem, 65–75ch max for prose): default reading text at #e3e3e3.
- **Label** (wght 470, 0.8125rem–1.0625rem): buttons, chips, form labels — the same size as body, one weight step firmer.
- **Mono** (JetBrains Mono, 0.8125rem): code, terminal panes, IDs, and the `data-ui="cli"` density mode.

### Named Rules
**The Weight-First Rule.** Emphasis is a variable-weight step (400 → 470 → 500), never uppercase-with-tracking and never a new font.

## 4. Elevation

A hybrid model: tonal layering does the everyday work (the surface ramp IS elevation), while two named materials handle the extremes — **neumorphic ground** (soft dual shadow + faint inset highlight, no hard border: `--neu-extrude`) for controls that sit *in* the surface, and **glass sky** (frosted translucent overlay, 16px backdrop blur at 0.72 alpha, 1px inset top highlight) for overlays that float *above* it. Drop shadows are soft and ambient, never hard-edged.

### Shadow Vocabulary
- **Rest** (`0 2px 8px -2px rgb(0 0 0 / 0.16)`): the composer and grounded cards at rest.
- **Menu** (`0 0 20px rgb(0 0 0 / 0.28)`): popovers and dropdowns.
- **Sheet** (`0 16px 40px rgb(0 0 0 / 0.42)`): modals and large sheets.
- **Lift** (deep ambient + coral-tinted glow at 50%): the composer's focused state — the one shadow allowed to carry brand color.

### Named Rules
**The Glow-Is-Alive Rule.** Coral-tinted glow (`--glow`) appears only when the agent is active or the user is focused into an input — never as static decoration.

## 5. Components

Controls are pill-shaped, tonal, and springy; every interactive component has default, hover, focus-visible, active/pressed, disabled, and (where async) loading states.

### Buttons
- **Shape:** pill (9999px); `squircle` (34px) opt-in for large-sheet contexts.
- **Primary:** coral fill (#ff6b5b), deep-ember ink, 40px height, 20px side padding, label at wght 470.
- **Hover / Press:** hover lifts to #ff9a86 with a subtle spring scale-up (~1.025); press springs in (~0.95); solid buttons at high expressiveness carry a pointer-tracking radial sheen.
- **Secondary:** tonal fill (#282a2c → #333537 on hover). **Ghost:** transparent → tonal hover. **Danger:** #f55e57 with white ink.
- **Loading:** label cross-fades out, centered spinner in; width preserved.
- **Disabled:** 40% opacity, pointer-events off.

### Icon Buttons
- Round pill hit area (40px), idle at ink-variant, hover fills surface-high and brightens to ink; `filled` variant uses solid coral. Optional icon-morph crossfade and one-shot success "bloom" pop (scales with bounciness).

### Cards / Containers
- **Corner Style:** 16px (`lg`) for cards, 28px (`xl`) and squircle for sheets.
- **Background:** surface-container (#1e1f20) on canvas; nested content steps one ramp tier, never a nested card.
- **Shadow Strategy:** tonal difference first; rest shadow only when the card floats (composer).
- **Border:** none or 1px #444746 hairline.

### Inputs / Fields
- Tonal fill (surface-low/container), 1px hairline stroke, 8–12px radius, ink text with ink-low placeholder (metadata only — placeholders never carry required information).
- **Focus:** the composer earns the animated 4-stop gradient ring (coral spectrum + highlight stop); ordinary inputs get the global 2px coral `:focus-visible` outline.

### Navigation
- **NavRail:** drag-resizable side rail (196px default, 64px icon-only collapsed) on rail tone (#1f1f1f); active item carries a tonal fill + coral accent; mobile becomes a 264px overlay drawer with scrim.

### Motion (component-level doctrine)
- **Spatial springs** (stiffness 200–800, visible overshoot) for position/scale; **effects curve** (0.2s, `cubic-bezier(0.2,0,0,1)`, critically damped) for opacity/color.
- Named bounce tiers (subtle / playful / lift / settle) are the ~3–4 sanctioned personality moments (menu open, success bloom, press-release); all interpolate to calm via the user's bounciness setting, and all collapse under reduced motion.

## 6. Do's and Don'ts

### Do:
- **Do** route every color, radius, spacing, and shadow through the token system (`--color-*`, `--radius-*`, `--spacing-*`); the whole app must survive a scheme retint and the density/roundness sliders.
- **Do** use the surface ramp for hierarchy: one tonal step per nesting level, hairline dividers at #444746.
- **Do** keep body text at #e3e3e3 (≥4.5:1 on all surface tiers) and reserve #9a9b9c for metadata.
- **Do** scale every playful moment through `expr()`/bounciness and provide a reduced-motion path — springs become crossfades, sheens and halos disappear.
- **Do** express emphasis with fractional variable weight (wght 470–500), and put the coral gradient only where the agent is alive.

### Don't:
- **Don't** ship hacker terminal cosplay — no green-on-black palettes, scanlines, CRT effects, or ASCII chrome (PRODUCT.md's named anti-reference); `data-ui="cli"` may tighten layout but never changes the palette.
- **Don't** hardcode hex, px radii, or spacing in components; no `#ff6b5b` literals outside the token/gradient modules.
- **Don't** use colored side-stripes (`border-left` > 1px), gradient text, nested cards, or glassmorphism outside the sanctioned glass-sky overlay material.
- **Don't** bounce opacity or color — overshoot belongs to spatial properties only; effects are critically damped.
- **Don't** put coral on inactive states, decorative fills, or more than ~10% of a task screen; semantic colors (green/orange/red/blue) never decorate.
- **Don't** use uppercase tracked eyebrows or display sizes inside panels — weight steps carry hierarchy.
