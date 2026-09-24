// Central identity/config — single source of truth for app naming.
// PersonalClaw is SELF-HOSTED and single-user: there is no service tier, no
// "Pro", no account billing, no marketing shorthand. Keep naming about the
// *workspace* and the *model*, not a product plan.
//
// DESIGN DOCTRINE for this rebuild: do NOT transcribe PersonalClaw's original
// layouts or pixel-copy Gemini. Design each surface from its actual purpose
// (a self-hosted personal agent) under NE principles — thoughtful layout,
// interaction, motion, and information hierarchy per page.

export const APP_NAME = 'PersonalClaw'

// The local operator's name is NOT hardcoded — it's captured once at first run
// and persisted client-side (see app/identity.tsx), since the self-hosted
// single-user backend has no user-profile entity.

// Models, agents, and modes are NOT hardcoded — they come live from the backend
// (see src/lib/api.ts + useComposerData). "Auto" is the implicit default the
// composer shows until a real model is chosen.
