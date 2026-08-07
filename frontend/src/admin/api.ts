import axios from 'axios'

export interface Slice {
  key: string
  count: number
}

export interface DayRow {
  day: string
  page_views: number
  visitors: number
  searches: number
  downloads: number
  tracks_done: number
  tracks_failed: number
}

export interface Totals {
  page_views: number
  visitors: number
  searches: number
  empty_searches: number
  downloads: number
  tracks_done: number
  tracks_failed: number
  tracks_delivered: number
  zips: number
  rate_limited: number
  /** Files that actually left the server — the honest download count. */
  files_saved: number
  shares: number
  artist_views: number
  link_errors: number
  installs: number
  install_prompts: number
  /** 0–1, or null before any track has finished either way. */
  success_rate: number | null
  median_track_seconds: number | null
}

export interface Breakdowns {
  top_searches: Slice[]
  top_tracks: Slice[]
  top_artists: Slice[]
  quality: Slice[]
  link_providers: Slice[]
  audio_sources: Slice[]
  errors: Slice[]
  referrers: Slice[]
  devices: Slice[]
  surfaces: Slice[]
  limits_hit: Slice[]
  link_errors: Slice[]
  artists_browsed: Slice[]
}

export interface Stats {
  enabled: boolean
  days: number
  from: string
  to: string
  totals: Totals
  series: DayRow[]
  breakdowns: Breakdowns
  live: { active_jobs: number; active_tracks: number; jobs_tracked: number }
}

const TOKEN_KEY = 'unstream:admin-token'

export const readToken = () => localStorage.getItem(TOKEN_KEY) ?? ''
export const writeToken = (token: string) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export class AuthError extends Error {}

async function get<T>(path: string, token: string): Promise<T> {
  try {
    const { data } = await axios.get<T>(path, {
      headers: { Authorization: `Bearer ${token}` },
    })
    return data
  } catch (err) {
    if (axios.isAxiosError(err)) {
      const status = err.response?.status
      if (status === 401) throw new AuthError('That token was not accepted.')
      if (status === 429) throw new AuthError('Too many bad tokens. Wait a few minutes.')
      if (status === 503)
        throw new AuthError('Analytics is off — set ADMIN_TOKEN on the api service.')
      throw new Error((err.response?.data as { detail?: string })?.detail ?? err.message)
    }
    throw err
  }
}

export const fetchStats = (token: string, days: number) =>
  get<Stats>(`/api/admin/stats?days=${days}`, token)

export interface RawEvent {
  ts: number
  name: string
  surface: string
  visitor: string | null
  source: string | null
  detail: string | null
  label: string | null
  value: number | null
  ms: number | null
}

export const fetchEvents = (token: string, limit = 200) =>
  get<{ events: RawEvent[] }>(`/api/admin/events?limit=${limit}`, token)
