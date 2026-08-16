# How Unstream is built, and why

The decisions in here are load-bearing: things that look like mistakes until you know the reason, and things that will quietly break if changed without one. The [README](../README.md) covers what the project does and how to run it.

- [The shape of it](#the-shape-of-it)
- [Anime download pipeline](#anime-download-pipeline)
- [Farsi first, with a language layer](#farsi-only)
- [One self-hosted typeface per script](#typeface)
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
                                                      ├─ lyrics.py      lyrics, 3 sources, cached in SQLite
                                                      ├─ downloader.py  find audio → encode → tags
                                                      ├─ jobs.py        thread pool + progress + sweeper
                                                      ├─ limits.py      per-caller budgets
                                                      └─ analytics.py   SQLite counters + /admin
```

nginx in the frontend container serves the built app **and** proxies `/api` to the backend over the internal network. Same origin, so there is no CORS layer and no second domain.

Every metadata provider is public and keyless — no account, no API key, nothing that can be revoked or start charging per call. That constraint is why there is no Spotify Web API integration: since 2025 it requires the app owner to hold an active Premium subscription. Spotify links are read from the public embed pages instead.

Lyrics follow the same rule. They come from **LRCLIB** (lrclib.net) first — a keyless catalog with plain text plus time-synced LRC — falling back to **Genius** when LRCLIB misses. The two-source split exists because LRCLIB keys Persian songs by romanized titles, so a catalog that hands the app Persian script can never match them; Genius's internal search API answers Persian queries and its song pages carry the lyrics. LRCLIB answers almost all English songs and Genius never hears about them. Genius is page-scraping, so it is the fragile member: if it starts answering 403s or changing its markup, the feature degrades to LRCLIB coverage — which is the same "a keyless source can rot" trade the rest of the app already makes with YouTube. Synced lyrics are fetched and cached but _not rendered_ in v1 — the UI shows plain text. They are stored now so a karaoke-style view later is a frontend change only.

**Genius is currently answering 403 to everything** — internal API, public search and song pages alike, on every header shape tried. That is the predicted rot above, arrived. While it lasts, a Persian-script title has no source that can read it, and the coverage work below exists to route around that rather than wait.

It is kept rather than deleted because the block is very likely per-address: this project ships to be self-hosted, and `docs/DESIGN.md` already documents the same asymmetry for YouTube. Genius may answer fine from someone else's machine, and it is the only source that can read Persian script at all — so the fix is to stop _paying_ for a blocked source, not to remove it.

### Resting a source that is refusing us

A source that has failed `BREAKER_THRESHOLD` times in a row is skipped for `BREAKER_COOLDOWN`, then allowed one request through to see whether it is back. No restart, no configuration, no flag to remember to unset.

This is worth real money. Measured on a Persian album, per track:

|                      | before | after                          |
| -------------------- | ------ | ------------------------------ |
| first few tracks     | ~9.0 s | ~9.0 s (tripping the breakers) |
| every track after    | ~9.0 s | **~1.9 s**                     |
| downloading it again | ~9.0 s | **~0 s** (cached)              |

Two sources are being rested there, not one. Genius's 403 takes ~1.7 s to come back — a Cloudflare block page is not a fast refusal — and **lyrics.ovh does not answer Persian queries at all, it times out at the full `REQUEST_TIMEOUT`**, which made it the more expensive of the two. What is left is LRCLIB, which is the source doing real work.

Skipping a rested source still yields `unavailable`, never `absent`: a source we declined to ask might have had the song.

The outage cache is what makes the re-download free, and it is the reason `fetch` takes `force`. An outage is remembered for `UNAVAILABLE_TTL_MINUTES` so an album pays one lookup rather than one per track — but a person who has just been told "couldn't reach the sources" and presses retry is exactly the caller that cache must not apply to, or the button is a decoration. `refresh=1` on the endpoint sets it, and it also clears the breakers. It bypasses **only** a cached outage: a hit and a real miss are both real answers and are served from cache regardless.

A third source, **lyrics.ovh**, sits behind both. It is keyless like the others but is the only one whose answer cannot be _checked_: no duration, no album, no synced text — an artist/title pair in, a blob out. So it is last, it is refused below `MIN_PLAUSIBLE_CHARS` (its short answers are near-always truncated junk), and the sheet names its source in the footer. A wrong lyric from it is embedded permanently in a file, which is why it is ordered behind the two that can be verified rather than merged in with them.

### Telling "no lyrics" apart from "couldn't ask"

`fetch` returns `None` when the sources answered and none has the song; it raises `LyricsUnavailable` when they did not answer. The API turns that into `status: found | absent | unavailable`, and the UI gives `unavailable` its own copy and a retry.

These were one value until it was measured. With Genius blocked, every Persian lookup reported "this song has no lyrics" — in the sheet _and_ in the `lyrics_view` analytics event. The numbers that should have revealed the outage were the numbers concealing it, so a total source failure was indistinguishable from a catalog gap. **Do not collapse these two again.** `lyrics_embed` records the same three outcomes from the download path, which is where most lookups actually happen — an album asks once per track, while the sheet is opened one song at a time.

### Matching, and what does not work

LRCLIB is asked up to `MAX_VARIANTS` ways, because our own providers hand back several shapes of the same song: decorations to strip (`Levitating (feat. DaBaby)`), a joined artist list to reduce to its first credit, and mixed-script fields — iTunes returns `"Gole Yakh  گل یخ"` as one string. Where the artist is Persian script and the title is Latin (Deezer does this), the artist is dropped and the title searched alone — the only variant that can return a different artist's song of the same name, which is why it requires a duration to verify against and is skipped without one.

**Transliteration was tried and rejected.** It looks like the obvious fix for Persian coverage and it does not work: LRCLIB's search is exact, not fuzzy. Its own `Gole Yakh` returns zero results for `Gol Yakh`, and `Kourosh Yaghmaei` returns zero for `Kurosh Yaghmai`. A transliterator would have to reproduce one particular informal romanization letter for letter. Don't rebuild this.

An exact title match is **not** exempt from the duration check. It used to be, and a 355-second request for `Bohemian Rhapsody - Live Aid` came back with a 514-character fragment of a different recording at full confidence — which then got embedded in the file. `MAX_EXACT_DRIFT` is wider than `MAX_DURATION_DRIFT`, not absent.

Lyrics are **nice-to-have, like cover art**: the lookup is wrapped so a failed fetch can never fail a download, and the SQLite cache (next to `analytics.db`) caches misses too so a track that has no lyrics doesn't get re-asked every download. Misses are cached; `unavailable` never is. Embedding into files is a user preference (the «متن آهنگ» toggle, in the header on a wide screen and in the settings sheet on a narrow one) and is off-by-nothing — on by default, per job, captured at job start like quality.

Jobs live in memory, not a database. A restart loses in-flight progress and that is an accepted trade: the files on disk are the durable artifact, and a queue would be a second stateful service for a project whose premise is that it needs none.

### Stopping a download, and "stopping" a search

The two cancels in the UI are not the same mechanism, because only one of them has anything to cancel.

A **download** is ours: `jobs.py` owns the threads, so `POST /api/jobs/{id}/cancel` sets a flag the workers actually read — between retry attempts, and from inside yt-dlp's progress hook, which is the only place a transfer can be interrupted while bytes are moving. Three details are load-bearing. The cancellation travels as its own exception type, or the retry loop in `downloader.py` reads being called off as a broken upload and goes looking for three more; the status is written by `cancel()` rather than left to the workers, because a track stuck in a provider search can take seconds to notice and a button with no visible effect for that long reads as broken; and anything a worker is still holding when it does notice is deleted, files included, so the counts the job reported stay true. Tracks that had already finished keep their files — cancelling an album halfway is "stop here", not "undo".

A **search** is not ours. It fans out to four providers inside one synchronous request, and there is no handle to call that off — so cancelling drops our end of it and gives the person their page back, which is the whole of what they were asking for. The work finishes into a response nobody reads. Both wear the same word in the UI («لغو», "stop") because the difference is ours, not theirs: what someone means by cancelling is that they want the screen back.

## Anime download pipeline

An episode is a `Track` with `media="video"` whose `source_url` is a synthetic
plan — `anime://<provider>/<animeId>/<season>/<episode>` — built from AniList
metadata by `anime/routes.py`. The batch goes through the same `jobs` machinery
as music (thread pool, progress, cancel, ZIP, sweeper), so the download dock
renders anime jobs unchanged. The difference downstream is
`anime/downloader.py#download_video_track()`, the video pipeline.

### Provider chain and strict quality

`download_video_track` walks the **provider chain** (`ANIME_PROVIDER_ORDER`,
default `nyaa,hianime`). Each provider is asked for the episode at the **same
requested resolution**; a failing provider is skipped and the next is asked at
that same resolution — a request is never silently re-served at another height.
Two kinds of provider:

- **nyaa** (self-downloading, `streams_hls=False`) — torrents via aria2c (or
  libtorrent). Works from any IP; first in the chain.
- **hianime** (HLS, `streams_hls=True`) — yt-dlp with a strict format selector.
  Bot-blocked from datacenter IPs, so Nyaa carries most real downloads.

Explicit resolutions (480/720/1080) are **invariants**: only releases whose
title clearly claims that resolution are eligible, and after the file exists
`_probe_height()` (ffprobe — the source of truth, never the filename or the
request) is checked against it by `_check_served_quality()`. A release labeled
480p that is really 720p raises `QualityUnavailable` and the chain continues at
the same request. `original` accepts whatever the source released and is never
checked. `served_quality` in the job API is that probed height — recorded even
on a failed track, so the requested-vs-served truth survives an error.

### Error aggregation: when the quality message wins

`QualityUnavailable` means "the episode exists, but not at the requested
resolution" — distinct from `ProviderError` ("the provider itself is
unreachable"). The downloader remembers every `QualityUnavailable` it saw; if
any provider could evaluate the resolution and said it was unavailable, the
final error is the clean `Requested quality Xp is unavailable for this
episode.` — even when the last provider to fail was an unrelated `ProviderError`
(rot, unreachable). Only when NO provider could evaluate the resolution do the
real technical errors surface, as `Failed to download episode after trying all
providers: …`. A successful download always wins.

### Nyaa release classification

`nyaa._parse_rows` classifies each search row as a **single** or a **batch**:

- a multi-episode **range** (`001-079`, `001 ~ 079`, `E01–E06`) covering the
  requested episode (first/middle/last) → batch;
- an explicit `[BATCH]` label → batch;
- a **space/comma-separated episode list** (`001 002 003`, `E01 E02`) → batch,
  via `_multi_episode_space_list` — deliberately conservative: only adjacent,
  standalone **zero-padded** episode forms or `E`/`EP` markers count, so
  `Show 01 720p` (episode + resolution) stays a single. A false positive would
  break a normal single-episode download; a false negative only means the batch
  falls through to the next provider, so it errs toward single.

An episode number is "real" only when it appears with a delimiter or marker —
never as a bare digit that could be a year, a resolution, or a hash.

### Batch extraction

A multi-episode torrent is **never** whole-downloaded as if it were a single
episode. Batches go through `_download_batch_episode`: fetch the `.torrent`,
list its files, match the requested episode by filename (`_file_is_episode`),
and download only that file via aria2 `--select-file` (or libtorrent file
priorities). If the episode can't be extracted, the download fails cleanly so
the chain can try the next provider. Two aria2 facts make this fiddly, and both
were exposed by a real VPS download (Montana Jones `01, 02, 03`, episode 2)
rather than the hermetic tests, which mock the aria2 boundary:

- **aria2 1.37 lists files as two lines** — `idx|path` then `|length` — not
  `idx|path|length` on one. The parser accepts a line whose first pipe-field is
  a numeric file index and validates the path with `_file_is_episode`; the
  length-only line is ignored.
- **aria2 preallocates every file** in a batch, so unselected files sit in the
  workdir as full-size, zero-filled look-alikes — "largest file" is wrong for a
  selected-file download. The batch path returns the **exact `--select-file`-ed
  file**, never `_largest_video` (the whole-torrent path still uses
  `_largest_video`, where every file genuinely downloaded).

The selected path is normalized by `_batch_rel_path`: a leading `./` is
stripped, and an absolute path or `..` component is refused, so a malicious
torrent cannot make the download escape its working directory.

### VPS validation strategy

Hermetic tests stub every network boundary, so the real swarm, real ffprobe,
and real aria2 are validated on a small disposable VPS (`docs/TEST_VPS.md`)
through the production `jobs` machinery (a parameterized `vps_drive_run.py`).
Confirmed end-to-end: One Piece 1100 @ 480p and @ 720p (ffprobe 848×480 /
1280×720, `served_quality` matching, job `done`), a real space-list batch
(Montana Jones episode 2 extracted alone, ~126 MiB, valid 640×480), and the
quality-unavailable aggregation (Dandadan 480p → clean quality message).

### Persian subtitles

The UI ships Farsi-first but the release sources are English-subbed, so the
milestone's answer to «زیرنویس» is **generate Persian by translating the
English track**, not wait for a Persian fansub that almost never exists.

**Acquisition.** The two providers hand subtitles over in different shapes:
Nyaa embeds them as streams inside the fansub `.mkv`; HiAnime offers an
external downloaded file (usually VTT despite the `.srt` name). Both are
English sources.

**`subs` is a list.** `AnimeDownloadRequest.subs` and `Track.subs` are a list of
requested languages (`["eng"]`, `["fas"]`, `["eng","fas"]`, or `[]` for none) —
a legacy single string is still accepted by the request validator, and
`"none"` normalizes to `[]`. The frontend exposes exactly four presets:
English, فارسی, English + فارسی, and None. The Farsi-first default is
`["eng", "fas"]`; English is never silently dropped from an explicit choice.

**Translation flow.** Once English is acquired, `subtitle_translate.py`
normalizes it to SRT (`subtitles.py` parses SRT/VTT into cues whose timestamps
are kept verbatim), translates **only the dialogue text** through the existing
keyless Google mechanism in `translate.py`, and rebuilds an SRT with identical
timestamps. A `Translator` protocol is the seam for future LLM/keyed providers;
only the keyless Google one is implemented.

**Cache.** The translated SRT is cached in SQLite
(`data/subtitle_translations.db`) keyed by `sha256(normalized English SRT) +
target language`, so a changed English subtitle produces a fresh translation and
a re-download of the same episode never re-translates. A cache miss on a repeat
episode is a hit.

**Muxing.** Only the languages explicitly requested are muxed (D2): `["eng"]`
keeps the legacy single-track path untouched; `["fas"]` muxes only the
translated Persian (English used internally as the source); `["eng","fas"]`
muxes both. The original English stream is always kept intact when present.
Nyaa extracts the embedded English to a temp SRT only when Persian is
requested; HiAnime normalizes its downloaded file through the same pipeline.

**Failure semantics.** A translation failure never fails the download: the job
completes with the available English subtitle, or a bare video when there is no
English at all.

**Validation.** The real ffmpeg mux + real Google translation were verified in
the Docker `api` container (a fansub-shaped mkv with a titled English sub:
`["eng","fas"]` → two mov_text tracks `eng`/`fas`, `["fas"]` → one `fas` track,
`["eng"]` → one `eng` track, and the extracted Persian text confirmed). A real
VPS download confirmed the English-only path muxes a `subtitle,eng` stream; the
Persian VPS runs were blocked by the swarm stalling on the final ~0.2% of a
torrent (transient, not a code failure) — the Docker test covers the identical
code path. Two real bugs surfaced during that validation and were fixed:
ffmpeg's `-map 0:s:N` wants the **per-type** subtitle index (ffprobe reports the
global one), and real fansub language tags carry a title (`2,eng,English`) that
a naive `eng` match silently failed on.

### Known limitations

- Non-zero-padded episode lists (`1 2 3`) and `S01E01 S01E02` forms are not
  detected as batches (conservative by design — a false negative is cheap).
- The libtorrent batch path is not live-verified and may share the
  preallocation hazard; only the aria2 path is.
- HiAnime is bot-blocked from datacenter IPs and can't be live-tested there; its
  strictness relies on the yt-dlp format selector erroring.

<a id="farsi-only"></a>

## Farsi first, with a language layer

The frontend targets Persian speakers, and Farsi is still the default — `UNSTREAM_DEFAULT_LOCALE` decides what a first-time visitor gets, and unset means `fa`. What changed is that the copy is no longer welded into the components: each language is one dictionary under `frontend/src/lib/locales/`, and a picker switches between them at run time, direction included.

That picker lives beside the other two preferences — in the header when there is room for them, and behind one button in a settings sheet when there is not. Three labelled chip strips do not fit next to the wordmark on a phone, and a flex row will not shrink below its content, so leaving them there gave the whole document a horizontal scrollbar.

This reverses an earlier decision ("no i18n framework, no strings file, no language switcher", on the grounds that a toggle doubles the copy surface for a single-audience app). The doubling is real; what bought it back is that the _dictionary is the type_. `Messages` is derived from `locales/en.ts`, so a language that omits a key — or gets a counting function's arity wrong — fails `tsc`, not the page. There is no framework and no dependency: a context, a plain object, and functions where a string needs a number.

- **Adding a language** is two edits: copy `locales/en.ts`, translate it, and add a line to `LOCALES` in `lib/i18n.tsx`. Nothing else in the app enumerates locales.
- **Direction comes from the locale; digits do not.** `dir` drives `<html dir>` and everything logical (`ps-`/`pe-`/`ms-`/`start-`/`end-`) follows it. Digits are decided per string instead — see [the digit rule](#digits) — because content and chrome mix inside one list.
- **Plural rules live in the locale.** Persian does not agree a noun after a numeral and English does, so counted phrases are functions (`trackCount(n)`) rather than one shared pluraliser that has to know about every language.
- **Copy that wraps an element** is split into `…Before` / `…After` halves. Word order around a `<kbd>` or a highlighted query is the translator's problem, not the component's.
- The register for Farsi is informal-but-clean spoken Persian (محاوره‌ی مرتب): spoken-register verbs, English loanwords in Persian script where they are the natural word («دانلود»، «پلی‌لیست»), no slang. Error and destructive-action copy is one notch calmer than the rest.
- **The hero `h1` and all `<head>` metadata** (title, meta description, OG/Twitter, JSON-LD, manifest) use neutral _written_ Persian instead, because those surfaces target organic search and Persian search queries are written-register («دانلود موزیک», not «موزیک دانلود کن»). Everything rendered in-app below the h1 stays محاوره. The head is re-applied from the active dictionary on every switch (`applyDocumentLocale`), so a shared link carries the language its sharer was reading; a crawler that does not run JavaScript still sees `index.html`'s static copy, which is the default language's.
- The brand is **«آنستریم»** in Farsi copy and **"Unstream"** in English — both live in `app.name`, so no component spells either.
- **Backend `detail` strings are rewritten client-side**, per locale, in `apiError` (`frontend/src/lib/api.ts`). The wire stays terse English; each dictionary supplies the sentence. **A new error string in the backend needs an entry there**, or callers fall through to the generic per-status message.
- Backend-composed result _subtitles_ ("5 releases", "by X · 40 tracks") go through each locale's `subtitle(part)` at **render** time, not when the response lands — that is what lets a language switch re-translate results already on screen without refetching. **New subtitle phrases added in the backend must be added to those maps**, or they reach the UI in English.
- The `/admin` dashboard is English and LTR regardless of the picker. It governs the product, and that page is not the product: it is owner-facing and its screenshots are meant to travel.

`CONTEXT.md` fixes the canonical Farsi rendering of each domain term. Copy changes should agree with it.

<a id="typeface"></a>

## One self-hosted typeface per script

One typeface per script, self-hosted, serving both `--font-display` and `--font-sans`; heading contrast comes from weight, not family. Self-hosting — preload, `font-display: swap`, a metric-tuned fallback — exists to make font loading effectively instant, so reintroducing any third-party font request defeats the point.

This was "one typeface" full stop until the interface got an English version. Vazirmatn's Latin is competent and was never the problem; what changed is that English stopped being incidental text inside a Persian interface and became an interface of its own, at 11–15px, where a Latin face designed for that job is visibly better. **The rule that did not change is one per script.** A second Latin face, or a display face layered on top, is still out.

Both cuts of Vazirmatn ship. Which one an element gets is decided by [the digit rule](#digits), not by the document's language.

### Which face, and when

`<html lang>` decides, and `applyDocumentLocale` already rewrites it when the language changes — so the typeface follows the picker with no class to keep in sync.

- **`lang="fa"`** — Vazirmatn throughout, exactly as before.
- **`lang="en"`** — `Inter, Vazirmatn, …`.

Vazirmatn stays behind Inter in the English stack, and it is load-bearing rather than defensive. **This app mixes scripts on every screen**: an English interface listing Persian track titles is the normal case, not the edge. Inter has no Persian glyphs, so those titles fall through per character, automatically, with no markup deciding anything. Vazirmatn is also what renders English in the moment before Inter arrives — a real, already-preloaded font instead of whatever the OS would have chosen.

**Inter** ([SIL OFL 1.1](https://github.com/rsms/inter), by Rasmus Andersson) was picked for the mixing, not in spite of it. It shares Vazirmatn's neo-grotesque proportions and near enough its x-height, so a Persian title sitting next to English chrome reads as one page rather than two. It is also engineered for exactly the sizes this UI is made of, and has the tabular numerals `QualityPicker` already asks for. A face with more personality would have been a face fighting Vazirmatn on every row.

- Inter ships as **one variable file per subset, 400–700**. Unlike Vazirmatn, a new weight class in the Latin face costs nothing and needs no second download.
- Two subsets, latin and latin-ext, split by `unicode-range` — a page of English never fetches the Latin-Extended cut, and a title with a Turkish or Polish letter pulls the one file that has it. **Those ranges are Google's own and must not be hand-edited**, or the CSS promises glyphs the file does not contain.
- **Inter is not preloaded and not in the service worker's `SHELL`.** Farsi is the default language, so preloading Inter would cost every default visitor a font they will not use, to spare a swap for a visitor who has chosen otherwise. It is fetched on demand and cached like any other asset under `/fonts/`. The swap it does cost lands on Vazirmatn's Latin rather than a system font, which is a narrower jump than the usual one.
- **`--font-fa` is not touched by any of this.** It is the Farsi-digit cut for Persian _content_, which is Persian whatever language the interface is in.
- The English fallback pair is **not** metric-tuned the way the Persian one is; `size-adjust` for Inter against Vazirmatn's Latin has not been measured. English is not the default language, so the shift is bounded to visitors who have switched, on their first load only — but it is a real gap, and the numbers are the same fontTools measurement the Persian fallback already documents.

Each font's licence travels with it, as the OFL requires: `OFL-Vazirmatn.txt` and `OFL-Inter.txt`.

### The Persian face

The font is **Vazirmatn** ([SIL OFL 1.1](https://github.com/rastikerdar/vazirmatn), by Saber Rastikerdar), in its plain and FD cuts. It replaced Peyda FaNum, which is a commercial font from fontiran.com — survivable while the repo was private, a licensing problem the moment open-sourcing came up, since publishing would have handed a paid font to everyone who cloned it. Estedad was the other candidate and lost on mechanics: its releases ship no FD build, only a generator script.

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
2. **Provider content** — track titles, artist and album names, a pasted query — gets `faNumerals(text)`, which returns the `font-fa` class when the _text itself_ is Persian script. A Persian title's digits should be Persian and an English one's should not, and the two sit in the same list, so the string decides. This is the same reasoning as the `dir="auto"` on those nodes, and it belongs on exactly those nodes.
3. **Everything else** inherits the plain cut and is left alone.

This replaces an earlier arrangement where the FD cut was the document default and rewrote every digit on the page. That was one mechanism doing all three jobs, and it could not tell a count from a title: it is why "24K Magic" rendered as "۲۴K Magic". Note what it means for a _new_ surface — a number you interpolate straight into JSX will come out in Latin digits in both languages. That is a bug in Farsi; route it through `m.app.num`.

<a id="analytics"></a>

## Analytics, recorded server-side

Unstream counts its own usage into a SQLite file on the `analytics` volume, read back through a token-gated `/admin` page in the existing React app. Plausible, Umami and GA were all rejected: a hosted one costs money or an account, a self-hosted one is a second container and a second database for a project whose premise is that it needs neither.

Almost every event is recorded **server-side**, inside endpoints that already run. Only page views and a couple of browser-only moments come from a `sendBeacon`. So the numbers are not something an ad blocker can subtract, and the page pays nothing to collect them.

- **No cookie, no raw IP, no consent banner.** A caller is `sha256(salt + ip + user-agent + day)`, truncated, with a random per-install salt in the database. The rotation is the point: it gives daily uniques and makes "returning visitor" **impossible to measure**. That metric is not missing by accident — do not add a durable id to get it back.
- **Search queries are stored as text**, deliberately: a top-searched leaderboard is the most shareable number the project has. Queries are only ever joined to that day's hash. If it stops feeling proportionate, drop the `label` on `search` events and the leaderboard goes with it.
- Writes go through a bounded queue drained by one thread and are **dropped when it is full**. Analytics can lose events; it can never slow down or fail a download. Every `record()` swallows its own errors, and an unwritable volume disables the subsystem rather than breaking startup.
- **`ADMIN_TOKEN` is the project's only secret.** Unset, `/api/admin/*` returns 503 and the dashboard does not exist — it cannot accidentally end up public. Failed token attempts are rate-limited and that guard is not configurable.
- Rows are kept `ANALYTICS_RETENTION_DAYS` (90), swept hourly by the same writer thread. Counters live in one process, exactly like `limits.py` — fine for the single container, a rethink if the API is ever scaled out.
- The **`analytics` volume is the only copy of the history**. Unlike `downloads` it is not disposable; losing it loses every number the project has had. The README has the SQLite-safe backup command. `lyrics.db` shares the volume and _is_ disposable — it is a cache, and throwing it away costs one slow afternoon, not any history.

<a id="self-hosting"></a>

## Run it yourself

YouTube treats a datacenter address differently from a home one. From a VPS it answers `LOGIN_REQUIRED` at the playability check — before a proof-of-origin token is asked for and before a JS challenge exists to solve — so the defences the image carries cannot reach the point where they would help. Signing in with cookies moves yt-dlp onto the web clients where those defences _do_ matter, which is why all three are needed together on a server and none are needed on a laptop.

That asymmetry is not a bug to fix here. It is why the same code works instantly at home and fails on a rented box, and it makes "run it yourself" the configuration where the project is at its best: no bot checks, no shared account carrying everyone's downloads, no operator in between. The legal shape agrees — distributing the software puts each person in charge of what they download, which is the position yt-dlp, spotDL and MeTube occupy.

Keeping a public instance working needs egress from a non-datacenter address: a residential or ISP proxy, which costs money. That is planned, not ruled out. It changes no code — `compose.dokploy.yml` already keeps the per-caller limits hardcoded on, and those limits stop being decorative the moment a public instance can actually serve people. The keyless rule is about _metadata providers_; renting an IP does not touch it.

Rejected along the way: a **split architecture** with the frontend on a VPS and a download worker at home — it gets a residential IP free and is strictly worse, since every stranger's download then traces to one home ISP account. And a **SoundCloud-only public demo**, which would work forever but advertises a downloader that mostly cannot download.

Consequences that outlive the decision:

- `docker-compose.yml` must work on a fresh clone with no `.env`, no external network and no file mounts. `compose.dokploy.yml` is the deployment. A change to one is not automatically right for the other.
- Defaults differ by audience on purpose: the **code's** defaults are public-safe (downloads expire, disk capped, limits tight) and the self-hosting compose file overrides them toward "this is my machine". Anything new with a limit attached needs a decision on both sides.
- The bot-check apparatus — deno, the challenge solver, the PO token provider — stays in the image even though most self-hosters never need it. It costs nothing idle, and the day YouTube starts asking a home address for a token is not a day to spend reading documentation.
