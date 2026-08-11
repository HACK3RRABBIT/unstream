# How Unstream is built, and why

The decisions in here are load-bearing: things that look like mistakes until you know the reason, and things that will quietly break if changed without one. The [README](../README.md) covers what the project does and how to run it.

- [The shape of it](#the-shape-of-it)
- [Farsi first, with a language layer](#farsi-only)
- [One self-hosted typeface](#typeface)
- [Which digits a number gets](#digits)
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
                                                      ├─ downloader.py  find audio → encode → tags
                                                      ├─ jobs.py        thread pool + progress + sweeper
                                                      ├─ limits.py      per-caller budgets
                                                      └─ analytics.py   SQLite counters + /admin
```

nginx in the frontend container serves the built app **and** proxies `/api` to the backend over the internal network. Same origin, so there is no CORS layer and no second domain.

Every metadata provider is public and keyless — no account, no API key, nothing that can be revoked or start charging per call. That constraint is why there is no Spotify Web API integration: since 2025 it requires the app owner to hold an active Premium subscription. Spotify links are read from the public embed pages instead.

Jobs live in memory, not a database. A restart loses in-flight progress and that is an accepted trade: the files on disk are the durable artifact, and a queue would be a second stateful service for a project whose premise is that it needs none.

<a id="farsi-only"></a>

## Farsi first, with a language layer

The frontend targets Persian speakers, and Farsi is still the default — `UNSTREAM_DEFAULT_LOCALE` decides what a first-time visitor gets, and unset means `fa`. What changed is that the copy is no longer welded into the components: each language is one dictionary under `frontend/src/lib/locales/`, and a picker in the header switches between them at run time, direction included.

This reverses an earlier decision ("no i18n framework, no strings file, no language switcher", on the grounds that a toggle doubles the copy surface for a single-audience app). The doubling is real; what bought it back is that the *dictionary is the type*. `Messages` is derived from `locales/en.ts`, so a language that omits a key — or gets a counting function's arity wrong — fails `tsc`, not the page. There is no framework and no dependency: a context, a plain object, and functions where a string needs a number.

- **Adding a language** is two edits: copy `locales/en.ts`, translate it, and add a line to `LOCALES` in `lib/i18n.tsx`. Nothing else in the app enumerates locales.
- **Direction comes from the locale; digits do not.** `dir` drives `<html dir>` and everything logical (`ps-`/`pe-`/`ms-`/`start-`/`end-`) follows it. Digits are decided per string instead — see [the digit rule](#digits) — because content and chrome mix inside one list.
- **Plural rules live in the locale.** Persian does not agree a noun after a numeral and English does, so counted phrases are functions (`trackCount(n)`) rather than one shared pluraliser that has to know about every language.
- **Copy that wraps an element** is split into `…Before` / `…After` halves. Word order around a `<kbd>` or a highlighted query is the translator's problem, not the component's.
- The register for Farsi is informal-but-clean spoken Persian (محاوره‌ی مرتب): spoken-register verbs, English loanwords in Persian script where they are the natural word («دانلود»، «پلی‌لیست»), no slang. Error and destructive-action copy is one notch calmer than the rest.
- **The hero `h1` and all `<head>` metadata** (title, meta description, OG/Twitter, JSON-LD, manifest) use neutral *written* Persian instead, because those surfaces target organic search and Persian search queries are written-register («دانلود موزیک», not «موزیک دانلود کن»). Everything rendered in-app below the h1 stays محاوره. The head is re-applied from the active dictionary on every switch (`applyDocumentLocale`), so a shared link carries the language its sharer was reading; a crawler that does not run JavaScript still sees `index.html`'s static copy, which is the default language's.
- The brand is **«آنستریم»** in Farsi copy and **"Unstream"** in English — both live in `app.name`, so no component spells either.
- **Backend `detail` strings are rewritten client-side**, per locale, in `apiError` (`frontend/src/lib/api.ts`). The wire stays terse English; each dictionary supplies the sentence. **A new error string in the backend needs an entry there**, or callers fall through to the generic per-status message.
- Backend-composed result *subtitles* ("5 releases", "by X · 40 tracks") go through each locale's `subtitle(part)` at **render** time, not when the response lands — that is what lets a language switch re-translate results already on screen without refetching. **New subtitle phrases added in the backend must be added to those maps**, or they reach the UI in English.
- The `/admin` dashboard is English and LTR regardless of the picker. It governs the product, and that page is not the product: it is owner-facing and its screenshots are meant to travel.

`CONTEXT.md` fixes the canonical Farsi rendering of each domain term. Copy changes should agree with it.

<a id="typeface"></a>

## One self-hosted typeface

A single self-hosted Persian typeface serves both `--font-display` and `--font-sans`; heading contrast comes from weight, not family. Self-hosting — preload, `font-display: swap`, a metric-tuned fallback — exists to make font loading effectively instant, so reintroducing any third-party font request defeats the point.

Both cuts of it ship. Which one an element gets is decided by [the digit rule](#digits), not by the document's language.

The font is **Vazirmatn** ([SIL OFL 1.1](https://github.com/rastikerdar/vazirmatn), by Saber Rastikerdar), in its plain and FD cuts. It replaced Peyda FaNum, which is a commercial font from fontiran.com — survivable while the repo was private, a licensing problem the moment open-sourcing came up, since publishing would have handed a paid font to everyone who cloned it. Estedad was the other candidate and lost on mechanics: its releases ship no FD build, only a generator script. `OFL.txt` travels with the fonts because the licence requires it.

- Two families, `Vazirmatn` (plain, the document default) and `Vazirmatn FD` (Farsi digits, opt-in via the `font-fa` utility). Same design and same metrics — digits are the only difference — so this is one typeface in two cuts, and the single-family rule above still holds.
- Four weights each (400/500/600/700), which is everything the UI uses. **A new weight class needs adding to both families** and preloading, or it silently falls back for one of them.
- Five files are preloaded: the plain cut's four weights, which render almost everything, plus **`Vazirmatn-FD-Regular`**. The FD regular earns its place because Farsi is the default language — leaving it on demand meant every Persian title swapped digit shapes after first paint. The metrics match so nothing moves, but the shapes visibly change, and paying one font beats letting the default language flash. The remaining FD weights stay on demand. `sw.template.js` precaches the same five.
- **Preload `href`s must match the CSS `url()`s exactly**, or browsers download every font twice. `sw.template.js` is stricter still: `cache.addAll` rejects atomically, so one stale filename in its `SHELL` means the service worker never installs at all and nothing is cached.
- The fallback's `size-adjust` / `ascent-override` / `descent-override` are measured, not guessed: mean Persian-glyph advance against Tahoma for the size-adjust, then the font's hhea metrics (2100/1100 at upm 2048) divided by it. **Changing the typeface means re-measuring them**, or the no-layout-shift guarantee quietly stops holding.

<a id="digits"></a>

## Which digits a number gets

The rule: **a quantity the UI states gets the reader's digits; a name keeps its own.**

`۱۲ آهنگ` is Farsi speaking, so it is Persian. `24K Magic` is a title, `MP3` and `m4a` are names of things, and none of them become `۲۴K Magic` or `MP۳` just because the surrounding chrome is Farsi. Bitrates are quantities (`۳۲۰` in Farsi, `320` in English); file extensions are names and never converted.

Three mechanisms, in order of how much they know:

1. **Numbers the UI composes** — counts, indices, durations, percentages, ETAs — go through `app.num` in the dictionary. Farsi maps ASCII digits to Persian there; English is the identity. Numbers rendered inline in a component use `m.app.num(...)` for the same reason.
2. **Provider content** — track titles, artist and album names, a pasted query — gets `faNumerals(text)`, which returns the `font-fa` class when the *text itself* is Persian script. A Persian title's digits should be Persian and an English one's should not, and the two sit in the same list, so the string decides. This is the same reasoning as the `dir="auto"` on those nodes, and it belongs on exactly those nodes.
3. **Everything else** inherits the plain cut and is left alone.

This replaces an earlier arrangement where the FD cut was the document default and rewrote every digit on the page. That was one mechanism doing all three jobs, and it could not tell a count from a title: it is why "24K Magic" rendered as "۲۴K Magic". Note what it means for a *new* surface — a number you interpolate straight into JSX will come out in Latin digits in both languages. That is a bug in Farsi; route it through `m.app.num`.

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
