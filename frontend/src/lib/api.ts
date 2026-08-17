import axios from 'axios'
import type { Messages } from './locales/en'

export interface Track {
  id: string
  title: string
  artists: string[]
  album: string
  duration_ms: number
  cover_url: string | null
  track_number: number
  release_date: string
  preview_url: string | null
}

export interface Collection {
  kind: 'track' | 'album' | 'playlist'
  name: string
  owner: string
  cover_url: string | null
  tracks: Track[]
}

/** `cancelled` is terminal like `done` and `error`, and is not a failure: it
 *  means someone stopped this job. Mirrors the statuses in backend/app/jobs.py. */
export type TrackStatus =
  'queued' | 'searching' | 'downloading' | 'tagging' | 'retrying' | 'done' | 'error' | 'cancelled'

/** Anime: live provider-chain search progress while a track's status is
 *  "searching". Mirrors backend TrackState.provider_progress — the source
 *  being checked (null between attempts), which 1-based provider of how many.
 *  Absent for audio tracks and for anime once searching ends. */
export interface ProviderProgress {
  checked: number
  total: number
  current: string | null
}

export interface JobTrack {
  id: string
  status: TrackStatus
  progress: number
  error: string | null
  /** Format the finished file actually came out as ('mp3' | 'm4a' | 'opus'). */
  ext: string | null
  /** Anime only, while searching. Real backend progress, not a timer. */
  provider_progress?: ProviderProgress | null
}

export interface Job {
  id: string
  name: string
  quality: Quality
  tracks: JobTrack[]
  done: number
  failed: number
  /** Tracks stopped by a cancel. Counts toward `finished` without counting as
   *  a failure, so a cancelled job is not reported as a broken one. */
  cancelled: number
  total: number
  finished: boolean
}

/** Audio the user can ask for: an mp3 bitrate in kbps, or the upload's own
 *  stream untouched. Mirrors QUALITIES in backend/app/downloader.py. */
export const QUALITIES = ['128', '192', '320', 'original'] as const

export type Quality = (typeof QUALITIES)[number]

export const DEFAULT_QUALITY: Quality = '192'

/** "original" is a word and comes from the dictionary; the bitrates are a
 *  quantity the UI is stating, so they get the locale's digits. (File
 *  extensions — mp3, m4a, opus — are names, not quantities, and stay as they
 *  came.) */
export const qualityLabel = (quality: Quality, m: Messages): string =>
  quality === 'original' ? m.quality.original : m.app.num(quality)

export const isQuality = (value: unknown): value is Quality => QUALITIES.includes(value as Quality)

export type Source = 'deezer' | 'itunes' | 'youtube' | 'soundcloud'

export type ResultKind = 'track' | 'album' | 'artist' | 'playlist'

export interface SearchResult {
  kind: ResultKind
  id: string
  name: string
  subtitle: string
  cover_url: string | null
  url: string
  source: Source
  /** Server-computed identity (kind + name + artist). The backend dedupes
   *  within a page; the client reuses this key to dedupe across pages. */
  dedup_key: string
}

export interface SearchPage {
  results: SearchResult[]
  page: number
  has_more: boolean
}

export interface ArtistDetail {
  id: string
  name: string
  picture_url: string | null
  fan_count: number | null
  top_tracks: SearchResult[]
  albums: SearchResult[]
}

/** Lyrics for a track. `synced` (time-stamped LRC) is fetched and cached but
 *  not rendered in v1 — it's a future karaoke view waiting for a client.
 *
 *  `status` says which kind of nothing a null `plain` is. `absent` means every
 *  source answered and none has this song; `unavailable` means they could not
 *  be reached. Rendering those the same way is what let a blocked source read
 *  as "this song has no lyrics" — only one of the two is worth a retry. */
export interface Lyrics {
  status: 'found' | 'absent' | 'unavailable'
  plain: string | null
  synced: string | null
  source: string | null
}

const client = axios.create({ baseURL: '/api' })

/** Backend `detail` strings, matched to a dictionary entry. The wire carries a
 *  terse machine-facing message; each surface renders it in its own voice and
 *  its own language (docs/DESIGN.md). The capture group, when there is one,
 *  carries the backend's number through to the phrase. */
