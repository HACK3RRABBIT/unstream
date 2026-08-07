# Self-hosted analytics, recorded server-side

Unstream counts its own usage. Events are written to a SQLite file on the `analytics` volume by `backend/app/analytics.py`, and read back through a token-gated `/admin` page in the existing React app. We rejected Plausible, Umami and GA alike: a hosted one costs money or an account, a self-hosted one is a second container and a second database for a project whose whole premise is that it needs neither.

Almost every event is recorded **server-side**, inside endpoints that already run — searches, link resolves, downloads started, and each track's outcome and duration. Only page views and a couple of browser-only moments (PWA install, the share sheet) come from a `sendBeacon` in `frontend/src/lib/analytics.ts`. So the numbers are not something an ad blocker can subtract, the page pays nothing to collect them, and the Telegram bot is counted the day it ships without writing any bot-side tracking — it sends `X-Unstream-Surface: telegram` and every event grows a surface.

## Consequences

- **No cookie, no raw IP, no consent banner.** A caller is `sha256(salt + ip + user-agent + day)`, truncated, with a random per-install salt kept in the database. The rotation is the point: it yields daily uniques and makes "returning visitor" **impossible to measure**. That metric is not missing by accident — do not add a durable id to get it back.
- **Search queries are stored as text.** This was a deliberate call: a top-searched leaderboard is the single most shareable number the project has. Queries are never joined to an address, only to that day's hash. If this ever stops feeling proportionate, drop the `label` on `search` events and the leaderboard goes with it.
- Writes go through a bounded queue drained by one thread, and are **dropped when it is full**. Analytics can lose events; it can never slow down or fail a download. Every `record()` call swallows its own errors, and an unwritable volume disables the whole subsystem rather than breaking startup.
- **`ADMIN_TOKEN` is the project's second secret**, after the bot's. Unset, `/api/admin/*` returns 503 — the dashboard is off by default, never accidentally public. Failed token attempts are rate-limited; successful polls are not.
- The dashboard is **English and LTR**, the only surface that is. ADR 0001 governs the product, and this page is not the product: it is owner-facing, its screenshots are meant to travel, and translating "median track time" into Farsi would add glossary the `CONTEXT.md` register does not cover.
- Rows are kept `ANALYTICS_RETENTION_DAYS` (90) and swept hourly by the same writer thread. Counters live in one process, exactly like `limits.py` — fine for the single container in `docker-compose.yml`, a rethink if the API is ever scaled out.
- The **`analytics` volume is the only copy of the history**. Unlike `downloads`, it is not disposable; losing it loses every number the project has ever had.
