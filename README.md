<p align="center">
  <img src="frontend/public/icon-192.png" width="96" alt="Unstream app icon" />
</p>

<h1 align="center">Unstream</h1>

Educational project: paste a **Spotify / Deezer / Apple Music / YouTube / SoundCloud** track, album or playlist URL — or search every catalog at once — and get tagged mp3 files. No accounts, no API keys, nothing paid.

## How it actually works

Streaming services are DRM-protected, so nothing is downloaded from them directly. Instead:

1. **Metadata** comes from free, keyless providers:
   - **Spotify embed pages** (`open.spotify.com/embed/…`) — the public iframe widget ships full track lists as JSON; this is how most downloader websites work. Used for pasted Spotify URLs.
   - **Deezer public API** — keyless; powers search (songs, albums, **artists**, playlists), full artist discographies, and Deezer URLs.
   - **iTunes Search API** — keyless; adds Apple Music coverage to search and resolves `music.apple.com` URLs.
   - **SoundCloud web API** — the site's own `api-v2` endpoints, using a client id scraped from its public JS bundles (the same trick yt-dlp uses). Full search parity with soundcloud.com: tracks, people, albums and playlists; artist profiles resolve to their complete uploads. Go-only (DRM) tracks are filtered out.
   - **yt-dlp** — reads public YouTube and SoundCloud pages; resolves their URLs (videos, playlists, sets, profiles) and contributes YouTube search results.
   - **Spotify Web API** (optional) — used only if you configure credentials in `backend/.env`; falls back to embed scraping automatically on any API error.
   Search fans out to all of these in parallel and merges the results, deduped by name + artist.
2. **yt-dlp** finds the audio: tracks that already point at a YouTube/SoundCloud page download directly; everything else is searched (`ytsearch8:` on "artists - title") picking the result whose duration is closest to the catalog's (rejects live versions and hour-long mixes). Retries exclude broken uploads, and the last attempt searches SoundCloud instead of YouTube.
3. The best audio stream is downloaded and **ffmpeg** converts it to mp3 (192 kbps); if the converter leaves raw audio behind, a direct ffmpeg pass salvages it.
4. **mutagen** embeds ID3 tags + cover art into the file.

The FastAPI backend runs downloads on a small thread pool (3 concurrent) and exposes job progress; the React frontend polls it and shows per-track status. A background sweeper deletes job folders older than `DOWNLOADS_TTL_HOURS` (default 24) every hour.

```
frontend (Vite + React + Tailwind)  ──proxy /api──▶  backend (FastAPI)
                                                      ├─ spotify.py / embed.py   Spotify URL → metadata
                                                      ├─ deezer.py               search, artists, Deezer URLs
                                                      ├─ itunes.py               search, Apple Music URLs
                                                      ├─ ytdlp.py                YouTube/SoundCloud URLs + search
                                                      ├─ downloader.py           find audio → mp3 → tags
                                                      └─ jobs.py                 thread pool + progress + sweeper
```

## Setup

Prereqs: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, ffmpeg (`brew install ffmpeg`).

**1. Spotify credentials (optional — skip this)** — the app works without any account via embed scraping + Deezer. If you *do* want the official API path (more robust, official): create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and put the Client ID/Secret in `backend/.env` (copy `.env.example`). Note: since 2025 Spotify requires the app owner to have an **active Premium subscription**, otherwise all API calls are rejected — which is exactly why the no-auth path is the default.

**2. Backend**

```sh
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

**3. Frontend**

```sh
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>, paste any supported URL (or search by name), hit **Fetch**, then **Download all**. Files land in `backend/downloads/<job-id>/` and can be grabbed per-track or as a ZIP from the UI (auto-deleted after `DOWNLOADS_TTL_HOURS`, default 24).

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/search?q=` | Multi-source search → tracks, albums, artists, playlists |
| `GET /api/artist/{id}` | Artist page: top tracks + complete discography (Deezer) |
| `POST /api/resolve` `{url}` | Spotify/Deezer/Apple Music/YouTube/SoundCloud URL → track list |
| `POST /api/download` `{url, track_ids?}` | Start a download job, returns `job_id` |
| `GET /api/jobs/{id}` | Per-track status/progress (polled by the UI) |
| `GET /api/jobs/{id}/tracks/{tid}/file` | Download one finished mp3 |
| `GET /api/jobs/{id}/zip` | ZIP of all finished tracks |

## Deploying (Dokploy)

One compose stack, one domain. nginx in the frontend container serves the built app **and** proxies `/api` to the backend over the internal network — same origin, so no CORS and no separate API domain.

```
unstream.amiralibg.xyz ──▶ Traefik ──▶ frontend (nginx :80) ──/api──▶ api (uvicorn :8000)
                                                                        └─ downloads volume
```

1. Push this repo to GitHub.
2. Dokploy → **Create Service → Compose**, pick the repo, compose path `./docker-compose.yml`.
3. **Domains tab**: add `unstream.amiralibg.xyz` → service `frontend`, port `80`, HTTPS on (Let's Encrypt).
4. DNS: `A` record `unstream` → VPS IP.
5. (Optional) Environment tab: `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` for the official API path.

Local run of the same stack: `docker compose up --build` (add a port mapping override for `frontend`, or use Dokploy's network by creating it: `docker network create dokploy-network`).

## Notes

- Playlists must be public. The embed-page provider even reads Spotify's editorial playlists (which the official API blocks for new apps).
- The embed pages are an undocumented structure — Spotify can change them any time. If resolving suddenly breaks, that's the first place to look (`backend/app/embed.py`).
- On a VPS, YouTube sometimes bot-checks datacenter IPs ("Sign in to confirm you're not a bot"). If downloads start failing there while working locally, the fix is passing a cookies file to yt-dlp (`cookiefile` option) or updating yt-dlp (`uv lock --upgrade-package yt-dlp` + rebuild). Rebuild the image every month or two anyway — old yt-dlp versions stop working as YouTube changes.
- Old job folders are cleaned automatically after `DOWNLOADS_TTL_HOURS` (default 24, set it in the environment to change).
- Only download music you have the rights to. This project exists to learn the mechanics of APIs, media pipelines and background jobs.