const ERROR_PHRASES: [RegExp, (m: Messages, n: string) => string][] = [
  [/^Too many searches — wait (\d+)s/, (m, n) => m.errors.tooManySearches(n)],
  [/^Too many links opened — wait (\d+)s/, (m, n) => m.errors.tooManyLinks(n)],
  [/^Too many downloads started — wait (\d+)s/, (m, n) => m.errors.tooManyDownloads(n)],
  [/^Too many downloads at once/, (m) => m.errors.downloadsAtOnce],
  [/^Too many tracks — (\d+)/, (m, n) => m.errors.tooManyTracks(n)],
  [/^Unsupported link/, (m) => m.errors.unsupportedLink],
  [/^No tracks to download/, (m) => m.errors.noTracksSelected],
  [/^No completed tracks yet/, (m) => m.errors.nothingFinished],
  [/^Track not ready/, (m) => m.errors.notReady],
  [/^Unknown job/, (m) => m.errors.unknownJob],
  [/^Empty search query/, (m) => m.errors.emptyQuery],
]

/** For anything the table missed: provider and yt-dlp errors are raw internals,
 *  which have no place in the UI in any language. */
function statusFallback(status: number, m: Messages): string {
  if (status === 400) return m.errors.badRequest
  if (status === 404) return m.errors.notFound
  if (status === 429) return m.errors.rateLimited
  return m.errors.noAnswer
}

/** Turns a thrown request into a sentence. Takes the dictionary rather than
 *  reaching for one, so it stays callable outside a React tree. */
export function apiError(err: unknown, m: Messages): string {
  if (axios.isAxiosError(err)) {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    if (status == null) return m.errors.offline
    const text = typeof detail === 'string' ? detail : ''
    for (const [pattern, phrase] of ERROR_PHRASES) {
      // Patterns anchor on the opening and the phrase covers the whole
      // message, so no raw backend tail hangs off the end of a sentence.
      const match = pattern.exec(text)
      if (match) return phrase(m, match[1] ?? '')
    }
    return statusFallback(status, m)
  }
  return err instanceof Error ? err.message : m.errors.unknown
}

const URL_PATTERNS = [
  /open\.spotify\.com\/(intl-[a-zA-Z-]+\/)?(track|album|playlist)\//,
  /deezer\.com\/([a-z]{2}\/)?(track|album|playlist)\/\d+/,
  /music\.apple\.com\/([a-z]{2}\/)?(album|song)\//,
  /(music\.|www\.|m\.)?(youtube\.com\/(watch|playlist)\?|youtu\.be\/)/,
  /(www\.|m\.|on\.)?soundcloud\.com\/./,
]

export const isCatalogUrl = (input: string) => URL_PATTERNS.some((re) => re.test(input))

/** True when a request ended because we aborted it, not because it failed.
 *  Everything that takes a `signal` below can end this way, and a cancelled
 *  request has no error to report — the user is the one who stopped it. */
export const isCanceled = (err: unknown): boolean => axios.isCancel(err)

export async function searchCatalog(
  query: string,
  page = 0,
  signal?: AbortSignal,
): Promise<SearchPage> {
  const { data } = await client.get<SearchPage>('/search', {
    params: { q: query, page },
    signal,
  })
  return data
}

/** Append a page, dropping anything already on screen. */
export function mergeResults(current: SearchResult[], incoming: SearchResult[]): SearchResult[] {
  const seen = new Set(current.map((r) => r.dedup_key))
  const fresh: SearchResult[] = []
  for (const result of incoming) {
    if (seen.has(result.dedup_key)) continue
    seen.add(result.dedup_key)
    fresh.push(result)
  }
  return fresh.length > 0 ? [...current, ...fresh] : current
}

export async function getArtist(id: string, signal?: AbortSignal): Promise<ArtistDetail> {
  const { data } = await client.get<ArtistDetail>(`/artist/${id}`, { signal })
  return data
}

export async function resolveUrl(url: string, signal?: AbortSignal): Promise<Collection> {
  const { data } = await client.post<Collection>('/resolve', { url }, { signal })
  return data
}

