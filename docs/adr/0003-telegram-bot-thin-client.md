# Telegram bot as a separate thin-client service

Unstream gets a Telegram bot with full feature parity to the web app. It runs as its own service in the compose stack (Python/aiogram, long polling — no webhook, no public route) and talks to the existing FastAPI over the internal network using the same `/api` endpoints the React frontend uses. The download pipeline stays untouched; the bot is a second client, not a second implementation. We rejected embedding the bot in the FastAPI process (couples lifecycles, blurs the boundary) and webhook mode (pays TLS/routing plumbing now for throughput we don't need).

## Consequences

- The bot keeps its own SQLite volume for per-user settings and a **`file_id` cache** keyed by (track identity, quality): the first requester pays the full yt-dlp/ffmpeg cost, later requesters get Telegram's stored copy instantly. A cached track is frozen to whichever source upload was picked first — refreshing it means flushing the cache entry.
- The web app's ZIP download is **deliberately not ported**. "دانلود همه" sends each track as its own audio message — Telegram's native form of the same capability. A ZIP would be an unplayable document and would break the Bot API's 50 MB upload cap on exactly the biggest albums. Do not "add back" ZIP without a decision.
- "Original"-quality opus files (remuxed from webm) are sent as *documents*, not audio messages — Telegram mangles opus in the music-player bubble, and transcoding would violate what «اورجینال» means. m4a originals and all mp3s go as proper audio messages.
- `BOT_TOKEN` is the project's first secret. The README's "no accounts, no API keys" claim now carries a bot-shaped asterisk.
- The bot is open to anyone but throttled: one active job per user, 100-track cap per collection. This protects the backend's shared 3-thread pool; the web app remains uncapped.
