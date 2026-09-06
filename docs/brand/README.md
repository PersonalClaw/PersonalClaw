# PersonalClaw brand assets

The PersonalClaw mark is a stylized **claw** painted in the brand's coral→amber gradient
(`#c85a48 → #ff6b5b → #ff9a7a → #ffb454`). The live app renders it theme-aware via
`web/src/ui/ClawMark.tsx`; the source vector is `web/public/claw.svg`.

| File | What it is | Where it's used |
|---|---|---|
| `personalclaw-mark.svg` | Vector mark, coral→amber gradient, transparent | source of truth for the mark |
| `personalclaw-mark.png` | 512×512 mark, transparent | README header logo |
| `avatar.png` | 1024×1024, cream claw on coral fill | **GitHub org avatar** (Settings → Profile) |
| `social-preview.png` | 1200×600 card | the **website's** `og:image`, served from the site repo at `/brand/social-preview.png` and declared at 1200×600 in its base layout |
| `social-preview-core.png` | 1280×640 card | **`PersonalClaw/PersonalClaw` social preview** (Settings → General → Social preview) |
| `social-preview-apps.png` | 1280×640 card | **`PersonalClaw/PersonalClawApps` social preview** (same slot, that repo) |

Regenerate the mark rasters from `desktop/icon.png` (the claw alpha mask) + the gradient
stops above; keep the gradient and geometry identical to `claw.svg` so every surface stays
consistent.

## The 1280×640 repo cards

`social_preview.mjs` renders both cards: `node docs/brand/social_preview.mjs`. It is not a
mock of the design system, it *is* the design system — the palette values carry their
[`web/DESIGN.md`](../../web/DESIGN.md) names in one `TOKENS` block, the typefaces are the
repo's own vendored variable fonts from `web/public/fonts/`, and the mark is the shipped
`web/public/claw.svg` inlined. Re-run it after a palette or wordmark change instead of
retouching a PNG.

Type treatment follows DESIGN.md § 3 and matches how the site hero already sets this exact
copy: the wordmark takes the display rung at a *light* fractional weight (`wght 320` — big
type gets lighter, the neural-expressive signature), the tagline sits smaller and firmer
(`wght 520`) in solid coral, body copy is `wght 400` at Ink Variant, and the chips are
Label-rung text in tonal pill containers with a hairline edge. Coral is the only accent on
the card (the One Voice Rule); the coral→amber spectrum appears only in the mark and the
top rule, never across text.

1280×640 is GitHub's recommended size for the repo Social-preview slot, which is why it
differs from the website's 1200×600 `og:image` — those are two different cards for two
different consumers, not a duplicate.

**Uploading is owner-only.** GitHub's Social-preview slot has no Git or API path; it is set
by hand at `Settings → General → Social preview` on each repo. These two PNGs are produced
and committed ready for that upload; nothing in CI can do it.