export async function startDownload(
  url: string,
  trackIds?: string[],
  quality: Quality = DEFAULT_QUALITY,
  lyrics: boolean = true,
): Promise<string> {
  const { data } = await client.post<{ job_id: string }>('/download', {
    url,
    track_ids: trackIds ?? null,
    quality,
    lyrics,
  })
  return data.job_id
}

/** Lyrics for a track, keyed by its catalog metadata. Always 200: nulls mean
 *  "no lyrics", and the UI renders that as its own state rather than an error.
 *
 *  `refresh` is for the retry button. The backend caches an outage for a few
 *  minutes so an album download doesn't re-pay the lookup on every track, and
 *  this is the flag that says "a person asked, go and look again anyway". */
export async function getLyrics(track: Track, refresh = false): Promise<Lyrics> {
  const { data } = await client.get<Lyrics>('/lyrics', {
    params: {
      artist: track.artists.join(', '),
      title: track.title,
      album: track.album,
      duration_ms: track.duration_ms,
      ...(refresh ? { refresh: 1 } : {}),
    },
  })
  return data
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await client.get<Job>(`/jobs/${jobId}`)
  return data
}

/** Poll every unfinished job in one request. Jobs the server no longer knows
 *  are absent from the response — callers use that gap to retire them. */
export async function getJobs(jobIds: string[]): Promise<Job[]> {
  if (jobIds.length === 0) return []
  const { data } = await client.get<{ jobs: Job[] }>('/jobs', {
    params: { ids: jobIds.join(',') },
  })
  return data.jobs
}

/** Stop a running job. Answers with the job's new state — every unfinished
 *  track already `cancelled` — so the dock doesn't have to wait for the next
 *  poll to stop claiming the download is still going. */
export async function cancelJob(jobId: string): Promise<Job> {
  const { data } = await client.post<Job>(`/jobs/${jobId}/cancel`)
  return data
}

export const trackFileUrl = (jobId: string, trackId: string) =>
  `/api/jobs/${jobId}/tracks/${encodeURIComponent(trackId)}/file`

export const jobZipUrl = (jobId: string) => `/api/jobs/${jobId}/zip`

// ---------------------------------------------------------------------------
// Anime — a second catalog searched through AniList (keyless GraphQL), with
// seasons grouped into a franchise. Video resolution is a *different* quality
// axis from audio bitrate, so it gets its own constant, type and storage key.

/** Resolutions the anime section asks a video provider for, plus "original" —
 *  the provider's own untouched stream, like the audio section's `original`.
 *  360p is not offered — anime torrents are released at 480p and up, so a 360
 *  option would only ever fail to match. Independent of the audio `QUALITIES`
 *  above; a 720p episode is not the same choice as a 192 kbps mp3. */
export const VIDEO_QUALITIES = ['480', '720', '1080', 'original'] as const

export type VideoQuality = (typeof VIDEO_QUALITIES)[number]

export const DEFAULT_VIDEO_QUALITY: VideoQuality = 'original'

export const videoQualityLabel = (quality: VideoQuality, m: Messages): string =>
  quality === 'original' ? m.anime.quality.original : m.app.num(quality)

export const isVideoQuality = (value: unknown): value is VideoQuality =>
  VIDEO_QUALITIES.includes(value as VideoQuality)

/** One anime in a search result — the card the Anime tab renders.
 *  `season_count` is how many seasons the franchise has (series only), and
 *  `description` is AniList's English synopsis, shown under the title.
 *  `episodes` is the planned total; `available_episodes` is what exists now —
 *  for a RELEASING show they differ (12 planned, 6 aired). */
export interface AnimeSearchResult {
  id: number
  title: string
  format: string
  episodes: number
  available_episodes: number
  year: number | null
  status: string
  cover_url: string | null
  description: string | null
  season_count: number
}

/** Search results split so series are never confused with movies. */
export interface AnimeSearchPage {
  series: AnimeSearchResult[]
  movies: AnimeSearchResult[]
}

/** One season of a franchise — a single AniList Media entry.
 *  `episodes` is the planned total; `available_episodes` what exists now. */
