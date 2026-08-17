# Consumet — investigated, rejected for now

This documents why the anime download path is **not** being moved to Consumet
today, despite it being the originally-approved architecture change. The
investigation was done live (August 2026) and the verdict is environment-
specific — the reasons could change, so the provider registry stays modular
for a working source to be added later without a redesign.

## What was approved, and what happened

The plan was: AniList for discovery/metadata + a self-hosted Consumet sidecar
(`CONSUMET_BASE_URL=http://consumet:4000`) for episode sources, qualities and
subtitles, with provider order gogoanime → miruro → reanime and Nyaa as the
fallback. That self-host requires a Consumet build that can actually resolve a
working stream. It cannot, today:

| Component | Status (verified Aug 2026) |
| --------- | -------------------------- |
| `api.consumet.org` (public) | **Dead** — HTTP 451 (DMCA-takedown) on all endpoints. |
| `consumet/consumet.ts` (upstream repo) | **DMCA-blocked on GitHub** (March 2026, "dramacool" takedown) — cannot clone. |
| `solo12345689/consumet.ts` (maintained fork) | Builds cleanly (`npm install` → `tsc` → `dist/index.js`, bundles `@consumet/extensions` 1.8.9). But every anime provider it ships fails to serve a real stream today. |

## The three anime providers, live

Tested against the fork's fresh build with the same HTTP stack the app uses:

- **gogoanime** — `fetchEpisodeSources` returns *embed-page* URLs
  (`gogoanime.is` episode → `newplayer.php?…` → `megaplay.buzz/stream/…?autostart=true`).
  That is a **JS-heavy HTML player page**, not an m3u8. It exposed **no direct
  `.m3u8`**, **no quality metadata**, and **no subtitle track**. Replicating the
  player's JS to reach the real stream would be brittle reverse-engineering.
- **miruro** — `fetchAnimeInfo` (the step that produces episode source ids)
  returns **403** from its pipe mirrors (and the fork's own code fetches a
  random HTTP proxy from `api.proxyscrape.com` before each request — an
  unreliable, rate-limited dependency).
- **reanime** — `fetchEpisodeSources` returns **410 Gone**.

`api.consumet.org` answering 451 to a health probe is the same story at the API
level: the project is DMCA'd, not temporarily down.

## Decision

- **No Consumet sidecar, no provider code.** Forcing it in would mean shipping
  a provider that cannot return a stream — the exact "silently falling back to
  the old broken behavior" the user said to avoid. We report the failure
  instead (this doc) and keep the working chain (`nyaa,hianime`).
- **No reverse-engineering of JS player pages, no proxy-scraping workarounds,
  no bypasses of provider blocking.** All three are outside the "legitimate,
  reliable, authorized" bar this project holds.
- **No architecture change to the downloader.** The provider registry
  (`app/anime/providers.py`) already isolates each source behind the
  `EpisodeSource` / `EpisodeStream` protocol and is driven by the
  `ANIME_PROVIDER_ORDER` env — a working/authorized source can be added as a
  new provider later (JSON-tracker-based, a licensed vendor, an authorized
  streaming API) without touching `downloader.py` or `routes.py`.

## What *does* work today

The Nyaa torrent path (first in the chain) is the reliable route on a normal
VPS — see `TEST_VPS.md` for a validated 480p download over aria2c when the
torrent client is present, and the download-transport fix in this repo's
history for the current VPS. HiAnime (HLS scraper, second in the chain) is
unreachable from some datacenter IPs but works from home connections.