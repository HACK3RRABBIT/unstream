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
}

export interface Collection {
  kind: 'track' | 'album' | 'playlist'
  name: string
  owner: string
  cover_url: string | null
  tracks: Track[]
}

export type TrackStatus =
  | 'queued'
  | 'searching'
  | 'downloading'
  | 'tagging'
  | 'retrying'
  | 'done'
  | 'error'

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

export interface SearchResult {
  kind: 'track' | 'album' | 'playlist'
  id: string
  name: string
  subtitle: string
  cover_url: string | null
  url: string
}

const client = axios.create({ baseURL: '/api' })

export function apiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    return err.response?.data?.detail ?? err.message
  }
  return err instanceof Error ? err.message : 'Something went wrong'
}

export const isCatalogUrl = (input: string) =>
  /open\.spotify\.com\/(intl-[a-zA-Z-]+\/)?(track|album|playlist)\/|deezer\.com\/([a-z]{2}\/)?(track|album|playlist)\/\d+/.test(
    input,
  )

export async function searchCatalog(query: string): Promise<SearchResult[]> {
  const { data } = await client.get<{ results: SearchResult[] }>('/search', {
    params: { q: query },
  })
  return data.results
}

export async function resolveUrl(url: string): Promise<Collection> {
  const { data } = await client.post<Collection>('/resolve', { url })
  return data
}

export async function startDownload(
  url: string,
  trackIds?: string[],
): Promise<string> {
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
