import axios from 'axios'

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

export type TrackStatus =
  'queued' | 'searching' | 'downloading' | 'tagging' | 'retrying' | 'done' | 'error'

export interface JobTrack {
  id: string
  status: TrackStatus
  progress: number
  error: string | null
  /** Format the finished file actually came out as ('mp3' | 'm4a' | 'opus'). */
  ext: string | null
}

export interface Job {
  id: string
  name: string
  quality: Quality
  tracks: JobTrack[]
  done: number
  failed: number
  total: number
  finished: boolean
}

/** Audio the user can ask for: an mp3 bitrate in kbps, or the upload's own
 *  stream untouched. Mirrors QUALITIES in backend/app/downloader.py. */
export const QUALITIES = ['128', '192', '320', 'original'] as const

export type Quality = (typeof QUALITIES)[number]

export const DEFAULT_QUALITY: Quality = '192'

export const QUALITY_LABEL: Record<Quality, string> = {
  '128': '128',
  '192': '192',
  '320': '320',
  original: 'اورجینال',
}

export const QUALITY_HINT: Record<Quality, string> = {
  '128': 'کم‌حجم‌ترین حالت — برای پادکست یا گوشی‌ای که جاش پره کافیه.',
  '192': 'پیش‌فرض. کیفیت خوب با تقریباً نصف حجم ۳۲۰.',
  '320': 'بهترین حالتی که mp3 داره. همه‌جا هم پخش میشه.',
  original:
    'بدون انکود دوباره — همون m4a یا opus خود آپلود. بهترین صدا، بیشترین حجم، و روی بعضی دستگاه‌های قدیمی پخش نمیشه.',
}

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

const client = axios.create({ baseURL: '/api' })

/** The backend composes result subtitles in English ("5 releases",
 *  "by X · 40 tracks"); the UI is Farsi-only (docs/adr/0001), so the known
 *  phrases are rewritten here at the API seam rather than forking the
 *  backend's response for one client. Unmatched parts (artist names, years)
 *  pass through untouched. */
function localizeSubtitle(subtitle: string): string {
  return subtitle
    .split(' · ')
    .map((part) =>
      part
        .replace(/^(\d+) releases?$/, '$1 اثر')
        .replace(/^(\d+) tracks?$/, '$1 آهنگ')
        .replace(/^(\d+) followers?$/, '$1 فالوور')
        .replace(/^by (.+)$/, 'از $1')
        .replace(/^Artist$/, 'آرتیست')
        .replace(/^On SoundCloud$/, 'تو ساندکلاد')
        .replace(/^SINGLE$/, 'تک‌آهنگ'),
    )
    .join(' · ')
}

const localizeResult = (result: SearchResult): SearchResult => ({
  ...result,
  subtitle: localizeSubtitle(result.subtitle),
})

/** Backend `detail` strings, translated at the same seam as result subtitles.
 *
 *  The wire format stays English — it is shared with the Telegram bot and with
 *  anyone poking at the API — and each surface renders it in its own voice
 *  (ADR 0001). Entries are ordered most specific first; `$1` carries through
 *  whatever number the backend computed. */
const ERROR_PHRASES: [RegExp, string][] = [
  [/^Too many searches — wait (\d+)s/, 'یه کم تند رفتی — $1 ثانیه صبر کن و دوباره جستجو کن.'],
  [/^Too many links opened — wait (\d+)s/, 'یه کم تند رفتی — $1 ثانیه صبر کن و دوباره امتحان کن.'],
  [
    /^Too many downloads started — wait (\d+)s/,
    'برای امروز به سقف دانلود رسیدی — $1 ثانیه دیگه دوباره امتحان کن.',
  ],
  [/^Too many downloads at once/, 'همزمان چندتا دانلود در جریانه — صبر کن یکیش تموم شه.'],
  [/^Too many tracks — (\d+)/, 'این لیست خیلی بلنده — هر بار حداکثر $1 آهنگ.'],
  [
    /^Unsupported link/,
    'این لینک پشتیبانی نمیشه — لینک اسپاتیفای، دیزر، اپل موزیک، یوتیوب یا ساندکلاد بذار، یا اسمش رو جستجو کن.',
  ],
  [/^No tracks to download/, 'هیچ آهنگی برای دانلود انتخاب نشده.'],
  [/^No completed tracks yet/, 'هنوز هیچ آهنگی آماده نشده.'],
  [/^Track not ready/, 'این فایل هنوز آماده نیست.'],
  [/^Unknown job/, 'این دانلود دیگه روی سرور نیست.'],
  [/^Empty search query/, 'چیزی برای جستجو ننوشتی.'],
]

