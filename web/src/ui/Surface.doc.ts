import type { UiDoc } from './uiDoc'

// Doc object for Surface (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// Surface.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'Surface',
  keywords: ['surface', 'card', 'panel', 'container', 'elevation', 'glass', 'tonal', 'ground', 'sky'],
  description:
    "The tonal surface primitive of PersonalClaw's elevation model. Two modes from one prop: default (glass=false) is the neumorphic GROUND — a tone step + soft shadow with no hard border, for content-bearing/permanent surfaces; glass is the frosted SKY overlay for transient/floating UI (menus, popovers, palettes). The backdrop-filter lives ONLY on the outermost glass overlay so nested blur can't recur, and falls back to a solid surface where unsupported or under reduced-motion.",
  props: [
    { name: 'children', description: 'The surface contents.' },
    { name: 'tone', description: "The GROUND tone step (ignored under glass): 'surface' / 'low' / 'container' (default) / 'high' — successive elevation shades." },
    { name: 'radius', description: "Corner radius: 'lg' (16px, default), 'md', 'xl' for cards, or 'squircle' for large sheets." },
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
    { name: 'glass', description: 'Switch from the neumorphic GROUND to the frosted glass SKY overlay — use ONLY on the outermost transient/floating surface (the sole place backdrop-filter belongs).' },
    { name: 'onClick', description: 'Optional click handler on the container.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Surface for any tonal container rather than hand-rolling bg + shadow classes — it encodes the GROUND/SKY elevation model in one prop.' },
    { guidance: true, description: 'Use glass ONLY on the outermost transient/floating overlay (menu, popover, palette); inner emphasis uses opacity/border, never a second backdrop-filter.' },
    { guidance: true, description: 'Pick radius by role — lg (default) for most surfaces, xl for cards, squircle for large sheets.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise); use tone/radius, not raw bg/shadow.' },
    { guidance: false, description: 'Do not expect a hover-lift from Surface — it is a pure static container; the liftable-card treatment lives on ListRow/TaskCard/AppCard/BentoCard.' },
  ],
  anatomy: ['div (tone bg + soft shadow GROUND, or frosted glass SKY overlay)', 'radius corner (lg / md / xl / squircle)'],
}

export default doc
