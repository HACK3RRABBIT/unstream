# Unstream UX Uplift — Plan & Progress

## Round 2 — deep search + design system (done)

**Search now pages.** `/api/search` takes `page` and returns `{results, page, has_more}`.
Each provider slices its own page: Deezer via `index`, SoundCloud via `offset`, iTunes and
yt-dlp by over-fetching and dropping the head (neither exposes an offset). Per-page quotas
live next to each provider as `PAGE_QUOTA`. Results are merged, deduped, then sorted by
`relevance()` — a `SequenceMatcher` score against both the title and "title + artist",
weighted by source quality — so exact matches lead instead of whichever provider replied
first. Measured: ~130–160 results per page, 4–9s, **739 unique across 6 pages** for
"daft punk" (was ~70 total, unpaged).

- yt-dlp bows out past `MAX_DEPTH` — every page re-extracts from the top, so deep pages
  would eat the whole 20s budget and starve the catalog APIs.
- `dedup_key` ships on every result (search *and* artist endpoints). The backend dedupes
  within a page; the client reuses the key in `mergeResults()` to drop cross-page repeats.
  Measured 93 suppressed across 4 pages.
- **Paging is manual, by design.** Auto-loading on scroll was tried and removed: one page
  is a fan-out across four providers taking 4–9s, so scrolling spent real request budget
  on results the user never asked for. Now there is a "Load more <kind>" button and
  nothing fetches without a click.
- The button lives **only on the kind tabs**, never on "All". "All" is a summary of what's
  already loaded — every section there is capped at a preview count, so paging into it
  wouldn't visibly change anything. "Show all N" moves you into the category, where the
  button is. (This capping is also what made an `IntersectionObserver` unworkable: panel
  height barely changed between pages, so the sentinel stayed in view and re-fired.)

**Design system.** Tokens now carry the vocabulary instead of ad-hoc values:

- Radius collapsed from 6 values to 3 — `rounded-ctl` (8px) / `rounded-btn` (14px) /
  `rounded-panel` (20px), plus `rounded-full`.
- Type ramp replaces `text-[11px]` / `[13px]` / `[13.5px]` / `[15px]` with
  `text-micro` / `text-mini` / `text-body`.
- Easing tokens `--ease-out-expo` / `--ease-spring`; new `rise`, `sweep`, `breathe`
  animations; `.stagger` list entrance driven by a `--i` custom property (capped at 14 so
  page 5 of infinite scroll doesn't animate in late).
- Shadows dropped from covers in favour of `ring-1` hairlines; kept only on genuinely
  floating surfaces (toast, dock).
- Indeterminate stages (searching / tagging) now show a travelling `sweep` band instead of
  `animate-pulse`, which read as broken rather than working.

**Shared-link arrivals get their own mode.** Landing on `/?url=…` used to show the full
marketing hero while silently fetching, which read as the app searching on its own. When
the app bootstraps from `?url=` / `?artist=` / `?q=`, the hero is replaced by a "Shared
link" banner that names what's happening (`Opening what someone shared with you…` →
`Here's what someone shared with you`, or a failure headline if resolve errors), plus a
"Search for something else" action that clears the context, the stack and the query
string. Any user-driven search or paste drops the mode too. Because the mode hides the
search form, `/` and ⌘K leave the mode and focus once the form has mounted (ref + effect,
not a timeout).

**Feedback on every action**: toasts on queue-all / queue-selected / queue-one / quick
download (success *and* error), a copy-link button with an inline check state, live
"N found so far" count, a guided empty state, and hover/press states on every control.

Verified: `tsc --noEmit`, `npm run lint`, `npm run build` all clean (2 pre-existing
fast-refresh warnings). Live: paging + dedup through the Vite proxy, artist endpoint
`dedup_key`, and a full resolve → download → tagged-mp3 lifecycle.

Not verified: no in-browser visual pass — the Chrome extension wasn't connected.

---


Goal: elevate the app experience (UI feedback, animations, power features) + credit footer.
Status: IMPLEMENTED & VERIFIED (lint + build + live API check). Only optional manual browser
pass remains. Track: `[x]` done / `[ ]` todo.

## Done

- [x] **Footnote**: "Built by Amirali Beigi" → https://github.com/amiralibg, in footer of
  `frontend/src/App.tsx` (lime hover underline, matches existing footer style).
- [x] **Backend preview URLs** (`preview_url` on tracks, flows through `asdict` automatically):
  - `backend/app/models.py` — `Track.preview_url: str | None = None`
  - `backend/app/deezer.py` — `item.get("preview")` (verified live: works)
  - `backend/app/itunes.py` — `item.get("previewUrl")`
  - `backend/app/embed.py` — `_preview_url()` reads `audioPreview.url` (verified present on
    track embeds; read defensively for album/playlist trackList items)
  - SoundCloud / yt-dlp providers: no cheap preview URL → stay `None` (UI hides play button)
