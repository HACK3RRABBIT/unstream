# Self-hosted Vazirmatn FD as the only typeface

Space Grotesk + Inter from Google Fonts were replaced by a single self-hosted Persian typeface for both `--font-display` and `--font-sans`; heading contrast comes from weight, not family. Self-hosting (preload + `font-display: swap` + a metric-tuned fallback, the `next/font` recipe) exists to make font loading effectively instant — reintroducing any third-party font request defeats the point.

The typeface must be a **Farsi-digit cut**: one that renders ASCII digits as Persian numerals (۱۲۳) from the font itself, so nothing in the app has to transliterate numbers.

## Superseding Peyda FaNum

This originally specified **Peyda FaNum**. Peyda is a commercial font — trademark of fontiran.com, designed by Naser Khadem — licensed per-use, and the ten `woff2` files were committed to the repo. That was survivable while the repository was private and became a licensing problem the moment open-sourcing was on the table: publishing the repo would have redistributed a paid font to everyone who cloned it.

It is replaced by **Vazirmatn FD** ([SIL OFL 1.1](https://github.com/rastikerdar/vazirmatn), by Saber Rastikerdar), which can be redistributed freely. `OFL.txt` ships alongside the `woff2` files in `frontend/public/fonts/` because the licence requires the copyright notice to travel with them.

**Estedad** was the other candidate and was rejected on mechanics, not looks: its releases ship no FD build, only a generator script, so using it would have meant committing a generated font and owning a build step.

## Consequences

- The **FD** cut renders ASCII digits as Persian numerals app-wide — durations, bitrates, and digits inside English song titles ("24K Magic" → "۲۴K Magic"). This is deliberate; do not swap in the plain Vazirmatn cut or wrap metadata in another font stack without a decision.
- Nine weights are registered via `@font-face`, but only the four the UI uses (400/500/600/700) are preloaded. If a new weight class appears in the design, add its preload. Peyda's tenth weight (ExtraBlack, 950) has no Vazirmatn equivalent and is gone.
- Vazirmatn's line box is taller than Peyda's — 1.5625em against 1.386em — so anything relying on the font's natural line height sits looser than it did.
- Preload `href`s must match the CSS `url()`s exactly, or browsers download the fonts twice.
- The fallback's `size-adjust` / `ascent-override` / `descent-override` are measured from the font, not guessed: mean Persian-glyph advance against Tahoma for the size-adjust, then Vazirmatn's hhea metrics (2100/1100 at upm 2048) divided by it. Changing the typeface again means re-measuring them, or the no-layout-shift guarantee quietly stops holding.