/** Anything the table didn't catch — dynamic provider and yt-dlp errors are
 *  raw English, which has no place in a Farsi-only UI. */
const STATUS_FALLBACK: Record<number, string> = {
  400: 'این لینک باز نشد — شاید خصوصی باشه یا منبعش در دسترس نباشه.',
  404: 'پیدا نشد.',
  429: 'یه کم تند رفتی — چند لحظه صبر کن.',
}

function localizeError(detail: string, status: number): string {
  for (const [pattern, farsi] of ERROR_PHRASES) {
    // Patterns anchor on the distinctive opening and the Farsi replaces the
    // whole message, so trailing English ("…and try again.") is dropped rather
    // than left hanging off the end of a Persian sentence.
    const match = pattern.exec(detail)
    if (match) return farsi.replace('$1', match[1] ?? '')
  }
  return STATUS_FALLBACK[status] ?? 'سرور جواب نداد — یه بار دیگه امتحان کن.'
}

export function apiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    if (status == null) return 'به سرور وصل نشدیم — اینترنتت رو چک کن.'
    return localizeError(typeof detail === 'string' ? detail : '', status)
  }
  return err instanceof Error ? err.message : 'یه مشکلی پیش اومد'
}

const URL_PATTERNS = [
  /open\.spotify\.com\/(intl-[a-zA-Z-]+\/)?(track|album|playlist)\//,
  /deezer\.com\/([a-z]{2}\/)?(track|album|playlist)\/\d+/,
  /music\.apple\.com\/([a-z]{2}\/)?(album|song)\//,
  /(music\.|www\.|m\.)?(youtube\.com\/(watch|playlist)\?|youtu\.be\/)/,
  /(www\.|m\.|on\.)?soundcloud\.com\/./,
]

export const isCatalogUrl = (input: string) => URL_PATTERNS.some((re) => re.test(input))

export async function searchCatalog(query: string, page = 0): Promise<SearchPage> {
  const { data } = await client.get<SearchPage>('/search', {
    params: { q: query, page },
  })
  return { ...data, results: data.results.map(localizeResult) }
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

export async function getArtist(id: string): Promise<ArtistDetail> {
  const { data } = await client.get<ArtistDetail>(`/artist/${id}`)
  return {
    ...data,
    top_tracks: data.top_tracks.map(localizeResult),
    albums: data.albums.map(localizeResult),
  }
}

export async function resolveUrl(url: string): Promise<Collection> {
  const { data } = await client.post<Collection>('/resolve', { url })
  return data
}

export async function startDownload(
  url: string,
  trackIds?: string[],
  quality: Quality = DEFAULT_QUALITY,
): Promise<string> {
  const { data } = await client.post<{ job_id: string }>('/download', {
    url,
    track_ids: trackIds ?? null,
    quality,
  })
  return data.job_id
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await client.get<Job>(`/jobs/${jobId}`)
  return data
}

/** Poll every unfinished job in one request.
 *
 *  Jobs the server no longer knows — swept after their TTL, or lost to a
 *  restart — are simply absent from the response. Callers use that gap to
 *  retire restored entries that have nothing behind them any more. */
export async function getJobs(jobIds: string[]): Promise<Job[]> {
  if (jobIds.length === 0) return []
  const { data } = await client.get<{ jobs: Job[] }>('/jobs', {
    params: { ids: jobIds.join(',') },
  })
  return data.jobs
}

export const trackFileUrl = (jobId: string, trackId: string) =>
  `/api/jobs/${jobId}/tracks/${trackId}/file`

export const jobZipUrl = (jobId: string) => `/api/jobs/${jobId}/zip`
