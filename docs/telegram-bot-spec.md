# Telegram bot — agreed scope

Settled 2026-08-05 in a scoping session. This is the source of truth for building the bot; the architectural why-s live in `docs/adr/0003-telegram-bot-thin-client.md`, the Farsi vocabulary in `CONTEXT.md`. Read both before building.

## Shape

A Telegram **bot** that delivers music as audio messages into the chat — not a Mini App, no webview. Full feature parity with the web app, translated into Telegram-native forms.

## Interaction model

- Any plain **text message = search**; any supported **URL = link resolve** (Spotify / Deezer / Apple Music / YouTube / SoundCloud). No commands beyond `/start` and a settings entry point.
- Search results are **one morphing message**: a top row of type tabs (آهنگ / آلبوم / آرتیست / پلی‌لیست), defaulting to tracks. Tapping a tab, entity, page arrow, or back button **edits the same message** in place — the whole browse session lives in one message, no chat spam.
  - ~8 results per page, ◀️ ▶️ pagination.
  - Album/playlist → track list with per-track buttons + «دانلود همه».
  - Artist → top tracks + full discography, browsable.
- «دانلود همه» sends each track as its own audio message, sequentially. **No ZIP** (see ADR 0003).

## Downloads

- **Quality** («کیفیت»): persistent per-user setting («تنظیمات»), default **192**, options 128 / 192 / 320 / اورجینال. Never asked per download.
- **Progress**: one status message per job, edited in place as tracks complete (respect Telegram's ~1 edit/sec flood limit — edit on track completion, not on percent). Finished tracks stream in as audio messages as they complete. The status message ends as a summary naming any failed tracks. A single track collapses to "downloading…" → audio arrives.
- **`file_id` cache**: keyed by (provider + track id, quality). On hit, resend instantly via `file_id` — no pipeline run. On miss, run the job, upload, store the returned `file_id`.
- **Delivery format**: mp3 and m4a → `sendAudio` with title/artist/cover so Telegram renders a proper music bubble; opus originals → `sendDocument` (see ADR 0003).

## Access & limits

- Open to anyone, no accounts, no allowlist.
- **One active job per Telegram user** — further requests get a calm "wait" notice (per ADR 0001's copy register).
- **100-track cap** per collection download.

## Architecture

- New `bot` service in `docker-compose.yml`: Python 3.12 + **aiogram**, **long polling** (outbound only — no public route, no Traefik change, not exposed like `api`).
- Talks to the existing backend over the internal Docker network via the same endpoints the frontend uses: `GET /api/search`, `GET /api/artist/{id}`, `POST /api/resolve`, `POST /api/download`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/tracks/{tid}/file`. Track metadata (title/artist/cover) for the audio message comes from the jobs payload.
- Own **SQLite volume** for user settings + `file_id` cache. The backend and its `downloads` volume are untouched.
- `BOT_TOKEN` env var on the `bot` service — the project's first secret. Add a footnote to the README's "no keys" claim.

## Copy

Farsi-only, same register and canonical terms as the web app (`CONTEXT.md`, ADR 0001). Brand name stays Latin "Unstream". New surface, same voice.

## Out of scope (deliberate)

- Inline mode (`@bot query` in other chats) — fights pipeline latency; revisit later at most.
- ZIP delivery, webhook mode, allowlists/approval gates, per-download quality prompts, self-hosted Bot API server (>50 MB uploads).