- [x] **Frontend animation foundation** — `frontend/src/index.css`:
  - Tailwind v4 `@theme` animations: `animate-fade-up`, `animate-pop`, `animate-eq`, `animate-toast-in`
  - `.shimmer` class for skeletons, `prefers-reduced-motion` kill-switch
- [x] **Preview player module** — `frontend/src/lib/preview.ts`: singleton `Audio`,
  `togglePreview(id, url)`, `usePlayingPreviewId()`, `usePreviewLoading()` (useSyncExternalStore)
- [x] **Toast system** — `frontend/src/lib/toast.tsx`: `ToastProvider` + `useToast().push(msg, kind)`,
  bottom-left stack, auto-dismiss 4.5s, success/error/info variants
- [x] **API types** — `Track.preview_url` added in `frontend/src/lib/api.ts`
- [x] **ETA tracking** — `frontend/src/lib/downloads.tsx`: `DownloadEntry.etaSeconds`,
  computed in the poller from a ~45s sliding window of settled-count samples (`samplesRef`)
- [x] **TrackRow** (`frontend/src/components/TrackRow.tsx`):
  - Preview play/pause button (only when `track.preview_url`), spinner while buffering
  - Animated equalizer bars replace row index while that track previews
  - Selection checkbox (`selected` / `onToggleSelect` props, subtle until hover)
  - `animate-pop` on done/error state chips, `active:scale` on buttons
- [x] **CollectionView** (`frontend/src/components/CollectionView.tsx`):
  - Multi-select state (`Set<string>`), Select-all toolbar row, "Download N selected" + clear
  - `startSelected` mutation → `downloads.start(url, collection, ids)` (backend already supports `track_ids`)
- [x] **DownloadsDock**: ETA text (`~Xs left`), `animate-pop` check icons, panel `animate-fade-up`,
  FAB `active:scale-95`, `formatEta()` helper
- [x] **UrlForm**: `inputRef` prop, `active:scale` on submit
- [x] **CollectionSkeleton**: shimmer blocks (replaced `animate-pulse`)

## Remaining (in order)

All implementation done. Only manual browser smoke test left (optional).

### 1. App.tsx rewrite — `frontend/src/App.tsx` [x]

Done: ToastProvider > DownloadsProvider > Shell; `query` on search views; deep links
(?url= / ?artist= / ?q= bootstrap + replaceState mirror of top view); smart paste
(document paste listener, toast "Link detected — fetching…"); keyboard shortcuts
(⌘K/Ctrl+K focus+select, `/` focus, Esc blur-or-back); keyed `animate-fade-up` view
transitions; hero stagger (0/80/160ms); DownloadNotifier toasts on job finish.

### 2. PWA install [x]

Done: `public/sw.js` network-passthrough worker; registration in `main.tsx` (PROD only);
iOS/standalone meta in `index.html`; `"id": "/"` in manifest.

### 3. Verify [x]

- `npm run lint && npm run build` — passes (2 expected only-export-components warnings,
  same pattern as pre-existing downloads.tsx)
- Backend live checks (all passed):
  - `POST /api/resolve` Deezer album → tracks carry `preview_url`
  - `POST /api/resolve` Apple Music song URL → `preview_url` from iTunes `previewUrl`
  - `GET /api/search?q=…` → 4-source fan-out (deezer/itunes/soundcloud/youtube)
  - Full job lifecycle: `POST /api/download` → polling showed
    downloading(0→13%) → tagging → done; file endpoint returned a tagged
    192 kbps ID3v2.4 mp3 (5.3 MB)
- Frontend dev-server checks: `/sw.js` (text/javascript) and `/manifest.webmanifest`
  (application/manifest+json) served; `/api` proxy works; deep-link URL
  (`/?url=…`) serves the SPA shell for client bootstrap
- Post-verify polish: preview player now resumes a paused clip instead of restarting
  (`preview.ts` compares `el.src` before reloading)
- [ ] Optional manual pass in browser: play preview, select 2 tracks → "Download 2
  selected", paste link outside input, ⌘K, Esc, refresh with `?url=`, toast on finish,
  ETA in dock, install prompt (prod build).

## Design decisions to keep

- No new dependencies (toast/preview/SW all hand-rolled; project style is lean).
- Previews only where providers give them; missing `preview_url` → no button (no dead states).
- Animations respect `prefers-reduced-motion`; everything uses existing ink/lime palette.
- Toasts bottom-left (dock owns bottom-right).
- Deep links model the TOP of the view stack only (url/artist/q), not full stack.
