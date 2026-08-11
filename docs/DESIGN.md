# How Unstream is built, and why

The decisions in here are load-bearing: things that look like mistakes until you know the reason, and things that will quietly break if changed without one. The [README](../README.md) covers what the project does and how to run it.

- [The shape of it](#the-shape-of-it)
- [Farsi only, no i18n layer](#farsi-only)
- [One self-hosted typeface](#typeface)
- [Analytics, recorded server-side](#analytics)
- [Run it yourself](#self-hosting)

## The shape of it

```
frontend (Vite + React + Tailwind)  ──proxy /api──▶  backend (FastAPI)
                                                      ├─ embed.py       Spotify URL → metadata
                                                      ├─ deezer.py      search, artists, Deezer URLs
                                                      ├─ itunes.py      search, Apple Music URLs
                                                      ├─ soundcloud.py  search + SoundCloud URLs
                                                      ├─ ytdlp.py       YouTube/SoundCloud URLs + search
                                                      ├─ lyrics.py      LRCLIB lyrics, cached in SQLite
                                                      ├─ downloader.py  find audio → encode → tags
                                                      ├─ jobs.py        thread pool + progress + sweeper
                                                      ├─ limits.py      per-caller budgets
                                                      └─ analytics.py   SQLite counters + /admin
```

nginx in the frontend container serves the built app **and** proxies `/api` to the backend over the internal network. Same origin, so there is no CORS layer and no second domain.

Every metadata provider is public and keyless — no account, no API key, nothing that can be revoked or start charging per call. That constraint is why there is no Spotify Web API integration: since 2025 it requires the app owner to hold an active Premium subscription. Spotify links are read from the public embed pages instead.

Lyrics follow the same rule. They come from **LRCLIB** (lrclib.net) first — a keyless catalog with plain text plus time-synced LRC — falling back to **Genius** when LRCLIB misses. The two-source split exists because LRCLIB keys Persian songs by romanized titles, so a catalog that hands the app Persian script can never match them; Genius's internal search API answers Persian queries and its song pages carry the lyrics. LRCLIB answers almost all English songs and Genius never hears about them. Genius is page-scraping, so it is the fragile member: if it starts answering 403s or changing its markup, the feature degrades to LRCLIB coverage — which is the same "a keyless source can rot" trade the rest of the app already makes with YouTube. Synced lyrics are fetched and cached but *not rendered* in v1 — the UI shows plain text. They are stored now so a karaoke-style view later is a frontend change only.

Lyrics are **nice-to-have, like cover art**: the lookup is wrapped so a failed fetch can never fail a download, and the SQLite cache (next to `analytics.db`) caches misses too so a track that has no lyrics doesn't get re-asked every download. Embedding into files is a user preference (the «متن آهنگ» toggle in the header) and is off-by-nothing — on by default, per job, captured at job start like quality.

Jobs live in memory, not a database. A restart loses in-flight progress and that is an accepted trade: the files on disk are the durable artifact, and a queue would be a second stateful service for a project whose premise is that it needs none.

<a id="farsi-only"></a>

## Farsi only, no i18n layer

The frontend targets Persian speakers. All UI copy is written directly in Farsi in the components — no i18n framework, no strings file, no language switcher. A bilingual toggle was rejected because it doubles the copy surface and adds a dependency to a ~10-component app with a single audience.

- The register is informal-but-clean spoken Persian (محاوره‌ی مرتب): spoken-register verbs, English loanwords in Persian script where they are the natural word («دانلود»، «پلی‌لیست»), no slang. Error and destructive-action copy is one notch calmer than the rest.
- **The hero `h1` and all `<head>` metadata** (title, meta description, OG/Twitter, JSON-LD, manifest) use neutral *written* Persian instead, because those surfaces target organic search and Persian search queries are written-register («دانلود موزیک», not «موزیک دانلود کن»). Everything rendered in-app below the h1 stays محاوره.
- The brand is **«آنستریم»** in all user-facing copy. Latin "Unstream" survives only in developer-facing places: the repo, package names, cache keys, code comments.
- **Backend and axios error strings surface in English on purpose** (`apiError` in `frontend/src/lib/api.ts`). Translating them client-side was considered and rejected. Do not "fix" this without a decision.
- Backend-composed result *subtitles* ("5 releases", "by X · 40 tracks") are the exception: `localizeSubtitle` rewrites them to Farsi at the API seam, so the backend response stays English. **New subtitle phrases added in the backend must be added to that map too**, or they reach the UI in English.
- The `/admin` dashboard is English and LTR — the only surface that is. This governs the product, and that page is not the product: it is owner-facing and its screenshots are meant to travel.

`CONTEXT.md` fixes the canonical Farsi rendering of each domain term. Copy changes should agree with it.

<a id="typeface"></a>

## One self-hosted typeface

A single self-hosted Persian typeface serves both `--font-display` and `--font-sans`; heading contrast comes from weight, not family. Self-hosting — preload, `font-display: swap`, a metric-tuned fallback — exists to make font loading effectively instant, so reintroducing any third-party font request defeats the point.

It must be a **Farsi-digit cut**: one that renders ASCII digits as Persian numerals (۱۲۳) from the font itself, so nothing in the app has to transliterate numbers.

The font is **Vazirmatn FD** ([SIL OFL 1.1](https://github.com/rastikerdar/vazirmatn), by Saber Rastikerdar). It replaced Peyda FaNum, which is a commercial font from fontiran.com — survivable while the repo was private, a licensing problem the moment open-sourcing came up, since publishing would have handed a paid font to everyone who cloned it. Estedad was the other candidate and lost on mechanics: its releases ship no FD build, only a generator script. `OFL.txt` travels with the fonts because the licence requires it.

- The FD cut turns digits Persian app-wide — durations, bitrates, and digits inside English song titles ("24K Magic" → "۲۴K Magic"). Deliberate. Do not swap in the plain cut without a decision.
- Nine weights are registered, four (400/500/600/700) preloaded. A new weight class in the design needs its preload added.
- **Preload `href`s must match the CSS `url()`s exactly**, or browsers download every font twice.
- The fallback's `size-adjust` / `ascent-override` / `descent-override` are measured, not guessed: mean Persian-glyph advance against Tahoma for the size-adjust, then the font's hhea metrics (2100/1100 at upm 2048) divided by it. **Changing the typeface means re-measuring them**, or the no-layout-shift guarantee quietly stops holding.

<a id="analytics"></a>

## Analytics, recorded server-side

Unstream counts its own usage into a SQLite file on the `analytics` volume, read back through a token-gated `/admin` page in the existing React app. Plausible, Umami and GA were all rejected: a hosted one costs money or an account, a self-hosted one is a second container and a second database for a project whose premise is that it needs neither.

Almost every event is recorded **server-side**, inside endpoints that already run. Only page views and a couple of browser-only moments come from a `sendBeacon`. So the numbers are not something an ad blocker can subtract, and the page pays nothing to collect them.

- **No cookie, no raw IP, no consent banner.** A caller is `sha256(salt + ip + user-agent + day)`, truncated, with a random per-install salt in the database. The rotation is the point: it gives daily uniques and makes "returning visitor" **impossible to measure**. That metric is not missing by accident — do not add a durable id to get it back.
- **Search queries are stored as text**, deliberately: a top-searched leaderboard is the most shareable number the project has. Queries are only ever joined to that day's hash. If it stops feeling proportionate, drop the `label` on `search` events and the leaderboard goes with it.
- Writes go through a bounded queue drained by one thread and are **dropped when it is full**. Analytics can lose events; it can never slow down or fail a download. Every `record()` swallows its own errors, and an unwritable volume disables the subsystem rather than breaking startup.
- **`ADMIN_TOKEN` is the project's only secret.** Unset, `/api/admin/*` returns 503 and the dashboard does not exist — it cannot accidentally end up public. Failed token attempts are rate-limited and that guard is not configurable.
- Rows are kept `ANALYTICS_RETENTION_DAYS` (90), swept hourly by the same writer thread. Counters live in one process, exactly like `limits.py` — fine for the single container, a rethink if the API is ever scaled out.
- The **`analytics` volume is the only copy of the history**. Unlike `downloads` it is not disposable; losing it loses every number the project has had. The README has the SQLite-safe backup command.

<a id="self-hosting"></a>

## Run it yourself

YouTube treats a datacenter address differently from a home one. From a VPS it answers `LOGIN_REQUIRED` at the playability check — before a proof-of-origin token is asked for and before a JS challenge exists to solve — so the defences the image carries cannot reach the point where they would help. Signing in with cookies moves yt-dlp onto the web clients where those defences *do* matter, which is why all three are needed together on a server and none are needed on a laptop.

That asymmetry is not a bug to fix here. It is why the same code works instantly at home and fails on a rented box, and it makes "run it yourself" the configuration where the project is at its best: no bot checks, no shared account carrying everyone's downloads, no operator in between. The legal shape agrees — distributing the software puts each person in charge of what they download, which is the position yt-dlp, spotDL and MeTube occupy.

Keeping a public instance working needs egress from a non-datacenter address: a residential or ISP proxy, which costs money. That is planned, not ruled out. It changes no code — `compose.dokploy.yml` already keeps the per-caller limits hardcoded on, and those limits stop being decorative the moment a public instance can actually serve people. The keyless rule is about *metadata providers*; renting an IP does not touch it.

Rejected along the way: a **split architecture** with the frontend on a VPS and a download worker at home — it gets a residential IP free and is strictly worse, since every stranger's download then traces to one home ISP account. And a **SoundCloud-only public demo**, which would work forever but advertises a downloader that mostly cannot download.

Consequences that outlive the decision:

- `docker-compose.yml` must work on a fresh clone with no `.env`, no external network and no file mounts. `compose.dokploy.yml` is the deployment. A change to one is not automatically right for the other.
- Defaults differ by audience on purpose: the **code's** defaults are public-safe (downloads expire, disk capped, limits tight) and the self-hosting compose file overrides them toward "this is my machine". Anything new with a limit attached needs a decision on both sides.
- The bot-check apparatus — deno, the challenge solver, the PO token provider — stays in the image even though most self-hosters never need it. It costs nothing idle, and the day YouTube starts asking a home address for a token is not a day to spend reading documentation.
