# Farsi-only UI with no i18n layer

The Unstream frontend targets Persian speakers. All UI copy is written directly in Farsi in the components — there is no i18n framework, no strings file, and no language switcher. English copy was deliberately deleted (git history keeps it). We rejected a bilingual EN/FA toggle because it doubles the copy surface and adds an i18n dependency to a ~10-component app with a single audience.

## Consequences

- The copy register is informal-but-clean spoken Persian (محاوره‌ی مرتب): spoken-register verbs, English loanwords in Persian script where they are the natural word («دانلود», «پلی‌لیست»), no slang. Error and destructive-action copy is one notch calmer than the rest.
- **Amendment (2026-08-05):** the hero h1 and all `<head>` metadata (title, meta description, OG/Twitter, JSON-LD, manifest) use neutral *written* Persian instead of محاوره, because these surfaces target organic search and Persian search queries are written-register («دانلود موزیک», not «موزیک دانلود کن»). Everything rendered in-app below the h1 — subhead, buttons, toasts, empty states, the downloads dock — stays محاوره.
- The brand name is written **«آنستریم»** in all user-facing copy (UI, metadata, manifest). Latin "Unstream" survives only in developer-facing places: the repo, package names, cache keys, and code comments. (This reverses an earlier keep-it-Latin decision from the same session.)
- Backend and axios error strings (`apiError` in `frontend/src/lib/api.ts`) surface in English **on purpose** — translating or mapping them client-side was considered and rejected to keep scope to frontend copy. Do not "fix" this without a decision.
- Backend-composed result *subtitles* ("5 releases", "by X · 40 tracks") are the exception: they are rewritten to Farsi client-side by `localizeSubtitle` in `frontend/src/lib/api.ts`, at the API seam, so the backend response stays English for other clients. New subtitle phrases added in the backend must be added to that map too.
