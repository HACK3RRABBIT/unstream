# Farsi-only UI with no i18n layer

The Unstream frontend targets Persian speakers. All UI copy is written directly in Farsi in the components — there is no i18n framework, no strings file, and no language switcher. English copy was deliberately deleted (git history keeps it). We rejected a bilingual EN/FA toggle because it doubles the copy surface and adds an i18n dependency to a ~10-component app with a single audience.

## Consequences

- The copy register is informal-but-clean spoken Persian (محاوره‌ی مرتب): spoken-register verbs, English loanwords in Persian script where they are the natural word («دانلود», «پلی‌لیست»), no slang. Error and destructive-action copy is one notch calmer than the rest.
- The brand name stays Latin **"Unstream"** everywhere, including inside Farsi sentences.
- Backend and axios error strings (`apiError` in `frontend/src/lib/api.ts`) surface in English **on purpose** — translating or mapping them client-side was considered and rejected to keep scope to frontend copy. Do not "fix" this without a decision.
- Backend-composed result *subtitles* ("5 releases", "by X · 40 tracks") are the exception: they are rewritten to Farsi client-side by `localizeSubtitle` in `frontend/src/lib/api.ts`, at the API seam, so the backend response stays English for other clients. New subtitle phrases added in the backend must be added to that map too.