export interface AnimeSeason {
  season: number
  media_id: number
  title: string
  year: number | null
  episodes: number
  available_episodes: number
  status: string
  cover_url: string | null
}

/** A franchise the user opened: the seed anime plus its ordered seasons. */
export interface AnimeDetail {
  id: number
  title: string
  cover_url: string | null
  description: string | null
  seasons: AnimeSeason[]
}

/** Translate an anime synopsis to `to` (default fa). Keyless, cached, and
 *  returns the original text when the translation service is unreachable. */
export async function translateAnime(
  text: string,
  to = 'fa',
  signal?: AbortSignal,
): Promise<string> {
  const { data } = await client.get<{ text: string }>('/anime/translate', {
    params: { text, to },
    signal,
  })
  return data.text
}

/** Search AniList for anime by name — returns series (grouped into
 *  franchises) and movies, kept apart so the two are never mixed. */
export async function searchAnime(query: string, signal?: AbortSignal): Promise<AnimeSearchPage> {
  const { data } = await client.get<AnimeSearchPage>('/anime/search', {
    params: { q: query },
    signal,
  })
  return data
}

/** A franchise: the seed anime plus its ordered seasons. */
export async function getAnime(id: number, signal?: AbortSignal): Promise<AnimeDetail> {
  const { data } = await client.get<AnimeDetail>(`/anime/${id}`, { signal })
  return data
}

/** What one configured source is verified to serve for a season. Mirrors the
 *  backend's /sources response: `qualities` is a list of resolutions the
 *  source is *verified* to hold ("480"/"720"/...), or null when it wasn't
 *  probed (Nyaa/hianime) — null must never be read as "absent". */
export interface AnimeSource {
  name: string
  status: 'ok' | 'unavailable' | 'unknown'
  qualities: string[] | null
  note: string | null
}

/** Per-provider capability for one season — the source of truth the quality
 *  picker renders from. A quality may only be hidden when every reporting
 *  source authoritatively lacks it; an unknown/null source keeps it shown. */
export interface AnimeSources {
  media_id: number
  season: number
  providers: AnimeSource[]
}

/** Fetch per-source capability for a season, from the backend's probe. */
export async function getAnimeSources(
  animeId: number,
  season: number,
  signal?: AbortSignal,
): Promise<AnimeSources> {
  const { data } = await client.get<AnimeSources>(`/anime/${animeId}/season/${season}/sources`, {
    signal,
  })
  return data
}

/** Subtitle languages an anime episode can mux in. The user's selection is a
 *  list — ["eng"], ["fas"], ["eng","fas"], or [] (none). "none" is kept as a
 *  legacy single value; the picker's None preset sends []. */
export const SUBTITLE_LANGUAGES = ['eng', 'fas', 'none'] as const

export type SubtitleLanguage = (typeof SUBTITLE_LANGUAGES)[number]

/** Default subtitle selection for the Persian-first UI: English + Persian.
 *  English is never dropped from an explicit selection, only from this default
 *  choice of what a fresh user gets. */
export const DEFAULT_SUBTITLE_LANGUAGES: SubtitleLanguage[] = ['eng', 'fas']

export const isSubtitleLanguage = (value: unknown): value is SubtitleLanguage =>
  SUBTITLE_LANGUAGES.includes(value as SubtitleLanguage)

export const isSubtitleLanguages = (value: unknown): value is SubtitleLanguage[] =>
  Array.isArray(value) && value.every(isSubtitleLanguage)

/** Queue a season's episodes as a download job. `episodeIds` selects a
 *  subset; omitted = the whole season. Quality is the header's global video
 *  quality; `subs` is the list of subtitle languages to mux in. */
export async function startAnimeDownload(
  animeId: number,
  season: number,
  quality: VideoQuality = DEFAULT_VIDEO_QUALITY,
  subs: SubtitleLanguage[] = DEFAULT_SUBTITLE_LANGUAGES,
  episodeIds?: string[],
): Promise<string> {
  const { data } = await client.post<{ job_id: string }>('/anime/download', {
    media_id: animeId,
    season,
    quality,
    subs,
    ...(episodeIds && episodeIds.length > 0 ? { episode_ids: episodeIds } : {}),
  })
  return data.job_id
}
