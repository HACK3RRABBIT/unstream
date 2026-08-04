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
}

export interface Job {
  id: string
  name: string
  tracks: JobTrack[]
  done: number
  failed: number
  total: number
  finished: boolean
}

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

export function apiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    return err.response?.data?.detail ?? err.message
  }
  return err instanceof Error ? err.message : 'Something went wrong'
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

export async function getArtist(id: string): Promise<ArtistDetail> {
  const { data } = await client.get<ArtistDetail>(`/artist/${id}`)
  return data
}

export async function resolveUrl(url: string): Promise<Collection> {
  const { data } = await client.post<Collection>('/resolve', { url })
  return data
}

export async function startDownload(url: string, trackIds?: string[]): Promise<string> {
  const { data } = await client.post<{ job_id: string }>('/download', {
    url,
    track_ids: trackIds ?? null,
  })
  return data.job_id
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await client.get<Job>(`/jobs/${jobId}`)
  return data
}

export const trackFileUrl = (jobId: string, trackId: string) =>
  `/api/jobs/${jobId}/tracks/${trackId}/file`

export const jobZipUrl = (jobId: string) => `/api/jobs/${jobId}/zip`
