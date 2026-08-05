# Self-hosted Peyda FaNum as the only typeface

Space Grotesk + Inter from Google Fonts were replaced by self-hosted **Peyda FaNum** for both `--font-display` and `--font-sans`; heading contrast comes from weight, not family. Self-hosting (preload + `font-display: swap` + a metric-tuned fallback, the `next/font` recipe) exists to make font loading effectively instant — reintroducing any third-party font request defeats the point.

## Consequences

- The **FaNum** cut renders ASCII digits as Persian numerals (۱۲۳) automatically, app-wide — durations, bitrates, and even digits inside English song titles ("24K Magic" → "۲۴K Magic"). This is deliberate; do not swap in the Latin-digit cut or wrap metadata in another font stack without a decision.
- All 10 weights are registered via `@font-face`, but only the four weights the UI uses (400/500/600/700) are preloaded. If a new weight class appears in the design, add its preload.
- Preload `href`s must match the CSS `url()`s exactly, or browsers download the fonts twice.
